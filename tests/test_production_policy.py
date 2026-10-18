import copy
import hashlib
from pathlib import Path

import pytest
import yaml

from cardnews_auto import production_policy as policy
from cardnews_auto.writer import _title_options


def test_active_studio_versions_ignore_archived_capacities_and_are_immutable(tmp_path, monkeypatch):
    from cardnews_auto.template_publication import bundle_hash
    monkeypatch.setattr(policy, "POLICY_PATH", tmp_path / "image-policy.yaml")
    active = {"profile_id": "a"}
    for role, field in (("cover", "title"), ("body", "body")):
        bundle = tmp_path / "templates/channels/a/items" / role / "versions/v1"
        bundle.mkdir(parents=True)
        (bundle / "template.json").write_text(role, encoding="utf-8")
        active[role] = {"renderer": "template-lab", "bundle_path": str(bundle), "bundle_sha256": bundle_hash(bundle),
                        "layers": {field: "copy"}, "image_layer": "photo", "text_options": {field: {"max_chars": 80}}}
    cfg = {"magazine":{"id":"a"}, "template":{"approved_set":active,
        "template_variants":{"body":[{"text_options":{"body":{"max_chars":4}}}]}, "_version_snapshot": True}}
    cfg["template"] = policy.approved_template(cfg)
    assert _title_options(cfg, "body", 96, 3, "body")["max_chars"] == 80
    (Path(active["cover"]["bundle_path"]) / "template.json").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="변경"):
        policy.approved_template(cfg)
    active["cover"] = {"renderer": "photoshop", "psd_path": "old.psd"}
    with pytest.raises(RuntimeError, match="템플릿 제작"):
        policy.approved_template(cfg)


def test_shared_image_policy_overrides_channel_settings_without_touching_design():
    a = policy.apply_image_policy({"template":{"name":"hiphop"}, "image_transform":{"model":"other", "enabled":False}})
    b = policy.apply_image_policy({"template":{"name":"football"}, "image_transform":{"prompt":"channel prompt"}})
    assert a["image_transform"] == b["image_transform"]
    assert a["image_sources"] == b["image_sources"]
    assert a["image_transform"]["model"] == "grok-imagine-image-2.0"
    assert a["image_transform"]["recompose_for_publication"] is True
    assert a["template"] != b["template"]


def test_new_channel_inherits_common_policy(tmp_path, monkeypatch):
    from cardnews_auto.dashboard_core import write_profile_config, ProfileInput
    from cardnews_auto import dashboard_core
    monkeypatch.setattr(dashboard_core, 'ROOT', tmp_path)
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump({'generation': {}, 'brand': {}, 'news': {}}), encoding='utf-8')
    path = write_profile_config({"id":"a", "config_path":str(tmp_path / "config.yaml")}, ProfileInput(
        name="A", category="music", image_transform_enabled=False, image_prompt="ignored",
        web_image_search_enabled=False, rss_image_enabled=False,
        cover_prompt="ignored", body_prompt="ignored", caption_prompt="ignored",
        pages=["https://example.com/news"], feeds=["https://example.com/rss"],
        schedule_enabled=True, collection_times=["08:30"]))
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    common = policy.apply_image_policy({})
    assert cfg["image_transform"] == common["image_transform"]
    assert cfg["image_sources"]["web_search"]["enabled"] is True
    assert cfg["image_sources"]["rss"]["enabled"] is True
    assert cfg["image_sources"]["rss"]["same_site_only"] is False
    from cardnews_auto.writer import DEFAULT_BODY_PROMPT, DEFAULT_COVER_PROMPT, DEFAULT_CAPTION_PROMPT
    assert cfg["generation"]["body_prompt"] == DEFAULT_BODY_PROMPT
    assert cfg["generation"]["cover_prompt"] == DEFAULT_COVER_PROMPT
    assert cfg["generation"]["caption_prompt"] == DEFAULT_CAPTION_PROMPT
    assert cfg["news"]["pages"][0]["url"] == "https://example.com/news"
    assert cfg["news"]["feeds"] == ["https://example.com/rss"]
    assert cfg["schedule"]["enabled"] is True
    assert cfg["schedule"]["times"] == ["08:30"]
    assert "approved_set" not in cfg["template"]
    with pytest.raises(RuntimeError, match="승인된 템플릿"):
        policy.approved_template(cfg)


def test_legacy_retry_gets_current_approved_template(monkeypatch):
    from cardnews_auto import dashboard_jobs as jobs
    current = {"template":{"approved_set":{"profile_id":"a","cover":{},"body":{}}}, "image_transform":{}}
    monkeypatch.setattr(jobs, "get_job", lambda _: {"id":"old","profile_id":"a","action":"build","status":"failed"})
    monkeypatch.setattr(jobs, "profile_config", lambda _: copy.deepcopy(current))
    monkeypatch.setattr(jobs, "build_snapshot", lambda _: {"build_config":{"template":{}}, "article":{"title":"test","url":"https://example.com","source":"test"}})
    captured = {}
    monkeypatch.setattr(jobs, "enqueue_job", lambda *a, **kw: captured.update(kw) or {"id":"new"})
    jobs.retry_job("old", {"id":"a"})
    assert captured["snapshot_extra"]["build_config"]["template"] == current["template"]


def test_rewrite_uses_frozen_limits_and_rejects_overflow(monkeypatch):
    from types import SimpleNamespace
    from cardnews_auto import writer
    from cardnews_auto.models import Slide, Article
    captured = {}
    monkeypatch.setattr(writer, "OpenAI", lambda **kw: SimpleNamespace(responses=SimpleNamespace(parse=None)))
    def respond(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=writer.RevisedText(title="반복 제목", body="너무 긴 문장입니다. " * 20))
    monkeypatch.setattr(writer, "metered_call", respond)
    spec = {"layers":{"body":"original copy"}, "text_options":{"body":{"max_chars":20,"line_chars":10,"max_lines":2}}}
    with pytest.raises(RuntimeError):
        writer.rewrite_slide_text(Slide(role="body",title="",body="원문입니다."), "수정", Article(title="news",url="https://example.com"), {"generation":{"model":"test"}}, spec)
    assert '"max_chars": 20' in captured["input"][1]["content"]
