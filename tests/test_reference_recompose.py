import base64
import json
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


def test_black_source_and_black_generated_result_are_rejected(tmp_path):
    source = tmp_path / "black.png"
    Image.new("RGB", (1080, 1440), "black").save(source)
    assert render._download_cover_image(str(source), tmp_path / "cache") is None
    with pytest.raises(OSError, match="검은 이미지"):
        render._save_image_response(response(color="black"), tmp_path / "generated.png")
    assert not (tmp_path / "generated.png").exists()


def test_dark_photo_with_visible_subject_is_allowed(tmp_path):
    source = tmp_path / "night.png"
    photo = Image.new("RGB", (1080, 1440), "black")
    photo.paste("white", (100, 100, 200, 200))
    photo.save(source)
    assert render._download_cover_image(str(source), tmp_path / "cache") is not None


OPTIONS = {"enabled":True, "recompose_for_publication":True}


def test_card_prompt_limits_context_and_leaves_template_and_copy_unchanged():
    value = card()
    value.slides = [
        Slide(role='cover', title='먼 표지', body=''),
        Slide(title='', body='앞 카드 활동 소개'),
        Slide(title='', body='현재 카드 새 작업 준비', image_prompt='old scene changes'),
        Slide(title='', body='다음 카드 공개 계획'),
        Slide(role='cta', title='고정 CTA', body='브랜드 문구'),
    ]
    spec = {'text_options': {'body': {'allowed_bounds': [20, 1000, 1060, 1400]}}, 'template_id': 'unchanged'}
    original_card, original_spec = value.model_dump_json(), json.dumps(spec)
    prompt = render.card_image_prompt(value, 2, spec, render.REFERENCE_RECOMPOSE_PROMPT, '작업실 자료사진')
    context = json.loads(prompt.split('Reference context JSON:\n', 1)[1].split('\n\n', 1)[0])
    assert context['card_copy'] == '현재 카드 새 작업 준비'
    assert context['previous_card'] == '앞 카드 활동 소개'
    assert context['next_card'] == '다음 카드 공개 계획'
    assert context['text_areas']['body'] == [20, 1000, 1060, 1400]
    assert '먼 표지' not in prompt and '고정 CTA' not in prompt
    assert 'old scene changes' not in prompt
    assert prompt.count(render.CARD_CONTEXT_PROMPT) == 1
    assert value.model_dump_json() == original_card and json.dumps(spec) == original_spec
    last = render.card_image_prompt(value, 3, spec, '', edit_request='피사체를 크게')
    assert '피사체를 크게' in last and '브랜드 문구' not in last
    first = render.card_image_prompt(value, 0, spec, '')
    assert '"previous_card": ""' in first


@pytest.mark.parametrize("kind", ["data", "evidence"])
def test_legacy_image_kind_does_not_block_ai_edit(monkeypatch, tmp_path, kind):
    source = tmp_path / "source.png"
    Image.new("RGB", (1080, 1440), "blue").save(source)
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: source)
    edits = []
    monkeypatch.setattr(render, "_transform_image_with_fallback", lambda path, *_: edits.append(path) or path)
    value = card()
    value.slides[0].image_kind = kind
    output = tmp_path / "out"
    render.render_hybrid(value, config(), output, image_transform=OPTIONS)
    manifest = json.loads((output / "render_manifest.json").read_text(encoding="utf-8"))
    assert manifest["slides"][0]["image_status"] == "edited"
    assert edits == [source]


def test_legacy_image_kind_without_original_uses_normal_search(monkeypatch):
    from cardnews_auto import image_sources
    from cardnews_auto.models import Article
    value = card()
    value.slides[0].image_kind = "data"
    value.slides[0].image_url = ""
    monkeypatch.setattr(image_sources, "_openai_image_search", lambda *_: [{"url": "https://example.com/photo.jpg"}])
    image_sources.assign_slide_images(value, Article(title="통계", url="https://example.com"),
                                     {"web_search": {"enabled": True}, "rss": {"enabled": False}})
    assert value.slides[0].image_url == "https://example.com/photo.jpg"


def test_legacy_data_image_with_wrong_ratio_can_be_edited(monkeypatch, tmp_path):
    source = tmp_path / "chart.png"
    Image.new("RGB", (1600, 900), "blue").save(source)
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: source)
    edits = []
    def edit(**kwargs):
        edits.append(kwargs)
        return response()
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(edit=edit)))
    value = card()
    value.slides[0].image_kind = "data"
    render.render_hybrid(value, config(), tmp_path / "out", image_transform=OPTIONS)
    assert len(edits) == 1
    assert "image_kind" not in edits[0]["prompt"]
    assert "image_kind" not in Slide.model_json_schema()["properties"]
    assert Image.open(source).size == (1600, 900)


def test_card_prompt_caps_neighbor_text():
    value = card()
    value.slides += [Slide(title='', body='긴 설명' * 500)]
    prompt = render.card_image_prompt(value, 0, {}, '')
    context = json.loads(prompt.split('Reference context JSON:\n', 1)[1].split('\n\n', 1)[0])
    assert len(context['next_card']) == 240


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
    assert render.CARD_CONTEXT_PROMPT in calls[0]['prompt']
    assert '"previous_card": ""' in calls[0]['prompt']


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


def test_blocked_reference_uses_original_without_generation(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (1600, 900), "blue").save(source)
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: source)
    edits = []
    def edit(**kwargs):
        edits.append(kwargs)
        raise RuntimeError("moderation_blocked")
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(edit=edit)))
    value = card()
    files = render.render_hybrid(value, config(), tmp_path / "out", image_transform=OPTIONS)
    assert len(edits) == 1
    assert value.slides[0].image_url == "photo"
    assert Image.open(files[0]).size == (1080, 1440)
    assert "generated_illustration" not in value.slides[0].image_provider


def test_missing_reference_searches_its_body_until_photo_found(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (1200, 1600), "blue").save(source)
    monkeypatch.setattr(render, "_download_cover_image", lambda url, *_: source if url == "body-photo" else None)
    monkeypatch.setattr(render, "OpenAI", lambda **_: NS(images=NS(edit=lambda **_: response())))
    value = card()
    value.slides = [Slide(role="body", title="Training", body="The team returned to training.")]
    calls = []
    def recover(slide, excluded):
        calls.append(slide.body)
        return [{"url": "body-photo", "provider": "web"}] if len(calls) == 3 else []
    files = render.render_hybrid(value, {**config(), "body": {"renderer": "auto"}}, tmp_path / "out",
                                 image_transform=OPTIONS, image_recovery=recover)
    assert len(files) == 1
    assert calls == [value.slides[0].body] * 3
    assert value.slides[0].image_url == "body-photo"
    assert not hasattr(render, "_generate_news_illustration")
