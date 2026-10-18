from types import SimpleNamespace


def test_article_text_keeps_short_context_and_reports_unavailable(monkeypatch):
    from cardnews_auto import feeds
    monkeypatch.setattr(feeds, "_fetch_page", lambda _: SimpleNamespace(
        url="https://example.com/story", text='<meta name="description" content="요약만">'
        '<article><p>부상 때문이다.</p><p>회복 후<br>일정을 <strong>발표했다.</strong></p>'
        '<p>현재는 복귀했다.</p></article>'))
    text = feeds.discover_article_text("https://example.com/story")
    assert text == "부상 때문이다.\n\n회복 후 일정을 발표했다.\n\n현재는 복귀했다."
    assert "요약만" not in text
    monkeypatch.setattr(feeds, "_fetch_page", lambda _: None)
    assert feeds.discover_article_text("https://example.com/story") == ""

from cardnews_auto.feeds import (
    _PageContentParser,
    _extract_page_metadata,
    _high_resolution_image_url,
    discover_article_images,
)


def test_hotnewhiphop_thumbnail_is_upgraded_to_editorial_resolution():
    thumbnail = (
        "https://www.hotnewhiphop.com/i/v1/228x152-cover-81-auto/"
        "2026/05/Nicki-Minaj-scaled.jpg"
    )

    assert _high_resolution_image_url(thumbnail) == (
        "https://www.hotnewhiphop.com/imgprst/2292x1200-fit-81-auto/"
        "2026/05/Nicki-Minaj-scaled.jpg"
    )


def test_article_page_parser_dedupes_later_in_collector():
    parser = _PageContentParser("https://www.hotnewhiphop.com/")
    parser.feed(
        '<a href="/1002423-post-malone">Post Malone Teases Hip-Hop Return</a>'
        '<a href="/news">News</a>'
    )
    assert parser.links == [
        ("https://www.hotnewhiphop.com/1002423-post-malone", "Post Malone Teases Hip-Hop Return")
    ]


def test_extract_page_metadata_uses_description_and_publish_date():
    html = '''
    <meta name="description" content="첫 문장입니다. 두 번째 핵심 문장입니다.">
    <script type="application/ld+json">{"datePublished":"2026-07-18T01:02:03+00:00"}</script>
    '''
    summary, published, image_url = _extract_page_metadata(
        html + '<meta property="og:image" content="/cover.jpg">',
        "https://example.com/news",
    )
    assert summary == "첫 문장입니다. 두 번째 핵심 문장입니다."
    assert published == "2026-07-18T01:02:03+00:00"
    assert image_url == "https://example.com/cover.jpg"


def test_article_images_skip_icons(monkeypatch):
    html = """
    <meta property="og:image" content="/cover.jpg">
    <img src="/assets/logo.svg" alt="Logo">
    <img data-src="/gallery/stage.jpg" alt="Live stage">
    """
    response = SimpleNamespace(
        text=html,
        url="https://example.com/story",
        apparent_encoding="utf-8",
        encoding="utf-8",
        headers={},
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr("cardnews_auto.feeds.requests.get", lambda *_args, **_kwargs: response)
    results = discover_article_images("https://example.com/story")
    assert [item["url"] for item in results] == [
        "https://example.com/cover.jpg",
        "https://example.com/gallery/stage.jpg",
    ]


def test_article_images_return_the_upgraded_url_not_the_thumbnail(monkeypatch):
    thumbnail = (
        "https://www.hotnewhiphop.com/i/v1/228x152-cover-81-auto/"
        "2026/05/Nicki-Minaj-scaled.jpg"
    )
    response = SimpleNamespace(
        text=f'<img src="{thumbnail}" alt="Nicki Minaj">',
        url="https://www.hotnewhiphop.com/1000000-story",
        apparent_encoding="utf-8",
        encoding="utf-8",
        headers={},
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr("cardnews_auto.feeds.requests.get", lambda *_args, **_kwargs: response)

    assert discover_article_images(response.url)[0]["url"] == (
        "https://www.hotnewhiphop.com/imgprst/2292x1200-fit-81-auto/"
        "2026/05/Nicki-Minaj-scaled.jpg"
    )


def test_article_gallery_keeps_largest_variant_and_skips_tracking_assets(monkeypatch):
    html = """
    <img src="https://sb.scorecardresearch.com/p?id=1">
    <img src="/files/image_36_36/uploads/team.png" alt="Team badge">
    <img src="/files/image_449_221/uploads/story.png">
    <img src="/files/image_948_465/uploads/story.png">
    <img srcset="/small/action.jpg 320w, /large/action.jpg 1600w">
    """
    response = SimpleNamespace(
        text=html,
        url="https://example.com/story",
        apparent_encoding="utf-8",
        encoding="utf-8",
        headers={},
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr("cardnews_auto.feeds.requests.get", lambda *_args, **_kwargs: response)

    assert [item["url"] for item in discover_article_images(response.url)] == [
        "https://example.com/files/image_948_465/uploads/story.png",
        "https://example.com/large/action.jpg",
    ]
