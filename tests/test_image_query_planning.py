import json
from types import SimpleNamespace

import pytest

from cardnews_auto import image_sources as source
from cardnews_auto.models import Article, CardNews, Slide


def article_and_card():
    article = Article(title="디디 병원 이송", original_title="Sean Combs Diddy hospitalization",
                      url="https://example.com/story", image_url="https://example.com/hero.jpg",
                      summary="디디가 머리 부상 치료 뒤 FCI Fort Dix 교도소로 돌아왔다.")
    card = CardNews(topic=article.title, rationale="", source_urls=[article.url], caption="", hashtags=[], slides=[
        Slide(role="cover", title="디디 병원 이송", body="", image_query="Diddy news portrait"),
        Slide(body="디디가 머리 부상 치료를 받았다.", title="",
              image_query="Sean Combs Diddy head injury hospitalization report portrait", image_prompt="기존 사진 편집 지시"),
        Slide(body="디디는 교도소에 돌아왔다.", title="",
              image_query="Sean Combs Diddy prison return hospitalization news photo"),
    ])
    return article, card


def result(cards=None):
    return SimpleNamespace(output_parsed=source.CardImageQueryPlan(cards=cards if cards is not None else [
        source.CardImageQuery(slide_number=3, query="FCI Fort Dix New Jersey prison exterior", visual_focus="기사에 나온 교도소 외관. 실제 복귀 순간 사진은 아님."),
        source.CardImageQuery(slide_number=2, query="Sean Combs Diddy press conference portrait", visual_focus="부상 장면을 꾸미지 않고 기사 인물의 별도 기자회견 사진 사용"),
    ]))


def test_joint_plan_rewrites_similar_queries_and_searches_the_new_targets(monkeypatch):
    article, card = article_and_card()
    before = [(s.title, s.body, s.image_prompt) for s in card.slides]
    plans, searches = [], []
    def parse(**request):
        plans.append(request)
        return result()
    monkeypatch.setattr(source, "OpenAI", lambda **_: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(source, "discover_article_images", lambda *_: [])
    def search(query, *_):
        searches.append(query)
        target = query.splitlines()[0]
        name = "prison" if target.startswith("FCI") else "portrait"
        return [{"url": f"https://example.com/{name}.jpg", "caption": target, "provider": "openai_web_search"}]
    monkeypatch.setattr(source, "_openai_image_search", search)
    audit = source.assign_slide_images(card, article, {})
    assert len(plans) == 1 and len(searches) == 2
    payload = json.loads(plans[0]["input"][1]["content"])
    assert len(payload["body_cards"]) == 2 and "FCI Fort Dix" in payload["article"]["context"]
    assert [(s.title, s.body, s.image_prompt) for s in card.slides] == before
    assert card.slides[0].image_query == "Diddy news portrait"
    assert card.slides[2].image_url.endswith("prison.jpg")
    assert "실제 복귀 순간" in card.slides[2].image_search_focus
    assert len({s.image_url for s in card.slides}) == 3
    assert audit[-1]["query"].startswith("FCI Fort Dix")
    assert audit[-1]["original_query"].startswith("Sean Combs Diddy prison")
    assert all("Other cards' searches:" in query for query in searches)
    assert "image_search_focus" not in CardNews.model_json_schema()["$defs"]["Slide"]["properties"]


@pytest.mark.parametrize("invalid", ["partial", "duplicate", "wrong_index", "timeout"])
def test_failed_plan_does_not_partially_change_queries(monkeypatch, invalid):
    article, card = article_and_card()
    before = card.model_dump()
    def parse(**_):
        if invalid == "timeout":
            raise TimeoutError()
        response = result()
        if invalid == "partial":
            response.output_parsed.cards.pop()
        elif invalid == "duplicate":
            response.output_parsed.cards[1].query = response.output_parsed.cards[0].query.upper()
        else:
            response.output_parsed.cards[0].slide_number = 99
        return response
    monkeypatch.setattr(source, "OpenAI", lambda **_: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    source._rewrite_image_queries(card, article, "test")
    assert card.model_dump() == before


def test_plan_quota_error_remains_explicit(monkeypatch):
    def parse(**_):
        raise RuntimeError("insufficient_quota")
    monkeypatch.setattr(source, "OpenAI", lambda **_: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    article, card = article_and_card()
    with pytest.raises(source.ImageSearchQuotaError):
        source._rewrite_image_queries(card, article, "test")


def test_recovery_sees_other_queries_and_excludes_used_images(monkeypatch):
    article, card = article_and_card()
    slide = card.slides[2]
    slide.image_query = "FCI Fort Dix exterior"
    requests = []
    monkeypatch.setattr(source, "_openai_image_search", lambda query, *_: requests.append(query) or [])
    source.search_alternative_images(slide, article, {}, ["https://example.com/used.jpg"],
                                     other_queries=[card.slides[1].image_query])
    assert card.slides[1].image_query in requests[0]
    assert "used.jpg" in requests[0] and "Reformulate the search" in requests[0]
