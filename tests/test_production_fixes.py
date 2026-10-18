import json
from pathlib import Path

import pytest
import yaml
from PIL import Image

from cardnews_auto import cli, dashboard_core, render, writer
from cardnews_auto.models import Article, CardNews, Slide


def test_telegram_instruction_rejected_before_any_queue_write(monkeypatch):
    from cardnews_auto import revision_api
    from fastapi import HTTPException
    monkeypatch.setattr(revision_api, "get_job", lambda _: {"action": "build"})
    monkeypatch.setattr(revision_api, "profile", lambda *_: pytest.fail("request continued after guidance"))
    monkeypatch.setattr(revision_api, "enqueue_article_build", lambda *_: pytest.fail("invalid request queued"))
    with pytest.raises(HTTPException) as error:
        revision_api.revise_job("job", revision_api.RevisionInput(slide_number=2, instruction="제목을 짧게"))
    assert error.value.status_code == 400
    assert "http://127.0.0.1:8765/#outputs" in error.value.detail
    assert "등록하지 않았습니다" in error.value.detail


def test_telegram_finished_copy_can_still_be_submitted(monkeypatch):
    from cardnews_auto import revision_api
    monkeypatch.setattr(revision_api, "get_job", lambda _: {"action": "build", "profile_id": "test"})
    monkeypatch.setattr(revision_api, "profile", lambda _: {"id": "test"})
    monkeypatch.setattr(revision_api, "state_path", lambda _: Path("unused"))
    monkeypatch.setattr(revision_api, "load_state", lambda _: {})
    monkeypatch.setattr(revision_api, "sync_workflow", lambda *_: None)
    monkeypatch.setattr(revision_api, "artifacts_for_magazine", lambda _: [{"job_id": "job", "id": 1}])
    monkeypatch.setattr(revision_api, "build_snapshot", lambda _: {"article": {
        "title": "Original", "url": "https://example.com"}})
    monkeypatch.setattr(revision_api, "_enqueue_revision", lambda record, profile, article, source, payload, **kw: {
        "source": source, "title": payload.slide_title, "body": payload.slide_body})
    result = revision_api.revise_job("job", revision_api.RevisionInput(
        slide_number=2, slide_title="수정 제목", slide_body="수정 본문"))
    assert result == {"source": "telegram", "title": "수정 제목", "body": "수정 본문"}


def card():
    return CardNews(topic="테스트", rationale="", source_urls=[], caption="", hashtags=[], slides=[
        Slide(role="cover", title="새 소식", body="짧은 설명"),
        Slide(role="body", title="본문", body="첫 문장입니다."),
        Slide(role="cta", title="마무리", body=""),
    ])


def baseline(tmp_path):
    value = card()
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (1600, 900), "blue").save(photo)
    for i, slide in enumerate(value.slides[:-1]):
        photo = tmp_path / f"photo-{i}.jpg"
        Image.new("RGB", (1600, 900), "blue" if i == 0 else "red").save(photo)
        slide.image_url = str(photo)
    template = {role: {"renderer": "auto"} for role in ("cover", "body", "cta")}
    out = tmp_path / "output" / "base"
    render.render_hybrid(value, template, out, image_transform={"enabled": False})
    render.save_metadata(value, out, [])
    return value, template, out


def test_new_channel_cannot_inherit_another_channels_psd(monkeypatch, tmp_path):
    config = {"brand": {}, "news": {}, "generation": {"model": "test", "hashtags": 1},
              "template": {"template_variants": {"cover": [{"psd_path": "other-channel.psd"}]}}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setattr(dashboard_core, "ROOT", tmp_path)
    path = dashboard_core.write_profile_config({"id": "new", "config_path": str(tmp_path / "new.yaml")},
        dashboard_core.ProfileInput(name="새 채널", category="패션"))
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "other-channel.psd" not in path.read_text(encoding="utf-8")
    assert all(saved["template"][role]["renderer"] == "unconfigured" for role in ("cover", "body", "cta"))


def test_text_revision_reuses_untouched_outputs_and_never_downloads(monkeypatch, tmp_path):
    value, template, base = baseline(tmp_path)
    old_cover = (base / "slide_01.jpg").read_bytes()
    old_cta = (base / "slide_03.jpg").read_bytes()
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: pytest.fail("text edit downloaded an image"))
    monkeypatch.setattr(render, "plan_layout", lambda *_: pytest.fail("text edit replanned templates"))
    value.slides[1].body = "수정된 본문입니다."
    out = tmp_path / "output" / "revision"
    render.render_hybrid(value, template, out, revision={"edit_mode": "slide", "slide_number": 2,
        "base_cardnews": str(base / "cardnews.json")})
    assert (out / "slide_01.jpg").read_bytes() == old_cover
    assert (out / "slide_03.jpg").read_bytes() == old_cta
    assert (out / "slide_02.jpg").read_bytes() != (base / "slide_02.jpg").read_bytes()
    assert len(json.loads((out / "render_manifest.json").read_text(encoding="utf-8"))["slides"]) == 3


def test_changed_baseline_source_is_rejected(tmp_path):
    value, template, base = baseline(tmp_path)
    manifest = json.loads((base / "render_manifest.json").read_text(encoding="utf-8"))
    (base / manifest["slides"][1]["source"]).write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="변경"):
        render.render_hybrid(value, template, tmp_path / "revision", revision={"edit_mode": "slide", "slide_number": 2,
            "base_cardnews": str(base / "cardnews.json")})


def test_old_outputs_require_explicit_baseline_rebuild(tmp_path):
    with pytest.raises(RuntimeError, match="기준 버전"):
        render.render_hybrid(card(), {}, tmp_path / "revision", revision={"edit_mode": "slide", "slide_number": 1,
            "base_cardnews": str(tmp_path / "old" / "cardnews.json")})


def test_cli_empty_title_and_body_are_deleted(monkeypatch, tmp_path):
    value, template, base = baseline(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"article": {"title": "Test", "url": "https://example.com"},
        "revision": {"edit_mode": "slide", "slide_number": 2, "base_cardnews": str(base / "cardnews.json"),
                     "slide_title": "", "slide_body": ""}}), encoding="utf-8")
    def inspect(value, *args, **kwargs):
        assert value.slides[1].title == value.slides[1].body == ""
        raise RuntimeError("checked-empty")
    monkeypatch.setattr(cli, "render_hybrid", inspect)
    monkeypatch.setattr(cli, "discover_article_image", lambda *_: pytest.fail("text revision searched"))
    with pytest.raises(RuntimeError, match="checked-empty"):
        cli._build(tmp_path / "config.yaml", {"template": template}, False, True, str(snapshot))


def test_auto_layout_rejects_long_text_without_shrinking():
    from cardnews_auto.layout_engine import render_auto_layout
    with pytest.raises(RuntimeError):
        render_auto_layout("cover", "너무 긴 제목입니다 " * 20, "본문")


def test_overflow_requires_editing_instead_of_cloning_cards():
    value = card()
    text = "첫 사건입니다. 다음 사건입니다. 마지막 사건입니다."
    value.slides[1].body = text
    count = len(value.slides)
    with pytest.raises(RuntimeError, match="허용"):
        writer._normalize_editorial_cards(value, {"line_chars": 12, "max_lines": 2, "max_chars": 24})
    assert len(value.slides) == count
    assert value.slides[1].body == text


def test_completed_drive_is_not_uploaded_again_when_telegram_fails(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "upload_files", lambda *_: calls.append("drive") or {"folder_url": "https://example.com/folder"})
    def failure(*args):
        calls.append("telegram")
        raise RuntimeError("network")
    monkeypatch.setattr(cli, "send_delivery", failure)
    cfg = {"drive": {"enabled": True, "credentials_file": "credentials.json", "token_file": "token.json"},
           "telegram": {"enabled": True}}
    article = Article(title="Test", url="https://example.com")
    with pytest.raises(RuntimeError, match="network"):
        cli._deliver(tmp_path, card(), article, cfg, tmp_path / "config.yaml", False)
    with pytest.raises(RuntimeError, match="중복 방지"):
        cli._deliver(tmp_path, card(), article, cfg, tmp_path / "config.yaml", False)
    assert calls == ["drive", "telegram"]


def test_resume_delivery_skips_render(monkeypatch, tmp_path):
    value, template, base = baseline(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"resume_output": str(base), "article": {"title": "Test", "url": "https://example.com"}}), encoding="utf-8")
    monkeypatch.setattr(cli, "render_hybrid", lambda *_: pytest.fail("rendered a delivery retry"))
    monkeypatch.setattr(cli, "create_cardnews", lambda *_: pytest.fail("rewrote a delivery retry"))
    monkeypatch.setattr(cli, "mark_built", lambda *a, **kw: None)
    assert cli._build(tmp_path / "config.yaml", {}, False, True, str(snapshot)) == base


def test_explicit_photo_upload_does_not_invoke_ai(monkeypatch, tmp_path):
    value, template, base = baseline(tmp_path)
    photo = tmp_path / "replacement.jpg"
    Image.new("RGB", (900, 1600), "red").save(photo)
    value.slides[1].image_url = str(photo)
    monkeypatch.setattr(render, "_transform_image_with_fallback", lambda *_: pytest.fail("unrequested AI edit"))
    paths = render.render_hybrid(value, template, tmp_path / "revision", image_transform={"enabled": True},
        revision={"edit_mode": "slide", "slide_number": 2, "base_cardnews": str(base / "cardnews.json"), "reference_image": str(photo)})
    assert len(paths) == 3
    for path in paths:
        with Image.open(path) as image:
            assert image.size == (1080, 1440)


def test_missing_replacement_cannot_silently_use_cover(monkeypatch, tmp_path):
    value, template, base = baseline(tmp_path)
    value.slides[1].image_url = "missing.jpg"
    calls = []
    monkeypatch.setattr(render, "_download_cover_image", lambda url, *_: calls.append(url))
    with pytest.raises(RuntimeError, match="다운로드 실패"):
        render.render_hybrid(value, template, tmp_path / "revision", revision={
            "edit_mode": "slide", "slide_number": 2,
            "base_cardnews": str(base / "cardnews.json"), "reference_image": "missing.jpg"})
    assert calls == ["missing.jpg"]


def test_identical_active_build_is_reused_but_different_article_is_queued(monkeypatch, tmp_path):
    from cardnews_auto import dashboard_jobs as jobs
    from types import SimpleNamespace
    rows = []
    monkeypatch.setattr(jobs, "JOB_INPUT_DIR", tmp_path)
    monkeypatch.setattr(jobs, "list_jobs", lambda *a, **kw: list(rows))
    monkeypatch.setattr(jobs, "add_job", lambda row: rows.append(dict(row)))
    monkeypatch.setattr(jobs.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    profile = {"id": "test", "name": "Test"}
    article = Article(title="First", url="https://example.com/1")
    first = jobs.enqueue_job(profile, "build", article=article, deduplicate=False)
    second = jobs.enqueue_job(profile, "build", article=article, deduplicate=False)
    third = jobs.enqueue_job(profile, "build", article=Article(title="Second", url="https://example.com/2"), deduplicate=False)
    assert first["id"] == second["id"]
    assert second["deduplicated"]
    assert third["id"] != first["id"]
    assert len(rows) == len(list(tmp_path.glob("*.json"))) == 2
