import json

from PIL import Image

from cardnews_auto import image_sources, render, writer
from cardnews_auto.models import Article, CardNews, Slide


def test_last_card_is_searched_and_rendered_as_body_not_fixed_cta(monkeypatch, tmp_path):
    monkeypatch.setattr(image_sources, "_rewrite_image_queries", lambda *_: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    article = Article(title="가수의 새 소식", url="https://example.com/story",
                      image_url="https://example.com/hero.jpg")
    card = CardNews(topic=article.title, rationale="", source_urls=[article.url], caption="", hashtags=[], slides=[
        Slide(role="cover", title="새로운 소식\n오늘 공개", body="보조 설명", image_query="artist portrait"),
        Slide(title="반복 제목", body="가수가 새 앨범을 발표했습니다.", image_query="artist studio"),
        Slide(title="반복 제목", body="공연 일정도 함께 공개했습니다.", image_query="artist concert"),
    ])
    writer._normalize_editorial_cards(card, {"max_chars": 72, "line_chars": 24, "max_lines": 3})
    monkeypatch.setattr(image_sources, "discover_article_images", lambda *_: [])
    searches = []

    def search(query, *_):
        searches.append(query)
        scene = "studio" if "artist studio" in query.splitlines()[0] else "concert"
        return [{"url": f"https://example.com/{scene}.jpg", "page_url": article.url,
                 "caption": f"artist {scene}", "provider": "openai_web_search"}]

    monkeypatch.setattr(image_sources, "_openai_image_search", search)
    image_sources.assign_slide_images(card, article, {"web_search": {"enabled": True, "max_queries_per_article": 1}})
    assert len(searches) == 2  # Both body cards get a search, including the last.
    assert card.slides[0].image_url == article.image_url
    assert card.slides[-1].image_url.endswith("concert.jpg")
    assert all(s.title == "" and 2 <= len(s.body.splitlines()) <= 3 for s in card.slides[1:])
    sources = {}
    for name, color in [("hero", "red"), ("studio", "green"), ("concert", "blue")]:
        path = tmp_path / f"{name}.png"
        Image.new("RGB", (1080, 1440), color).save(path)
        sources[f"https://example.com/{name}.jpg"] = path
    monkeypatch.setattr(render, "_download_cover_image", lambda url, *_: sources[url])
    template = {role: {"renderer": "auto", "name": role} for role in ("cover", "body", "cta")}
    template["cta"]["preserve_original"] = True
    out = tmp_path / "result"
    images = render.render_hybrid(card, template, out, image_transform={"enabled": False})
    manifest = json.loads((out / "render_manifest.json").read_text(encoding="utf-8"))
    assert len(images) == 3
    assert [s["spec"]["name"] for s in manifest["slides"]] == ["cover", "body", "body"]
    assert manifest["slides"][-1]["source_url"].endswith("concert.jpg")
    assert len({s["source_sha256"] for s in manifest["slides"]}) == 3
