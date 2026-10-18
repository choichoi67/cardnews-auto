from types import SimpleNamespace as NS

import pytest

from cardnews_auto import image_sources as source, render
from cardnews_auto.models import Article, CardNews, Slide


def story():
    return (Article(title="Doechii 데뷔 앨범", original_title="Doechii debut album",
                    url="https://example.com/article"),
            Slide(title="", body="도이치의 영상에 금속 의상과 불꽃 장식이 등장한다.",
                  image_query="Doechii metallic costume sparking chest ornament BOMBS video",
                  image_search_focus="금속 의상 불꽃 장식 클로즈업"))


def test_recovery_replaces_failed_visual_target_without_changing_copy(monkeypatch):
    article, slide = story()
    requests = []
    plan = source.RecoveryImageQuery(subject="Doechii", query="Doechii concert performance",
                                    visual_focus="별도 공연 자료사진이며 실제 영상 장면이 아님")
    monkeypatch.setattr(source, "OpenAI", lambda **_: NS(responses=NS(parse=lambda **_: NS(output_parsed=plan))))
    def search(query, *_):
        requests.append(query)
        return [
            {"url": "https://example.com/used.jpg", "caption": "Doechii concert performance"},
            {"url": "https://example.com/new.jpg", "caption": "Doechii onstage"},
            {"url": "https://example.com/archive.jpg", "caption": ""},
        ]
    monkeypatch.setattr(source, "_openai_image_search", search)
    before = slide.model_dump()
    results = source.search_alternative_images(slide, article, {}, ["https://example.com/used.jpg"])
    assert [r["url"] for r in results] == ["https://example.com/new.jpg", "https://example.com/archive.jpg"]
    assert results[0]["provider"].endswith(":subject_context")
    assert requests[0].startswith("Doechii concert performance")
    assert "금속 의상 불꽃 장식 클로즈업" not in requests[0]
    assert "BOMBS" not in requests[0]
    assert slide.model_dump() == before


@pytest.mark.parametrize("subject,query", [("Other Artist", "Other Artist concert"), ("Doechii", "Other Artist concert")])
def test_recovery_plan_cannot_invent_or_change_subject(monkeypatch, subject, query):
    article, slide = story()
    plan = source.RecoveryImageQuery(subject=subject, query=query, visual_focus="자료사진")
    monkeypatch.setattr(source, "OpenAI", lambda **_: NS(responses=NS(parse=lambda **_: NS(output_parsed=plan))))
    assert source._plan_recovery_query(slide, article, "test", []) is None


def test_recovery_keeps_full_person_name(monkeypatch):
    article = Article(title="Sean Combs Diddy prison return", url="https://example.com")
    slide = Slide(title="", body="", image_search_focus="병원", image_query="Sean Combs Diddy hospital")
    plan = source.RecoveryImageQuery(subject="Sean Combs Diddy", query="Sean Combs Diddy interview portrait", visual_focus="자료사진")
    monkeypatch.setattr(source, "OpenAI", lambda **_: NS(responses=NS(parse=lambda **_: NS(output_parsed=plan))))
    assert source._plan_recovery_query(slide, article, "test", []).subject == "Sean Combs Diddy"


def test_context_image_does_not_recreate_missing_video_scene(monkeypatch, tmp_path):
    from PIL import Image
    image = tmp_path / "photo.png"
    Image.new("RGB", (1080, 1440), "navy").save(image)
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: image)
    prompts = []
    def edit(path, options, *_):
        prompts.append(options["prompt"])
        return path
    monkeypatch.setattr(render, "_transform_image_with_fallback", edit)
    card = CardNews(topic="test", rationale="", source_urls=[], caption="설명", hashtags=[], slides=[
        Slide(role="cover", title="", body="", image_url="photo", image_provider="openai_web_search:subject_context",
              image_prompt="Add metallic outfit and sparking ornament")])
    render.render_hybrid(card, {"publication_frame": "exact", "cover": {"renderer": "auto"}},
                         tmp_path / "out", image_transform={"enabled": True})
    assert "Add metallic outfit" not in prompts[0]
    assert "archive context photograph" in prompts[0]
    assert "자료사진: 1번 카드" in card.caption
