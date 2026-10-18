import base64
from io import BytesIO
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from cardnews_auto import render
from cardnews_auto.models import CardNews, Slide


def response(size=(1200,1600), color="green"):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return NS(data=[NS(b64_json=base64.b64encode(buffer.getvalue()).decode())])


def card():
    return CardNews(topic="음악 차트 기록", rationale="", source_urls=[], caption="기사 설명", hashtags=[],
                    slides=[Slide(role="cover", title="음악 소식", body="", image_url="photo", image_prompt="old scene changes")])


def config():
    return {"publication_frame":"exact", "cover":{"renderer":"auto"}}


OPTIONS = {"enabled":True, "recompose_for_publication":True}


@pytest.mark.parametrize("size", [(1080,1440), (1800,2400)])
def test_publication_ready_source_is_also_washed(monkeypatch, tmp_path, size):
    source = tmp_path/"source.png"
    Image.new("RGB", size, "blue").save(source)
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: source)
    calls = []
    def edit(**kwargs):
        calls.append(kwargs)
        return response()
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(edit=edit)))
    files = render.render_hybrid(card(), config(), tmp_path/"out", image_transform=OPTIONS)
    assert Image.open(files[0]).size == (1080,1440)
    assert len(calls) == 1
    assert '"article_title"' in calls[0]['prompt']
    assert '"card_copy"' in calls[0]['prompt']


def test_landscape_reference_is_recomposed_without_story_scene_instructions(monkeypatch, tmp_path):
    source = tmp_path/"source.png"
    Image.new("RGB", (1600,900), "blue").save(source)
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: source)
    calls = []
    def edit(**kwargs):
        calls.append(kwargs)
        return response()
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(edit=edit)))
    render.render_hybrid(card(), config(), tmp_path/"out", image_transform=OPTIONS)
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-image-2.5-sunburst"
    assert calls[0]["size"] == "1536x2048"
    assert "old scene changes" not in calls[0]["prompt"]
    assert render.REFERENCE_RECOMPOSE_PROMPT in calls[0]["prompt"]


def test_blocked_reference_is_not_retried_and_independent_illustration_is_labeled(monkeypatch, tmp_path):
    source = tmp_path/"source.png"
    Image.new("RGB", (1600,900), "blue").save(source)
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: source)
    edits, generations = [], []
    def edit(**kwargs):
        edits.append(kwargs)
        raise RuntimeError("moderation_blocked")
    def generate(**kwargs):
        generations.append(kwargs)
        return response()
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(edit=edit, generate=generate)))
    value = card()
    render.render_hybrid(value, config(), tmp_path/"out", image_transform=OPTIONS)
    assert len(edits) == len(generations) == 1
    assert "image" not in generations[0] and "No people, faces" in generations[0]["prompt"]
    assert "old scene changes" not in generations[0]["prompt"]
    assert value.slides[0].image_provider == "openai_generated_illustration"
    assert value.slides[0].image_url == ""
    assert "AI 생성 설명 이미지: 1번" in value.caption


def test_missing_source_generates_illustration_without_edit_call(monkeypatch, tmp_path):
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: None)
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(generate=lambda **_: response())))
    value = card()
    render.render_hybrid(value, config(), tmp_path/"out", image_transform=OPTIONS)
    assert value.slides[0].image_provider == "openai_generated_illustration"


def test_generation_cache_accepts_other_valid_size(monkeypatch, tmp_path):
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return response((1200,1600))
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(generate=generate)))
    value = card()
    path = render._generate_news_illustration(value, value.slides[0], 0, OPTIONS, tmp_path)
    assert Image.open(path).size == (1200,1600)
    assert render._generate_news_illustration(value, value.slides[0], 0, OPTIONS, tmp_path) == path
    assert len(calls) == 1


def test_generation_moderation_is_propagated_without_retries(monkeypatch, tmp_path):
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("moderation_blocked")
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(generate=generate)))
    value = card()
    with pytest.raises(RuntimeError, match="moderation_blocked"):
        render._generate_news_illustration(value, value.slides[0], 0, OPTIONS, tmp_path)
    assert len(calls) == 1


def test_generation_wrong_ratio_retries_once(monkeypatch, tmp_path):
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return response((1024,1536))
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(generate=generate)))
    value = card()
    with pytest.raises(render.ImageSizeMismatch):
        render._generate_news_illustration(value, value.slides[0], 0, OPTIONS, tmp_path)
    assert len(calls) == 2
    assert not list(tmp_path.glob('*.png'))
