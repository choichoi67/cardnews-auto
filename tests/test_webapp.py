import asyncio
from io import BytesIO
import json
import threading
from datetime import datetime

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from cardnews_auto import dashboard_core, dashboard_jobs, revision_api, webapp
from cardnews_auto.models import Article


def test_psd_upload_writes_chunks_and_removes_oversized_file(tmp_path):
    target = tmp_path / "template.psd"
    asyncio.run(dashboard_core.save_psd_upload(
        UploadFile(filename="template.psd", file=BytesIO(b"psd-data")),
        target,
    ))
    assert target.read_bytes() == b"psd-data"

    oversized = tmp_path / "oversized.psd"
    with pytest.raises(webapp.HTTPException) as error:
        asyncio.run(dashboard_core.save_psd_upload(
            UploadFile(filename="oversized.psd", file=BytesIO(b"1234")),
            oversized,
            max_bytes=3,
        ))
    assert error.value.status_code == 413
    assert not oversized.exists()


def test_next_collection_rolls_to_tomorrow():
    now = datetime(2026, 7, 18, 21, 0, tzinfo=dashboard_core.SEOUL)
    result = webapp._next_collection_at(["09:00", "14:00", "20:00"], now)
    assert result.startswith("2026-07-19T09:00")


def test_profile_schedule_accepts_one_to_ten_times():
    payload = webapp.ProfileInput(name="테스트", category="뉴스", collection_times=["09:00"])
    assert payload.collection_times == ["09:00"]
    with pytest.raises(ValueError):
        webapp.ProfileInput(name="테스트", category="뉴스", collection_times=[])


def test_profile_schedule_rejects_duplicates_over_ten_and_invalid_times():
    with pytest.raises(ValueError):
        webapp.ProfileInput(name="test", category="news", collection_times=["09:00", "09:00"])
    with pytest.raises(ValueError):
        webapp.ProfileInput(name="test", category="news", collection_times=[f"{hour:02d}:00" for hour in range(11)])
    with pytest.raises(ValueError):
        webapp.ProfileInput(name="test", category="news", collection_times=["24:00"])


def test_profile_accepts_channel_caption_prompt():
    prompt = "**✅ [뉴스] [헤드라인]**\n\n**Song - [곡명]**\n**Artist - [아티스트]**"
    payload = webapp.ProfileInput(name="외힙명곡", category="음악", caption_prompt=prompt)
    assert payload.caption_prompt == prompt


def test_profile_has_grounded_body_prompt_by_default():
    payload = webapp.ProfileInput(name="외힙명곡", category="음악")
    assert "승인 기사에 있는 핵심 사실" in payload.body_prompt
    assert "source_interpretation" in payload.body_prompt
    assert "editorial_summary" in payload.body_prompt
    assert "body_draft" in payload.body_prompt
    assert "핵심 사실과 수치를 왜곡하지 않습니다" in payload.body_prompt
    assert "2~3줄 분량의 완결된 문장" in payload.body_prompt
    assert "미완성 문장과 중복 내용을 고칩니다" in payload.body_prompt


def test_profile_has_two_line_cover_prompt_by_default():
    payload = webapp.ProfileInput(name="외힙명곡", category="음악")
    assert "정확히 2줄" in payload.cover_prompt
    assert "줄바꿈 문자를 정확히 한 번" in payload.cover_prompt
    assert "근거 없는 숫자" in payload.cover_prompt


def test_profile_accepts_channel_scoped_image_source_rules():
    payload = webapp.ProfileInput(
        name="외힙명곡",
        category="음악",
        web_image_search_enabled=True,
        rss_image_enabled=True,
        rss_image_same_site_only=True,
    )
    assert payload.web_image_search_enabled is True
    assert payload.rss_image_same_site_only is True


def test_dashboard_approval_immediately_enqueues_drive_build(monkeypatch, tmp_path):
    profile = {"id": "channel-a", "name": "A", "config_path": str(tmp_path / "config.yaml")}
    enqueued = []

    monkeypatch.setattr(webapp, "_profile", lambda profile_id: profile)
    monkeypatch.setattr(webapp, "_state_path", lambda item: tmp_path / "workflow.json")
    monkeypatch.setattr(
        webapp,
        "approve_candidate",
        lambda path, index, approved_by: Article(title="승인 뉴스", url="https://example.com/news"),
    )
    monkeypatch.setattr(
        webapp,
        "_enqueue_article_build",
        lambda item, article, source: enqueued.append((item["id"], article, source)) or {
            "id": "job-1", "action": "build", "status": "queued",
        },
    )
    monkeypatch.setattr(webapp, "add_audit_event", lambda *args, **kwargs: None)

    result = webapp.approve("channel-a", webapp.ApproveInput(index=1))

    assert result["build_job"]["status"] == "queued"
    assert enqueued == [("channel-a", Article(title="승인 뉴스", url="https://example.com/news"), "browser")]


def test_same_channel_accepts_multiple_snapshotted_build_jobs(monkeypatch, tmp_path):
    added = []
    launched = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            launched.append((target, args, daemon))

        def start(self):
            return None

    monkeypatch.setattr(dashboard_jobs, "JOB_INPUT_DIR", tmp_path / "job-inputs")
    monkeypatch.setattr(dashboard_jobs, "add_job", lambda job: added.append(job.copy()))
    monkeypatch.setattr(dashboard_jobs.threading, "Thread", FakeThread)
    profile = {"id": "channel-a", "name": "A", "config_path": "C:/channel/config.yaml"}

    first = dashboard_jobs.enqueue_job(
        profile, "build", article=Article(title="First", url="https://example.com/first"),
        deduplicate=False,
    )
    second = dashboard_jobs.enqueue_job(
        profile, "build", article=Article(title="Second", url="https://example.com/second"),
        deduplicate=False,
    )

    assert first["id"] != second["id"]
    assert len(added) == 2
    assert len(launched) == 2
    snapshots = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / "job-inputs").glob("*.json"))
    ]
    assert {item["article"]["title"] for item in snapshots} == {"First", "Second"}


def test_telegram_listener_never_starts_a_duplicate_thread(monkeypatch):
    threads = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.alive = True
            threads.append(self)

        def start(self):
            pass

        def is_alive(self):
            return self.alive

    monkeypatch.setattr(dashboard_jobs.threading, "Thread", FakeThread)
    monkeypatch.setattr(dashboard_jobs, "_telegram_thread", None)
    dashboard_jobs.start_telegram_listener()
    dashboard_jobs.start_telegram_listener()
    assert len(threads) == 1
    threads[0].alive = False
    dashboard_jobs.start_telegram_listener()
    assert len(threads) == 2


def test_browser_revision_uses_shared_build_queue(monkeypatch, tmp_path):
    profile = {"id": "channel-a", "name": "A", "config_path": str(tmp_path / "config.yaml")}
    record = {
        "id": 11, "magazine_id": "channel-a", "topic": "Original",
        "output_dir": str(tmp_path / "output"), "revision_number": 0,
    }
    queued = []
    monkeypatch.setattr(revision_api, "get_artifact", lambda artifact_id: record)
    monkeypatch.setattr(revision_api, "profile", lambda profile_id: profile)
    monkeypatch.setattr(
        revision_api, "_artifact_source",
        lambda item, selected: Article(title="Original", url="https://example.com/news"),
    )
    monkeypatch.setattr(revision_api, "_cardnews_path", lambda item: tmp_path / "cardnews.json")
    monkeypatch.setattr(
        revision_api, "enqueue_article_build",
        lambda selected, article, source, revision=None: queued.append((source, revision)) or {
            "id": "revision-job", "status": "queued", "queue_position": 2,
        },
    )
    monkeypatch.setattr(revision_api, "add_audit_event", lambda *args, **kwargs: None)

    result = asyncio.run(revision_api.revise_artifact(
        11,
        instruction="표지를 강하게",
        cover_title="새 표지",
        slide_number=0,
        slide_title="",
        slide_body="",
        reference_image=None,
    ))

    assert result["queued"] is True
    assert queued[0][0] == "browser"
    assert queued[0][1]["parent_artifact_id"] == 11
    assert queued[0][1]["number"] == 1
    assert queued[0][1]["cover_title"] == "새 표지"


def test_artifact_detail_exposes_every_editable_slide(monkeypatch, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    for index in range(1, 4):
        (output / f"slide_{index:02d}.jpg").write_bytes(b"jpg")
    (output / "cardnews.json").write_text(json.dumps({
        "slides": [
            {"role": "cover", "title": "표지", "body": "출처"},
            {"role": "body", "title": "본문 제목", "body": "본문 설명"},
            {"role": "cta", "title": "CTA", "body": "마무리"},
        ],
        "caption": "caption",
        "source_interpretation": "충실한 원문 해석",
        "editorial_summary": "검수할 편집 요약",
        "body_draft": "먼저 자연스럽게 완성한 본문 초안",
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(revision_api, "get_artifact", lambda artifact_id: {
        "id": artifact_id, "magazine_id": "channel-a", "output_dir": str(output),
        "topic": "Topic", "slide_count": 3,
    })

    result = revision_api.artifact_detail(7)

    assert [slide["role"] for slide in result["slides"]] == ["cover", "body", "cta"]
    assert result["slides"][1]["title"] == "본문 제목"
    assert result["slides"][1]["image_url"].endswith("/slide_02.jpg")
    assert result["source_interpretation"] == "충실한 원문 해석"
    assert result["editorial_summary"] == "검수할 편집 요약"
    assert result["body_draft"] == "먼저 자연스럽게 완성한 본문 초안"


def test_telegram_revision_uses_original_job_snapshot(monkeypatch, tmp_path):
    profile = {"id": "channel-a", "name": "A", "config_path": str(tmp_path / "config.yaml")}
    original = {"id": "old-job", "profile_id": "channel-a", "action": "build"}
    queued = []
    monkeypatch.setattr(revision_api, "get_job", lambda job_id: original)
    monkeypatch.setattr(revision_api, "profile", lambda profile_id: profile)
    monkeypatch.setattr(revision_api, "state_path", lambda selected: tmp_path / "workflow.json")
    monkeypatch.setattr(revision_api, "load_state", lambda path: {"builds": []})
    monkeypatch.setattr(revision_api, "sync_workflow", lambda *args, **kwargs: None)
    monkeypatch.setattr(revision_api, "artifacts_for_magazine", lambda magazine_id: [])
    monkeypatch.setattr(revision_api, "build_snapshot", lambda job_id: {
        "article": {"title": "Original", "url": "https://example.com/news"},
        "revision": {},
    })
    monkeypatch.setattr(
        revision_api, "enqueue_article_build",
        lambda selected, article, source, revision=None: queued.append((source, revision)) or {
            "id": "new-job", "status": "queued",
        },
    )
    monkeypatch.setattr(revision_api, "add_audit_event", lambda *args, **kwargs: None)

    result = revision_api.revise_job(
        "old-job", revision_api.RevisionInput(instruction="본문을 더 구체적으로")
    )

    assert result["queued"] is True
    assert queued[0][0] == "telegram"
    assert queued[0][1]["instruction"] == "본문을 더 구체적으로"


def test_dashboard_build_status_is_forwarded_to_telegram(monkeypatch, tmp_path):
    profile = {"id": "channel-a", "name": "A", "config_path": str(tmp_path / "config.yaml")}
    state_path = tmp_path / "workflow.json"
    state_path.write_text(json.dumps({
        "approved": {"article": {"title": "승인 뉴스"}},
        "build": {
            "output_dir": "C:/output",
            "drive_url": "https://drive.google.com/folder",
        },
    }), encoding="utf-8")
    calls = []

    monkeypatch.setattr(dashboard_jobs, "profile_config", lambda item: {"telegram": {"enabled": True}})
    monkeypatch.setattr(dashboard_jobs, "state_path", lambda item: state_path)
    monkeypatch.setattr(dashboard_jobs, "add_audit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard_jobs,
        "send_build_status",
        lambda status, topic, job_id, environ=None, **values: calls.append((status, topic, job_id, values)),
    )
    monkeypatch.setattr(
        dashboard_jobs,
        "send_revision_controls",
        lambda topic, profile_id, job_id, profile_name, environ=None: calls.append(
            ("completed", topic, profile_id, job_id, profile_name)
        ),
    )

    assert dashboard_jobs.notify_build(profile, "running", "job-1")
    assert dashboard_jobs.notify_build(profile, "failed", "job-1", "실패 원인")
    assert dashboard_jobs.notify_build(profile, "failed", "job-2", "insufficient_quota")
    assert dashboard_jobs.notify_build(profile, "completed", "job-1")
    assert calls == [
        ("running", "승인 뉴스", "job-1", {"error": "상세 오류가 기록되지 않았습니다."}),
        ("failed", "승인 뉴스", "job-1", {"error": "실패 원인"}),
        ("failed", "승인 뉴스", "job-2", {"error": (
            "OpenAI API 사용 한도에 도달했습니다. ChatGPT 구독과 API 결제는 별도입니다. "
            "OpenAI Platform에서 결제수단·월 사용 한도를 확인한 뒤 다시 실행하세요."
        )}),
        ("completed", "승인 뉴스", "channel-a", "job-1", "A"),
    ]


def test_job_retry_allows_failed_and_rejects_completed(monkeypatch):
    profile = {"id": "channel-a", "name": "A", "config_path": "unused"}
    monkeypatch.setattr(
        dashboard_jobs, "enqueue_job",
        lambda item, action, args=None, **kwargs: {"id": "new-job", "action": action, "status": "queued"},
    )
    original = {
        "id": "old-job", "profile_id": "channel-a", "profile_name": "A",
        "action": "collect", "status": "failed",
    }
    monkeypatch.setattr(dashboard_jobs, "get_job", lambda job_id: original)

    retried = dashboard_jobs.retry_job("old-job", profile)
    assert retried["retried_from"] == "old-job"
    assert retried["action"] == "collect"

    original["status"] = "completed"
    with pytest.raises(ValueError):
        dashboard_jobs.retry_job("old-job", profile)


def test_failed_rebuild_retry_preserves_article_and_revision_snapshot(monkeypatch):
    profile = {"id": "channel-a", "name": "A", "config_path": "unused"}
    captured = {}

    def fake_enqueue(item, action, args=None, **kwargs):
        captured.update(profile=item, action=action, args=args, **kwargs)
        return {"id": "new-job", "action": action, "status": "queued"}

    monkeypatch.setattr(dashboard_jobs, "enqueue_job", fake_enqueue)
    monkeypatch.setattr(dashboard_jobs, "get_job", lambda _job_id: {
        "id": "old-job",
        "profile_id": "channel-a",
        "profile_name": "A",
        "action": "build",
        "status": "failed",
    })
    monkeypatch.setattr(dashboard_jobs, "build_snapshot", lambda _job_id: {
        "job_id": "old-job",
        "candidate_id": "candidate-1",
        "article": {"title": "Drake ICEMAN", "url": "https://example.com/drake"},
        "revision": {"edit_mode": "regenerate", "instruction": "전체 재제작"},
    })

    retried = dashboard_jobs.retry_job("old-job", profile, ["--no-upload"])

    assert retried["retried_from"] == "old-job"
    assert captured["article"].title == "Drake ICEMAN"
    assert captured["snapshot_extra"]["revision"]["instruction"] == "전체 재제작"
    assert captured["deduplicate"] is False


def test_retry_of_photoshop_job_uses_current_design_and_photo_mode(monkeypatch, tmp_path):
    import copy
    psd = str(tmp_path / "cover.psd")
    frozen = {"article": {"title": "Test", "url": "https://example.com/test"},
              "build_config": {"generation": {"model": "frozen-model"}, "template": {
                  "cover": {"psd_path": psd, "text_options": {"title": {"max_chars": 20}}},
                  "body": {"psd_path": str(tmp_path / "other.psd")}}}}
    monkeypatch.setattr(dashboard_jobs, "get_job", lambda _: {"id": "old", "profile_id": "a", "action": "build", "status": "failed"})
    monkeypatch.setattr(dashboard_jobs, "build_snapshot", lambda _: copy.deepcopy(frozen))
    monkeypatch.setattr(dashboard_jobs, "profile_config", lambda _: {"image_transform": {"recompose_for_publication": True, "model": "grok-imagine-image-2.0"}, "template": {"cover": {
        "psd_path": psd, "text_options": {"title": {"max_chars": 40, "allowed_bounds": [70, 930, 1010, 1220]}}}}})
    captured = {}
    def enqueue(*args, **kwargs):
        captured.update(kwargs)
        return {"id": "new"}
    monkeypatch.setattr(dashboard_jobs, "enqueue_job", enqueue)
    dashboard_jobs.retry_job("old", {"id": "a"}, ["--no-upload"])
    result = captured["snapshot_extra"]["build_config"]
    assert result["generation"]["model"] == "frozen-model"
    assert result["image_transform"]["recompose_for_publication"] is True
    assert result["image_transform"]["model"] == "grok-imagine-image-2.0"
    # A job frozen with Photoshop templates retries with the channel's current studio design.
    assert result["template"]["cover"]["text_options"]["title"] == {
        "max_chars": 40, "allowed_bounds": [70, 930, 1010, 1220]}


def test_overview_builds_global_candidate_projection_from_workflow(monkeypatch):
    approved = {"id": "approved", "title": "Approved", "url": "https://example.com/approved"}
    profile = {
        "id": "channel-a", "name": "A", "color": "#123456",
        "workflow": {
            "approved": {"candidate_id": dashboard_jobs.candidate_id_for(approved)},
            "candidates": [
                {"id": "open", "title": "Open", "url": "https://example.com/open"},
                approved,
                {"id": "built", "title": "Built", "url": "https://example.com/built", "built_count": 1},
            ],
        },
    }
    monkeypatch.setattr(webapp, "_registry", lambda: [profile])
    monkeypatch.setattr(webapp, "_public_profile", lambda item: item)
    monkeypatch.setattr(webapp, "newsroom_data", lambda: {"metrics": {}, "artifacts": []})
    monkeypatch.setattr(webapp, "list_audit_events", lambda limit: [])
    monkeypatch.setattr(webapp, "job_views", lambda: [])

    result = webapp.overview()["newsroom"]

    assert result["metrics"]["candidates"] == 1
    assert result["metrics"]["approved"] == 1
    assert [item["status"] for item in result["candidates"]] == ["candidate", "approved", "built"]


def test_candidate_disposition_api_is_channel_scoped_and_audited(monkeypatch, tmp_path):
    from cardnews_auto.workflow import save_candidates

    state_path = tmp_path / "channel-a" / "state" / "workflow.json"
    state = save_candidates(
        [Article(title="Candidate", url="https://example.com/candidate")],
        "news",
        state_path,
    )
    candidate_id = state["candidates"][0]["id"]
    profile = {"id": "channel-a", "name": "A", "config_path": str(tmp_path / "channel-a" / "config.yaml")}
    audited = []
    monkeypatch.setattr(webapp, "_profile", lambda profile_id: profile if profile_id == "channel-a" else (_ for _ in ()).throw(webapp.HTTPException(404)))
    monkeypatch.setattr(webapp, "sync_workflow", lambda *args: None)
    monkeypatch.setattr(webapp, "add_audit_event", lambda *args, **kwargs: audited.append((args, kwargs)))

    result = webapp.update_candidate_disposition(
        "channel-a", candidate_id, webapp.CandidateDispositionInput(disposition="hidden"),
    )

    assert result["disposition"] == "hidden"
    assert webapp.load_state(state_path)["candidates"][0]["disposition"] == "hidden"
    assert audited[0][0][:2] == ("candidate.disposition", "channel-a")
    with pytest.raises(webapp.HTTPException) as missing:
        webapp.update_candidate_disposition(
            "channel-a", "not-owned", webapp.CandidateDispositionInput(disposition="held"),
        )
    assert missing.value.status_code == 404


def test_scheduler_runs_each_channel_time_only_once(monkeypatch, tmp_path):
    profile = {"id": "channel-a", "name": "A", "config_path": "unused"}
    enqueued = []

    monkeypatch.setattr(dashboard_jobs, "registry", lambda: [profile])
    monkeypatch.setattr(dashboard_jobs, "profile_config", lambda item: {"schedule": {"enabled": True, "times": ["09:00"]}})
    monkeypatch.setattr(
        dashboard_jobs, "enqueue_job",
        lambda item, action: enqueued.append((item["id"], action)),
    )
    claims = set()

    def claim(profile_id, scheduled_for, claimed_at):
        key = (profile_id, scheduled_for)
        if key in claims:
            return False
        claims.add(key)
        return True

    monkeypatch.setattr(dashboard_jobs, "claim_schedule_run", claim)
    now = datetime(2026, 7, 18, 9, 0, tzinfo=dashboard_core.SEOUL)
    assert dashboard_jobs.run_schedules(now) == ["channel-a"]
    assert dashboard_jobs.run_schedules(now) == []
    assert enqueued == [("channel-a", "collect")]


def test_scheduler_recovers_a_just_missed_slot_after_restart(monkeypatch, tmp_path):
    profile = {"id": "channel-a", "name": "A", "config_path": "unused"}
    enqueued = []
    claims = set()

    def claim(profile_id, scheduled_for, claimed_at):
        key = (profile_id, scheduled_for)
        if key in claims:
            return False
        claims.add(key)
        return True

    monkeypatch.setattr(dashboard_jobs, "registry", lambda: [profile])
    monkeypatch.setattr(dashboard_jobs, "profile_config", lambda item: {"schedule": {"enabled": True, "times": ["09:00"]}})
    monkeypatch.setattr(dashboard_jobs, "claim_schedule_run", claim)
    monkeypatch.setattr(
        dashboard_jobs, "enqueue_job",
        lambda item, action: enqueued.append((item["id"], action))
        or {"id": "job-a", "deduplicated": False},
    )

    assert dashboard_jobs.run_schedules(datetime(2026, 7, 18, 9, 3, tzinfo=dashboard_core.SEOUL)) == ["channel-a"]
    assert dashboard_jobs.run_schedules(datetime(2026, 7, 18, 9, 3, tzinfo=dashboard_core.SEOUL)) == []
    assert enqueued == [("channel-a", "collect")]


def test_legacy_invalid_schedule_does_not_break_next_collection():
    now = datetime(2026, 7, 18, 8, 0, tzinfo=dashboard_core.SEOUL)
    assert webapp._next_collection_at(["bad", "09:00", "25:00", "09:00"], now).startswith("2026-07-18T09:00")
    assert webapp._next_collection_at(["bad"], now) == ""
