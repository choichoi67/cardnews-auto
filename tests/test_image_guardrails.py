import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from cardnews_auto import render, image_sources
from cardnews_auto.image_identity import image_signature, duplicate_of
from cardnews_auto.models import Article, CardNews, Slide


def card():
    return CardNews(topic="test", rationale="", source_urls=[], caption="", hashtags=[], slides=[
        Slide(role="cover", title="artist concert", body="", image_url="cover"),
        Slide(role="body", title="artist concert", body="", image_url="duplicate"),
        Slide(role="cta", title="", body="")])


def test_search_candidates_are_saved_but_not_requested_from_writer():
    item = card()
    item.slides[0].image_candidates = [{"url": "https://example.com/photo.jpg"}]
    assert "image_candidates" not in CardNews.model_json_schema()["$defs"]["Slide"]["properties"]
    assert CardNews.model_validate_json(item.model_dump_json()).slides[0].image_candidates == item.slides[0].image_candidates


def test_selection_does_not_reuse_hero_when_candidates_exhausted(monkeypatch):
    monkeypatch.setattr(image_sources, "discover_article_images", lambda *a: [])
    item = card()
    audit = image_sources.assign_slide_images(item, Article(title="artist concert", url="https://example.com", image_url="https://example.com/artist-concert.jpg"), {"web_search": {"enabled": False}})
    assert item.slides[0].image_url
    assert item.slides[1].image_url == ""
    assert audit[1]["status"] == "no_image"


def test_signature_detects_resized_reencoded_photo_but_allows_another(tmp_path):
    source = tmp_path / "source.png"
    image = Image.new("RGB", (1200, 900), "navy")
    ImageDraw.Draw(image).ellipse((200, 50, 800, 800), fill="orange")
    image.save(source)
    variant = tmp_path / "different-name.jpg"
    image.resize((800, 600)).save(variant, quality=80)
    other = tmp_path / "other.png"
    Image.new("RGB", (1200, 900), "green").save(other)
    used = {0: image_signature(source)}
    assert duplicate_of(image_signature(variant), used) == 0
    assert duplicate_of(image_signature(other), used) is None


@pytest.mark.parametrize("has_alternative", [True, False])
def test_preflight_replaces_duplicate_or_stops_before_paid_edit(monkeypatch, tmp_path, has_alternative):
    source, other = tmp_path / "source.png", tmp_path / "other.png"
    Image.new("RGB", (1080,1440), "navy").save(source)
    Image.new("RGB", (1080,1440), "green").save(other)
    monkeypatch.setattr(render, "_download_cover_image", lambda url, *_: other if url == "alternative" else source)
    edits = []
    monkeypatch.setattr(render, "_transform_image_with_fallback", lambda path, *_: edits.append(path) or path)
    item = card()
    if has_alternative:
        item.slides[1].image_candidates = [{"url": "alternative", "provider": "test"}]
    config = {"publication_frame": "exact", **{r: {"renderer": "auto"} for r in ("cover","body","cta")}}
    if has_alternative:
        render.render_hybrid(item, config, tmp_path / "out", image_transform={"enabled": True, "generate_missing": False})
        assert item.slides[1].image_url == "alternative"
        assert edits == [source, other]
    else:
        with pytest.raises(RuntimeError, match="2번 카드.*중복"):
            render.render_hybrid(item, config, tmp_path / "out", image_transform={"enabled": True, "generate_missing": False})
        assert edits == []
        assert not list((tmp_path / "out").glob("slide_*.jpg"))


@pytest.mark.parametrize("second_size", [(1536,2048), (1200,1600), (1024,1536)])
def test_generated_size_is_verified_and_retried_only_once(monkeypatch, tmp_path, second_size):
    source = tmp_path / "source.png"
    Image.new("RGB", (100,100)).save(source)
    calls = []
    def edit(**kwargs):
        calls.append(kwargs)
        result = BytesIO()
        Image.new("RGB", (1024,1536) if len(calls) == 1 else second_size, "blue").save(result, "PNG")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(result.getvalue()).decode())])
    monkeypatch.setattr(render, "OpenAI", lambda **_: SimpleNamespace(images=SimpleNamespace(edit=edit)))
    options = {"enabled": True, "size": "1536x2048", "quality": "high", "fallback_quality": "", "prompt": render.SHARED_COMPOSITION_PROMPT}
    if second_size in ((1536,2048), (1200,1600)):
        result = render._transform_image_with_fallback(source, options, tmp_path / "cache")
        assert Image.open(result).size == second_size
        # A valid cached result makes no additional paid call.
        assert render._transform_image_with_fallback(source, options, tmp_path / "cache") == result
    else:
        with pytest.raises(render.ImageSizeMismatch):
            render._transform_image_with_fallback(source, options, tmp_path / "cache")
        assert not list((tmp_path / "cache").glob("*.png"))
    assert len(calls) == 2
    assert all(request["size"] == "1536x2048" and request["quality"] == "high" for request in calls)


def test_generated_duplicate_is_blocked_before_rendering(monkeypatch, tmp_path):
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (1080,1440), "navy").save(first)
    Image.new("RGB", (1080,1440), "green").save(second)
    monkeypatch.setattr(render, "_download_cover_image", lambda url, *_: first if url == "cover" else second)
    monkeypatch.setattr(render, "_transform_image_with_fallback", lambda *_: first)
    config = {"publication_frame": "exact", **{r: {"renderer": "auto"} for r in ("cover","body","cta")}}
    with pytest.raises(RuntimeError, match="출력 배경.*중복"):
        render.render_hybrid(card(), config, tmp_path / "out", image_transform={"enabled": True, "generate_missing": False})
    assert not list((tmp_path / "out").glob("slide_*.jpg"))


def test_missing_photo_recovers_with_distinct_candidate(monkeypatch, tmp_path):
    source, other = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (1080,1440), "navy").save(source)
    Image.new("RGB", (1080,1440), "green").save(other)
    monkeypatch.setattr(render,"_download_cover_image",lambda url,*_: other if url == "recovered" else source)
    requests=[]
    def recover(slide, excluded):
        requests.append((slide.title,excluded))
        return [{"url":"recovered","provider":"test"}]
    item=card()
    cfg={"publication_frame":"exact",**{role:{"renderer":"auto"} for role in ("cover","body","cta")}}
    render.render_hybrid(item,cfg,tmp_path/'out',image_recovery=recover)
    assert len(requests)==1 and "cover" in requests[0][1]
    assert item.slides[1].image_url == "recovered"
    import json
    audit=json.loads((tmp_path/'out'/'image_selection.json').read_text(encoding='utf-8'))
    assert any(row['status']=='duplicate_content:slide_1' for row in audit)
    assert audit[-1]['status']=='selected'


def test_recovery_search_allows_all_sources_without_keyword_gate(monkeypatch):
    monkeypatch.setattr(image_sources,"_openai_image_search",lambda *a:[
        {"url":"https://club.test/unique.jpg","page_url":"https://athletic-club.eus/news","caption":"Williams striker official"},
        {"url":"https://other.test/unrelated.jpg","page_url":"https://athletic-club.eus/news","caption":""},
        {"url":"https://espn.com/photo.jpg","page_url":"https://espn.com/news","caption":"Williams striker"}])
    slide=Slide(title="",body="",image_query="Williams striker")
    result=image_sources.search_alternative_images(slide,Article(title="1면",url="https://example.com"),{"web_search":{"official_only":True}},[])
    assert [row['url'] for row in result]==["https://club.test/unique.jpg", "https://other.test/unrelated.jpg", "https://espn.com/photo.jpg"]


def test_recovery_broadens_only_approved_subject_and_labels_context(monkeypatch):
    requests=[]
    def search(query,*args):
        requests.append(query)
        return [{"url":"https://example.com/doechii.jpg","caption":"Doechii portrait","provider":"openai_web_search"},
                {"url":"https://example.com/other.jpg","caption":"","provider":"openai_web_search"}]
    monkeypatch.setattr(image_sources,"_openai_image_search",search)
    slide=Slide(role="cover",title="영상",body="",image_query="Doechii Bombs music video breastplate sparklers")
    article=Article(title="Doechii 데뷔 앨범",url="https://example.com/article")
    results=image_sources.search_alternative_images(slide,article,{},[])
    assert len(results)==2 and results[0]['provider'].endswith(':subject_context')
    assert "Do not require the exact outfit" in requests[0]
    requests.clear()
    image_sources.search_alternative_images(Slide(**slide.model_dump()),Article(title="Other topic",url=article.url),{},[])
    assert "Do not require the exact outfit" not in requests[0]


def test_search_quota_error_is_not_disguised_as_missing_photos(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("credit_balance_exhausted")
    monkeypatch.setattr(image_sources,"OpenAI",lambda **kwargs:SimpleNamespace(responses=SimpleNamespace(parse=fail)))
    with pytest.raises(image_sources.ImageSearchQuotaError,match="크레딧"):
        image_sources._openai_image_search("Doechii","test",6)
    with pytest.raises(image_sources.ImageSearchQuotaError,match="크레딧"):
        image_sources.assign_slide_images(card(),Article(title="Doechii",url="https://example.com"),{"rss":{"enabled":False}})


def test_original_recovery_requests_publication_ready_general_audience_photos(monkeypatch):
    from cardnews_auto import image_sources
    from cardnews_auto.models import Slide, Article
    captured = []
    monkeypatch.setattr(image_sources, "_openai_image_search", lambda query, *args: captured.append(query) or [])
    image_sources.search_alternative_images(Slide(title="목표", body="", image_query="Doechii portrait"),
        Article(title="Doechii album", url="https://example.com"), {}, [], publication_ready=True)
    assert "3:4" in captured[0] and "1080x1440" in captured[0]
    assert "ordinary clothing" in captured[0] and "No AI editing" in captured[0]
