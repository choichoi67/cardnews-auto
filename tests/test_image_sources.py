from types import SimpleNamespace
import pytest

from cardnews_auto.image_sources import (
    _official_rank,
    _openai_image_search,
    _relevant_image,
    assign_slide_images,
)
from cardnews_auto.models import Article, CardNews, Slide


@pytest.fixture(autouse=True)
def isolate_query_planning(monkeypatch):
    # Query planning has its own mocked integration tests.
    monkeypatch.setattr("cardnews_auto.image_sources._rewrite_image_queries", lambda *_: None)


def _card() -> CardNews:
    return CardNews(
        topic="Topic",
        rationale="Why",
        source_urls=["https://example.com/story"],
        slides=[
            Slide(role="cover", title="Cover", body="Lead", image_query="artist cover"),
            Slide(role="body", title="Body", body="Explanation", image_query="artist concert"),
            Slide(role="cta", title="CTA", body="Follow"),
        ],
        caption="Caption",
        hashtags=["news"],
    )


def test_assigns_distinct_rss_images(monkeypatch):
    monkeypatch.setattr(
        "cardnews_auto.image_sources.discover_article_images",
        lambda *_args, **_kwargs: [
            {
                "url": "https://cdn.example.com/one.jpg",
                "page_url": "https://example.com/story",
                "provider": "rss_article",
                "caption": "Artist cover",
            },
            {
                "url": "https://cdn.example.com/two.jpg",
                "page_url": "https://example.com/story",
                "provider": "rss_article",
                "caption": "Artist concert",
            },
        ],
    )
    monkeypatch.setattr(
        "cardnews_auto.image_sources._openai_image_search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("RSS images are sufficient")),
    )
    card = _card()
    audit = assign_slide_images(
        card,
        Article(title="Story", url="https://example.com/story"),
        {
            "web_search": {"enabled": False},
            "rss": {"enabled": True, "same_site_only": True},
        },
    )
    assert card.slides[0].image_url.endswith("one.jpg")
    assert card.slides[1].image_url.endswith("two.jpg")
    assert len({card.slides[0].image_url, card.slides[1].image_url}) == 2
    assert len(audit) == 2


def test_body_searches_its_scene_instead_of_blindly_using_gallery(monkeypatch):
    monkeypatch.setattr(
        "cardnews_auto.image_sources.discover_article_images",
        lambda *_args, **_kwargs: [
            {
                "url": "https://publisher.example/hero.jpg",
                "page_url": "https://publisher.example/story",
                "provider": "rss_article",
                "caption": "",
            },
            {
                "url": "https://publisher.example/action.jpg",
                "page_url": "https://publisher.example/story",
                "provider": "rss_article",
                "caption": "",
            },
        ],
    )
    monkeypatch.setattr(
        "cardnews_auto.image_sources._openai_image_search",
        lambda query, *_args, **_kwargs: [{"url": "https://publisher.example/concert.jpg", "page_url": "https://example.com/official", "caption": "artist concert"}],
    )
    card = _card()

    assign_slide_images(
        card,
        Article(title="Football story", url="https://publisher.example/story"),
        {
            "web_search": {
                "enabled": True,
                "official_only": True,
                "article_gallery_first": True,
            },
            "rss": {"enabled": True, "same_site_only": True},
        },
    )

    assert card.slides[0].image_url.endswith("hero.jpg")
    assert card.slides[1].image_url.endswith("concert.jpg")


def test_unrelated_recommended_article_image_is_rejected():
    item = {
        "url": "https://www.hotnewhiphop.com/imgprst/2292x1200/E-40.jpg",
        "page_url": "https://www.hotnewhiphop.com/50-cent-eminem-tupac-story",
        "provider": "rss_article",
        "caption": "2023 Essence Festival Of Culture",
    }

    assert not _relevant_image(item, "50 Cent Balrog Street Fighter training")
    assert not _relevant_image(item, "Eminem 50 Cent Shady Records photo")


def test_openai_web_search_returns_image_urls(monkeypatch):
    captured = {}
    client_options = {}
    class Result:
        type = "web_search_call"

        def model_dump(self):
            return {
                "type": "web_search_call",
                "results": [{
                    "type": "image_result",
                    "image_url": "https://cdn.example.com/photo.jpg",
                    "source_website_url": "https://example.com/story",
                    "caption": "Artist on stage",
                }],
            }

    fake = SimpleNamespace(
        responses=SimpleNamespace(
            parse=lambda **kwargs: captured.update(kwargs) or SimpleNamespace(output=[Result()])
        )
    )
    monkeypatch.setattr("cardnews_auto.image_sources.OpenAI", lambda **kwargs: client_options.update(kwargs) or fake)
    results = _openai_image_search("artist concert", "gpt-5.6", 3)
    assert captured["max_tool_calls"] == 1
    assert client_options["max_retries"] == 1
    assert results == [{
        "url": "https://cdn.example.com/photo.jpg",
        "page_url": "https://example.com/story",
        "provider": "openai_web_search",
        "caption": "Artist on stage",
    }]


def test_null_tool_captions_use_grounded_selection_and_reject_invented_urls(monkeypatch):
    from cardnews_auto.image_sources import ImageSearchSelection, ImageSearchCandidate
    url = "https://publisher.example/assets/DSC2915.jpg"
    row = {"type": "image_result", "image_url": url, "caption": None,
           "source_website_url": "https://publisher.example/doechii-festival"}
    response = SimpleNamespace(output=[{"type": "web_search_call", "results": [row]}],
        output_parsed=ImageSearchSelection(images=[
            ImageSearchCandidate(url=url, caption="Doechii performs live on stage at a festival."),
            ImageSearchCandidate(url=url, caption="Repeated result"),
            ImageSearchCandidate(url="https://invented.example/fake.jpg", caption="Doechii live stage"),
        ]))
    monkeypatch.setattr("cardnews_auto.image_sources.OpenAI", lambda **_: SimpleNamespace(
        responses=SimpleNamespace(parse=lambda **kwargs: response)))
    query = "Doechii live performance photo microphone stage"
    assert not _relevant_image({"url": url, "page_url": row["source_website_url"],
                                "caption": "", "provider": "openai_web_search"}, query)
    results = _openai_image_search(query, "gpt-6-luna", 6)
    assert len(results) == 1 and results[0]["url"] == url
    assert _relevant_image(results[0], query)
    assert not _relevant_image(results[0], "FCI Fort Dix prison exterior")


def test_rejected_candidates_are_recorded_in_search_audit(monkeypatch):
    monkeypatch.setattr("cardnews_auto.image_sources._openai_image_search", lambda *_: [
        {"url": "https://example.com/random.jpg", "caption": "Unrelated person"}])
    audit = assign_slide_images(_card(), Article(title="Story", url="https://example.com"),
                               {"rss": {"enabled": False}})
    assert audit[1]["status"] == "no_image"
    assert audit[1]["search_candidates"][0]["rejection"] == "unrelated"


def test_archive_recovery_keeps_real_tool_candidates_when_selection_is_empty(monkeypatch):
    from cardnews_auto.image_sources import ImageSearchSelection
    response = SimpleNamespace(output=[{"type": "web_search_call", "results": [
        {"type": "image_result", "image_url": "https://example.com/123.jpg", "caption": None,
         "source_website_url": "https://example.com/doechii-interview"},
        {"type": "image_result", "image_url": "https://example.com/other.jpg", "caption": "Other artist"},
    ]}], output_parsed=ImageSearchSelection(images=[]))
    monkeypatch.setattr("cardnews_auto.image_sources.OpenAI", lambda **_: SimpleNamespace(
        responses=SimpleNamespace(parse=lambda **kwargs: response)))
    assert _openai_image_search("Doechii metallic video", "test", 6) == []
    results = _openai_image_search("Doechii portrait", "test", 6, "subject_context")
    assert len(results) == 2
    assert [r["url"] for r in results if _relevant_image(r, "Doechii")] == ["https://example.com/123.jpg"]


def test_image_intent_keeps_article_hero_for_cover_and_never_touches_cta(monkeypatch):
    monkeypatch.setattr(
        "cardnews_auto.image_sources.discover_article_images",
        lambda *_args, **_kwargs: [],
    )
    calls = []

    def fake_search(query, _model, _limit, intent="context"):
        calls.append((query, intent))
        return [{
            "url": f"https://images.example.com/{intent}-{len(calls)}.jpg",
            "page_url": "https://example.com/story",
            "provider": "openai_web_search",
            "caption": intent,
        }]

    monkeypatch.setattr("cardnews_auto.image_sources._openai_image_search", fake_search)
    slides = [
        Slide(role="cover", title="Cover", body="Lead", image_query="main subject"),
        Slide(role="body", title="Hook one", body="Reaction", image_query="subject reaction"),
        Slide(role="body", title="Hook two", body="Action", image_query="subject action"),
        Slide(role="body", title="Context", body="Explanation", image_query="specific location"),
        Slide(role="cta", title="CTA", body="Follow", image_url="https://bad.example/replace.jpg"),
    ]
    card = CardNews(
        topic="Topic",
        rationale="Why",
        source_urls=["https://example.com/story"],
        slides=slides,
        caption="Caption",
        hashtags=["news"],
    )
    article = Article(
        title="Korean title",
        original_title="Exact English story title",
        url="https://example.com/story",
        image_url="https://cdn.example.com/article-hero.jpg",
    )

    audit = assign_slide_images(
        card,
        article,
        {
            "web_search": {"enabled": True},
            "rss": {"enabled": True, "same_site_only": True},
        },
    )

    assert card.slides[0].image_url == article.image_url
    assert [intent for _query, intent in calls] == ["hook", "hook", "context"]
    assert all("Exact English story title" in query for query, _intent in calls)
    assert card.slides[-1].image_url == ""
    assert [item["intent"] for item in audit] == ["cover", "hook", "hook", "context"]


def test_transfer_rumor_uses_official_story(monkeypatch):
    monkeypatch.setattr(
        "cardnews_auto.image_sources.discover_article_images",
        lambda *_args, **_kwargs: [],
    )
    calls = []

    def fake_search(query, _model, _limit, intent="context"):
        calls.append(intent)
        return [{
            "url": "https://www.arsenal.com/player.jpg",
            "page_url": "https://www.arsenal.com/news/marcus-rashford-transfer",
            "provider": "openai_web_search",
            "caption": "Marcus Rashford Arsenal transfer",
        }]

    monkeypatch.setattr("cardnews_auto.image_sources._openai_image_search", fake_search)
    card = CardNews(
        topic="transfer",
        rationale="rumor",
        source_urls=["https://example.com/story"],
        slides=[
            Slide(
                role="cover",
                title="Rashford transfer",
                body="market",
                image_query="Marcus Rashford Arsenal transfer",
            ),
            Slide(role="cta", title="", body=""),
        ],
        caption="",
        hashtags=[],
    )

    assign_slide_images(
        card,
        Article(title="Marcus Rashford Arsenal transfer", url="https://example.com/story"),
        {
            "web_search": {
                "enabled": True,
                "official_only": True,
                "max_queries_per_article": 1,
            },
            "rss": {"enabled": False},
        },
    )

    assert calls == ["cover"]
    assert card.slides[0].image_page_url.startswith("https://www.arsenal.com/")
    assert _official_rank(
        {"page_url": "https://www.espn.com/story", "caption": "official"},
        "Arsenal transfer",
    ) == 2


@pytest.mark.parametrize("failed", [False, True])
def test_initial_search_and_all_recovery_paths_share_one_attempt(monkeypatch, failed):
    from cardnews_auto.image_sources import search_alternative_images
    calls = []
    def search(*args):
        calls.append(args)
        if failed:
            raise TimeoutError()
        return []
    monkeypatch.setattr("cardnews_auto.image_sources._openai_image_search", search)
    card = _card()
    article = Article(title="Artist", url="https://example.com", image_url="https://example.com/hero.jpg")
    cfg = {"rss": {"enabled": False}}
    assign_slide_images(card, article, cfg)
    assert len(calls) == 1
    for publication_ready in [False, False, True]:
        assert search_alternative_images(card.slides[1], article, cfg, [], publication_ready=publication_ready) == []
    assert len(calls) == 1
    # The RSS cover has not searched, so it gets its one attempt if needed.
    try:
        search_alternative_images(card.slides[0], article, cfg, [])
    except TimeoutError:
        pass
    assert search_alternative_images(card.slides[0], article, cfg, []) == []
    assert len(calls) == 2
    assert "_image_search_attempted" not in card.slides[0].model_dump()


def test_search_cache_reuses_candidates_but_new_context_and_corrupt_cache_search_again(monkeypatch, tmp_path):
    calls = []
    def search(*args):
        calls.append(args)
        return [{"url": "https://example.com/concert.jpg", "caption": "artist concert", "provider": "openai_web_search"}]
    monkeypatch.setattr("cardnews_auto.image_sources._openai_image_search", search)
    cfg = {"rss": {"enabled": False}, "_search_cache_dir": tmp_path}
    article = Article(title="Artist", url="https://example.com", image_url="https://example.com/hero.jpg")
    assign_slide_images(_card(), article, cfg)
    cached_card = _card()
    assign_slide_images(cached_card, article, cfg)
    assert len(calls) == 1
    assert cached_card.slides[1].image_url.endswith("concert.jpg")
    assert not cached_card.slides[1]._image_search_attempted
    for path in tmp_path.glob("*.json"):
        path.write_text('{broken', encoding="utf-8")
    assign_slide_images(_card(), article, cfg)
    assert len(calls) == 2
    changed = _card()
    changed.slides[1].body = "A different event"
    assign_slide_images(changed, article, cfg)
    assert len(calls) == 3
