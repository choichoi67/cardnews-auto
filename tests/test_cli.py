import json
from pathlib import Path

import pytest
from cardnews_auto import cli
from cardnews_auto.build_progress import PREFIX, COLLECT_STAGES

from cardnews_auto.cli import (
    _build,
    _unique_output_dir,
    load_env,
)


def test_rebuild_uses_a_new_output_directory(tmp_path):
    config_path = tmp_path / "config.yaml"
    first = tmp_path / "output" / "2026-07-19_topic"
    first.mkdir(parents=True)

    rebuilt = _unique_output_dir(config_path, "2026-07-19_topic")

    assert rebuilt != first
    assert rebuilt.parent == first.parent
    assert rebuilt.name.startswith("2026-07-19_topic_")


def test_full_rebuild_loads_article_snapshot_json(monkeypatch, tmp_path):
    snapshot_path = tmp_path / "rebuild.json"
    snapshot_path.write_text(
        json.dumps({
            "article": {
                "title": "Drake ICEMAN",
                "url": "https://example.com/drake",
            },
            "candidate_id": "candidate-1",
            "job_id": "job-1",
            "revision": {"edit_mode": "regenerate", "instruction": "문장을 더 자연스럽게"},
        }),
        encoding="utf-8",
    )

    captured = {}

    def snapshot_was_loaded(_articles, cfg):
        captured.update(cfg)
        raise RuntimeError("snapshot-loaded")

    monkeypatch.setattr("cardnews_auto.cli.approved_template", lambda cfg: {})
    monkeypatch.setattr("cardnews_auto.cli.create_cardnews", snapshot_was_loaded)

    with pytest.raises(RuntimeError, match="snapshot-loaded"):
        _build(tmp_path / "config.yaml", {}, False, True, str(snapshot_path))

    generation = captured["generation"]
    assert generation["_revision_instruction"] == "문장을 더 자연스럽게"
    assert not {"cover_prompt", "body_prompt", "caption_prompt"} & generation.keys()


def test_cli_loads_project_env(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=test-key\nTELEGRAM_CHAT_ID=123\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    load_env(env_path)
    assert __import__("os").environ["OPENAI_API_KEY"] == "test-key"
    assert __import__("os").environ["TELEGRAM_CHAT_ID"] == "123"


def test_collect_emits_all_progress_stages(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(json.dumps({"news": {"category": "test", "summarize_in_korean": False}, "telegram": {"enabled": True}}), encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", ["cardnews-auto", "--config", str(config_path), "collect"])
    monkeypatch.setattr(cli, "collect_articles", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "send_review", lambda *args: [])
    cli.main()
    events = [json.loads(line[len(PREFIX):]) for line in capsys.readouterr().out.splitlines() if line.startswith(PREFIX)]
    assert [(event["stage"], event["status"]) for event in events] == [
        (stage, status) for stage in COLLECT_STAGES for status in ("running", "completed")
    ]
