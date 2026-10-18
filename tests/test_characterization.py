import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cardnews_auto import cli, webapp
from cardnews_auto.models import Article, CardNews, Slide
from cardnews_auto.render import save_metadata
from cardnews_auto.telegram_bot import send_review


def test_browser_approval_http_response_contract(monkeypatch, tmp_path):
    profile = {
        "id": "channel-a",
        "name": "Channel A",
        "config_path": str(tmp_path / "config.yaml"),
    }
    job = {
        "id": "job-1",
        "action": "build",
        "status": "queued",
        "queue_position": 2,
    }
    monkeypatch.setattr(webapp, "_profile", lambda _profile_id: profile)
    monkeypatch.setattr(webapp, "_state_path", lambda _profile: tmp_path / "workflow.json")
    monkeypatch.setattr(
        webapp,
        "approve_candidate",
        lambda _path, _index, _actor: Article(
            title="승인 뉴스",
            url="https://example.com/news",
        ),
    )
    monkeypatch.setattr(
        webapp,
        "_enqueue_article_build",
        lambda _profile, _article, _source: job,
    )
    monkeypatch.setattr(webapp, "add_audit_event", lambda *_args, **_kwargs: None)

    response = TestClient(webapp.app).post(
        "/api/profiles/channel-a/approve",
        json={"index": 3},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "title": "승인 뉴스",
        "build_job": job,
    }


def test_profile_create_and_update_http_contract(monkeypatch):
    profiles = []
    events = []
    monkeypatch.setattr(webapp, "_registry", lambda: profiles)
    monkeypatch.setattr(webapp, "_safe_id", lambda _name: "channel-a")
    monkeypatch.setattr(webapp, "_now", lambda: "2026-07-26T00:00:00+00:00")
    monkeypatch.setattr(webapp, "_write_profile_config", lambda *_args: None)
    monkeypatch.setattr(webapp, "_profile_config", lambda _profile: {})
    monkeypatch.setattr(webapp, "_atomic_json", lambda *_args: None)
    monkeypatch.setattr(
        webapp,
        "add_audit_event",
        lambda event, profile_id, **_kwargs: events.append((event, profile_id)),
    )
    monkeypatch.setattr(webapp, "_public_profile", lambda profile: profile.copy())
    client = TestClient(webapp.app)
    payload = {"name": "Channel A", "category": "Music"}

    created = client.post("/api/profiles", json=payload)
    updated = client.put(
        "/api/profiles/channel-a",
        json={"name": "Channel A+", "category": "Culture"},
    )

    assert created.status_code == 200
    assert created.json()["id"] == "channel-a"
    assert created.json()["created_at"] == "2026-07-26T00:00:00+00:00"
    assert updated.status_code == 200
    assert updated.json()["name"] == "Channel A+"
    assert updated.json()["category"] == "Culture"
    assert events == [
        ("profile.create", "channel-a"),
        ("profile.update", "channel-a"),
    ]


def test_telegram_review_payload_contract(monkeypatch, tmp_path):
    state_path = tmp_path / "workflow.json"
    state_path.write_text(json.dumps({
        "collected_at": "2026-07-26T00:00:00+00:00",
        "category": "외국 힙합",
        "latest_candidate_ids": ["candidate-a"],
        "candidates": [{
            "id": "candidate-a",
            "title": "Drake announces a tour",
            "summary": "드레이크가 새 투어를 암시했습니다.",
            "source": "Example News",
            "published": "2026-07-26",
            "url": "https://example.com/drake",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    sent = []

    class FakeClient:
        chat_id = "test-chat"

        def send(self, text, reply_markup=None):
            sent.append((text, reply_markup))
            return {"message_id": len(sent)}

    monkeypatch.setattr(
        "cardnews_auto.telegram_bot.client_from_env",
        lambda: FakeClient(),
    )

    result = send_review(state_path, max_candidates=10)

    assert len(result) == 3
    assert "검토할 후보 1건" in sent[0][0]
    assert "외국 힙합" in sent[0][0]
    assert "Drake announces a tour" in sent[1][0]
    assert "드레이크가 새 투어를 암시했습니다." in sent[1][0]
    assert "https://example.com/drake" in sent[1][0]
    button = sent[1][1]["inline_keyboard"][0][0]
    assert button["callback_data"].startswith("approveid:candidate-a:")
    assert len(button["callback_data"].encode()) <= 64
    assert sent[2][1]["inline_keyboard"][0][0]["callback_data"].startswith("hold:0:")


def test_local_artifact_manifest_contract(tmp_path):
    card = CardNews(
        topic="테스트 주제",
        rationale="선정 이유",
        source_interpretation="원문 해석",
        editorial_summary="편집 요약",
        body_draft="자연스럽게 작성한 전체 본문",
        source_urls=["https://example.com/news"],
        slides=[
            Slide(role="cover", title="첫째 줄\n둘째 줄", body="Example"),
            Slide(role="body", title="본문 제목", body="본문 설명입니다."),
            Slide(role="cta", title="", body=""),
        ],
        caption="게시용 캡션",
        hashtags=["뉴스", "매거진"],
    )
    audit = [{"slide": 1, "status": "selected", "url": "https://example.com/image.jpg"}]

    files = save_metadata(card, tmp_path, audit)

    assert [path.name for path in files] == [
        "cardnews.json",
        "caption.txt",
        "image_sources.json",
    ]
    manifest = json.loads((tmp_path / "cardnews.json").read_text(encoding="utf-8"))
    assert manifest["topic"] == "테스트 주제"
    assert [slide["role"] for slide in manifest["slides"]] == ["cover", "body", "cta"]
    assert manifest["body_draft"] == "자연스럽게 작성한 전체 본문"
    assert (tmp_path / "caption.txt").read_text(encoding="utf-8") == (
        "게시용 캡션\n\n#뉴스 #매거진"
    )
    assert json.loads(
        (tmp_path / "image_sources.json").read_text(encoding="utf-8"),
    ) == audit


def test_drive_success_then_telegram_failure_leaves_build_uncommitted(
    monkeypatch,
    tmp_path,
):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({
        "job_id": "job-1",
        "candidate_id": "candidate-1",
        "article": {
            "title": "부분 성공 테스트",
            "url": "https://example.com/news",
            "image_url": "",
        },
        "revision": {},
    }, ensure_ascii=False), encoding="utf-8")
    card = CardNews(
        topic="부분 성공 테스트",
        rationale="선정 이유",
        source_urls=["https://example.com/news"],
        slides=[
            Slide(role="cover", title="부분 성공\n테스트", body="Example"),
            Slide(role="cta", title="", body=""),
        ],
        caption="캡션",
        hashtags=[],
    )
    image = tmp_path / "slide_01.jpg"
    image.write_bytes(b"image")
    metadata = tmp_path / "cardnews.json"
    metadata.write_text("{}", encoding="utf-8")
    events = []

    monkeypatch.setattr(cli, "approved_template", lambda cfg: cfg["template"])
    monkeypatch.setattr(cli, "create_cardnews", lambda _articles, _cfg: card)
    monkeypatch.setattr(cli, "assign_slide_images", lambda *_args: [])
    monkeypatch.setattr(cli, "discover_article_image", lambda _url: "")
    monkeypatch.setattr(
        cli,
        "render_hybrid",
        lambda *_args, **_kwargs: events.append("render") or [image],
    )
    monkeypatch.setattr(
        cli,
        "save_metadata",
        lambda *_args, **_kwargs: events.append("metadata") or [metadata],
    )
    monkeypatch.setattr(
        cli,
        "upload_files",
        lambda *_args, **_kwargs: events.append("drive") or {
            "file_ids": ["drive-file"],
            "folder_url": "https://drive.google.com/drive/folders/result",
            "path": "2026/07/2026-07-26/Channel/result",
        },
    )

    def fail_delivery(*_args, **_kwargs):
        events.append("telegram")
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(cli, "send_delivery", fail_delivery)
    monkeypatch.setattr(
        cli,
        "mark_built",
        lambda *_args, **_kwargs: events.append("built"),
    )
    config = {
        "magazine": {"name": "Channel"},
        "brand": {"name": "Channel"},
        "generation": {},
        "image_sources": {},
        "image_transform": {},
        "template": {
            "cover": {},
            "body": {},
            "cta": {},
        },
        "drive": {
            "enabled": True,
            "credentials_file": "credentials.json",
            "token_file": "token.json",
        },
        "telegram": {
            "enabled": True,
            "send_outputs_after_upload": True,
        },
    }

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        cli._build(
            tmp_path / "config.yaml",
            config,
            preview=False,
            no_upload=False,
            article_file=str(snapshot_path),
        )

    assert events == ["render", "metadata", "drive", "telegram"]
    assert "built" not in events
