import hashlib
import json

import pytest
from PIL import Image

from cardnews_auto import cli, render
from cardnews_auto.models import CardNews, Slide


def test_prepared_images_skip_download_search_and_paid_edit(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (1080, 1440), "blue").save(source)
    card = CardNews(topic="test", rationale="", source_urls=[], caption="", hashtags=[],
                    slides=[Slide(role="cover", title="test", body="")])
    def forbidden(*args, **kwargs):
        pytest.fail("Prepared output must not search, download or regenerate")
    monkeypatch.setattr(render, "_download_cover_image", forbidden)
    monkeypatch.setattr(render, "_transform_cover_image", forbidden)
    files = render.render_hybrid(card, {"cover": {"renderer": "image"}}, tmp_path / "out",
        image_transform={"enabled": True}, image_recovery=forbidden, prepared_images={0: source})
    assert Image.open(files[0]).size == (1080, 1440)


@pytest.mark.parametrize("tampered", [False, True])
def test_resume_render_skips_writing_and_search_and_checks_hash(monkeypatch, tmp_path, tampered):
    source = tmp_path / "source.png"
    Image.new("RGB", (1080, 1440), "blue").save(source)
    card = CardNews(topic="test", rationale="", source_urls=[], caption="", hashtags=[],
                    slides=[Slide(role="cover", title="test", body="")])
    checkpoint = tmp_path / "output" / "checkpoint.json"
    checkpoint.parent.mkdir()
    checkpoint.write_text(json.dumps({"card": card.model_dump(), "images": [{"index": 0,
        "path": str(source), "sha256": "bad" if tampered else hashlib.sha256(source.read_bytes()).hexdigest()}]}))
    snapshot = tmp_path / "job.json"
    snapshot.write_text(json.dumps({"article": {"title": "test", "url": "https://example.com"},
                                   "resume_render": str(checkpoint)}))
    monkeypatch.setattr(cli, "approved_template", lambda cfg: {"cover": {}, "body": {}, "cta": {}})
    def forbidden(*args, **kwargs):
        pytest.fail("Resume must not write or search again")
    monkeypatch.setattr(cli, "create_cardnews", forbidden)
    monkeypatch.setattr(cli, "assign_slide_images", forbidden)
    monkeypatch.setattr(cli, "discover_article_image", forbidden)
    def reached_render(*args, **kwargs):
        assert kwargs["prepared_images"] == {0: source}
        raise RuntimeError("reached-render")
    monkeypatch.setattr(cli, "render_hybrid", reached_render)
    with pytest.raises(RuntimeError, match="변경" if tampered else "reached-render"):
        cli._build(tmp_path / "config.yaml", {}, False, True, str(snapshot))
