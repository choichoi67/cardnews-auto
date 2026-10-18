import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from cardnews_auto import build_progress, dashboard_jobs, database, database_schema, telegram_bot, render
from cardnews_auto.models import CardNews, Slide


def job(job_id, channel="hiphop"):
    return dict(id=job_id, profile_id=channel, profile_name=channel, action="build", status="queued",
                created_at="2026-10-13T00:00:00+00:00", started_at=None, finished_at=None, return_code=None, log="")


def test_atomic_fifo_claim_allows_two_running_builds(tmp_path):
    path = tmp_path / "jobs.db"
    for name, channel in (("z-first", "hiphop"), ("a-second", "football"), ("m-third", "hiphop")):
        database.add_job(job(name, channel), path)
    assert database.build_queue_position("z-first", path) == (1, 3)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: database.claim_next_build("z-first", path), range(8)))
    assert sum(claims) == 1
    assert not database.claim_next_build("m-third", path)  # FIFO: the second job goes first
    assert database.claim_next_build("a-second", path)
    assert not database.claim_next_build("m-third", path)  # two already running
    database.update_job("z-first", {"status": "failed"}, path)
    assert database.claim_next_build("m-third", path)


def test_progress_edits_one_message_and_preserves_failed_stage(monkeypatch):
    calls = []
    class Client:
        chat_id = "test"
        def call(self, method, payload, **kwargs):
            calls.append((method, payload))
            return {"message_id": 42}
    monkeypatch.setattr(telegram_bot, "client_from_env", Client)
    monkeypatch.setattr(build_progress.time, "sleep", lambda _: None)
    tracker = build_progress.BuildProgress("뉴스", "12345678xx", True)
    tracker.send("대기", force=True)
    tracker.event(dict(stage="원고 작성", status="running"))
    tracker.event(dict(stage="원고 작성", status="completed"))
    tracker.event(dict(stage="이미지 생성", status="running", detail="1/3번 카드"))
    tracker.send("실패", "3:4 사진으로 교체하세요", force=True)
    assert [method for method, _ in calls] == ["sendMessage"] + ["editMessageText"] * 4
    assert all(payload["message_id"] == 42 for _, payload in calls[1:])
    assert "[ 완료 ] 원고 작성" in calls[-1][1]["text"]
    assert "[ 실패 ] 이미지 생성" in calls[-1][1]["text"]
    assert "[ 대기 ] 카드 출력" in calls[-1][1]["text"]


def test_notification_failure_does_not_fail_build(monkeypatch):
    def unavailable():
        raise RuntimeError("network down")
    monkeypatch.setattr(telegram_bot, "client_from_env", unavailable)
    tracker = build_progress.BuildProgress("뉴스", "job", True)
    tracker.event(dict(stage="원고 작성", status="running"))
    assert tracker.stages["원고 작성"] == "작업중"


@pytest.mark.parametrize("return_code", [0, 1])
def test_collection_sends_start_progress_and_final_status(monkeypatch, tmp_path, return_code):
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(dashboard_jobs, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard_jobs, "profile_config", lambda _: {"telegram": {"enabled": True}})
    monkeypatch.setattr(dashboard_jobs, "build_snapshot", lambda _: {})
    monkeypatch.setattr(dashboard_jobs, "_env_values", lambda _: {"TELEGRAM_BOT_TOKEN": "saved-token", "TELEGRAM_CHAT_ID": "saved-chat"})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "stale-token")
    monkeypatch.setattr(build_progress.time, "sleep", lambda _: None)
    calls = []
    class Client:
        chat_id = "saved-chat"
        def call(self, method, payload, **kwargs):
            calls.append((method, payload))
            return {"message_id": 42}
    def client(*, environ):
        assert environ["TELEGRAM_BOT_TOKEN"] == "saved-token"
        assert environ["TELEGRAM_CHAT_ID"] == "saved-chat"
        return Client()
    monkeypatch.setattr(telegram_bot, "client_from_env", client)
    class Process:
        def __init__(self, command, **kwargs):
            assert kwargs["env"]["TELEGRAM_BOT_TOKEN"] == "saved-token"
            self.stdout = [build_progress.PREFIX + json.dumps({"stage": "기사 수집", "status": "running"}) + "\n"]
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def wait(self):
            return return_code
    monkeypatch.setattr(dashboard_jobs.subprocess, "Popen", Process)
    record = {**job("collect-test"), "action": "collect"}
    database.add_job(record)
    dashboard_jobs._run_job(record["id"], {"id": "hiphop", "name": "채널", "config_path": "test.yaml"}, "collect", [])
    assert [method for method, _ in calls] == ["sendMessage", "editMessageText", "editMessageText"]
    assert "뉴스 수집 시작" in calls[0][1]["text"]
    assert "[ 작업중 ] 기사 수집" in calls[1][1]["text"]
    assert ("뉴스 수집 실패" if return_code else "뉴스 수집 완료") in calls[-1][1]["text"]
    assert database.get_job(record["id"])["status"] == ("failed" if return_code else "completed")


def test_workers_stream_log_and_next_job_runs_after_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(dashboard_jobs, "profile_config", lambda _: {"telegram": {"enabled": False}})
    monkeypatch.setattr(dashboard_jobs, "build_snapshot", lambda _: {})
    monkeypatch.setattr(dashboard_jobs, "_env_values", lambda _: {})
    monkeypatch.setattr(dashboard_jobs, "notify_build", lambda *a, **k: True)
    monkeypatch.setattr(database, "MAX_RUNNING_BUILDS", 1)  # exercise the waiting path with one slot
    database.add_job(job("first"))
    database.add_job(job("second", "football"))
    first_read = threading.Event()
    release = threading.Event()
    calls = []
    class Process:
        def __init__(self, command, **kwargs):
            self.name = command[-1]
            calls.append(self.name)
            self.stdout = self.lines()
        def lines(self):
            yield build_progress.PREFIX + json.dumps({"stage": "이미지 생성", "status": "running"}, ensure_ascii=False) + "\n"
            if self.name == "first":
                first_read.set()
                assert release.wait(5)
                yield "RuntimeError: 사진 교체 필요\n"
            else:
                yield "제작 완료\n"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def wait(self):
            return 1 if self.name == "first" else 0
    monkeypatch.setattr(dashboard_jobs.subprocess, "Popen", Process)
    profile = {"id": "hiphop", "name": "test", "config_path": "test.yaml"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(dashboard_jobs._run_job, "first", profile, "build", ["first"])
        assert first_read.wait(5)
        second = pool.submit(dashboard_jobs._run_job, "second", profile, "build", ["second"])
        assert database.get_job("first")["status"] == "running"
        assert "이미지 생성" in database.get_job("first")["log"]
        assert database.get_job("second")["status"] == "queued"
        assert calls == ["first"]
        release.set()
        first.result(5)
        second.result(5)
    assert calls == ["first", "second"]
    assert database.get_job("first")["status"] == "failed"
    assert database.get_job("second")["status"] == "completed"


@pytest.mark.parametrize("size,success", [((2292, 1200), False), ((1080, 1440), True)])
def test_moderation_fallback_only_uses_publishable_original(monkeypatch, tmp_path, size, success):
    source = tmp_path / "source.png"
    Image.new("RGB", size).save(source)
    monkeypatch.setattr(render, "_download_cover_image", lambda *args: source)
    attempts = []
    def blocked(*args):
        attempts.append(True)
        raise RuntimeError("moderation_blocked")
    monkeypatch.setattr(render, "_transform_cover_image", blocked)
    card = CardNews(topic="test", rationale="", source_urls=[], caption="", hashtags=[],
                    slides=[Slide(role="cover", title="test", body="", image_url="source")])
    config = {"publication_frame": "exact", "cover": {"renderer": "auto"},
              "body": {"renderer": "auto"}, "cta": {"renderer": "auto"}}
    args = (card, config, tmp_path / "out")
    if success:
        assert len(render.render_hybrid(*args, image_transform={"enabled": True, "generate_missing": False})) == 1
    else:
        with pytest.raises(RuntimeError, match="AI 안전 검사.*3:4 사진으로 교체"):
            render.render_hybrid(*args, image_transform={"enabled": True, "generate_missing": False})
        assert not list((tmp_path / "out").glob("slide_*.jpg"))
    assert attempts == [True]
