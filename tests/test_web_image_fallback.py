import pytest
from PIL import Image

from cardnews_auto import render
from cardnews_auto.models import CardNews, Slide


def card():
    return CardNews(topic="앨범 제작", rationale="", source_urls=[], caption="", hashtags=[], slides=[
        Slide(role="cover", title="새 앨범", body="", image_url="cover"),
        Slide(role="body", title="", body="앨범 제작 과정입니다.", image_url="body"),
    ])


def config():
    return {"publication_frame": "exact", "cover": {"renderer": "auto"}, "body": {"renderer": "auto"}}


@pytest.fixture
def photos(tmp_path, monkeypatch):
    files = {}
    for name, color in [("cover", "navy"), ("body", "red"), ("web", "yellow")]:
        path = tmp_path / f"{name}.png"
        Image.new("RGB", (1080, 1440), color).save(path)
        files[name] = path
    monkeypatch.setattr(render, "_download_cover_image", lambda url, *_: files.get(url))
    monkeypatch.setattr(render, "OpenAI", lambda **_: pytest.fail("unexpected provider call"))
    return files


def test_missing_photo_uses_web_recovery_without_new_generation(photos, monkeypatch, tmp_path):
    value = card()
    value.slides[1].image_url = ""
    monkeypatch.setattr(render, "_transform_image_with_fallback", lambda path, *_: path)
    searches = []
    def recover(slide, excluded):
        searches.append(slide.body)
        return [{"url": "web", "provider": "web_search"}]
    result = render.render_hybrid(value, config(), tmp_path / "out", image_transform={"enabled": True}, image_recovery=recover)
    assert len(result) == 2 and searches == [value.slides[1].body]
    assert value.slides[1].image_url == "web"


def test_no_candidates_cannot_call_grok_even_with_legacy_option(photos, tmp_path):
    value = card()
    value.slides[1].image_url = "missing"
    with pytest.raises(RuntimeError, match="중복 0개, 다운로드 실패 1개"):
        render.render_hybrid(value, config(), tmp_path / "out",
                             image_transform={"enabled": True, "generate_missing": True})
    assert not hasattr(render, "_generate_slide_image")


def test_empty_search_reports_no_candidates_instead_of_download_failure(photos, tmp_path):
    value = card()
    value.slides[1].image_url = ""
    with pytest.raises(RuntimeError, match="검색·관련성·출처 검사를 통과한 사진 후보가 없습니다"):
        render.render_hybrid(value, config(), tmp_path / "out", image_recovery=lambda *_: [])


def test_edit_failure_with_no_valid_original_does_not_generate(photos, monkeypatch, tmp_path):
    Image.new("RGB", (1200, 600), "red").save(photos["body"])
    def edit(path, options, *_):
        options.update(_outcome="original_fallback", _failure_reason="편집 실패")
        return path
    monkeypatch.setattr(render, "_transform_image_with_fallback", edit)
    with pytest.raises(RuntimeError, match="발행 규격 사진"):
        render.render_hybrid(card(), config(), tmp_path / "out", image_transform={"enabled": True})


def test_duplicate_edited_photo_is_replaced_by_web(photos, monkeypatch, tmp_path):
    value = card()
    monkeypatch.setattr(render, "_transform_image_with_fallback", lambda *_: photos["cover"])
    searches = []
    def recover(slide, excluded):
        searches.append(excluded)
        return [{"url": "web", "provider": "web_search"}]
    render.render_hybrid(value, config(), tmp_path / "out", image_transform={"enabled": True}, duplicate_image_recovery=recover)
    assert len(searches) == 1
    assert value.slides[1].image_url == "web"
    assert "AI 생성 설명 이미지" not in value.caption
