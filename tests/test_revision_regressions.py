import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from cardnews_auto import revision_api, render
from cardnews_auto.models import Article, CardNews, Slide


@pytest.fixture
def upload_context(monkeypatch, tmp_path):
    monkeypatch.setattr(revision_api, "REVISION_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(revision_api, "get_artifact", lambda _: {"slide_count": 3, "magazine_id": "test"})
    monkeypatch.setattr(revision_api, "profile", lambda _: {})
    monkeypatch.setattr(revision_api, "_artifact_source", lambda *_: Article(title="Test", url="https://example.com"))
    monkeypatch.setattr(revision_api, "_enqueue_revision", lambda *args, **_: {"reference": args[5]})
    return tmp_path


def upload(data, filename="reference.jpg"):
    return asyncio.run(revision_api.revise_artifact(
        1, instruction="", cover_title="", slide_number=2, slide_title="", slide_body="",
        reference_image=UploadFile(filename=filename, file=BytesIO(data)),
    ))


@pytest.mark.parametrize("size,format", [((1600, 900), "JPEG"), ((900, 1600), "PNG"), ((500, 500), "WEBP")])
def test_uploaded_reference_is_always_portrait(upload_context, size, format):
    data = BytesIO()
    Image.new("RGB", size, "red").save(data, format)
    result = upload(data.getvalue(), "reference." + format.lower())
    target = Path(result["reference"])
    with Image.open(target) as image:
        assert image.size == (1080, 1440)
        assert image.format == "JPEG"
    assert set(upload_context.iterdir()) == {target, target.with_suffix(".source")}
    assert target.with_suffix(".source").read_bytes() == data.getvalue()


def test_invalid_reference_is_rejected_and_cleaned(upload_context):
    with pytest.raises(HTTPException) as error:
        upload(b"not an image")
    assert error.value.status_code == 400
    assert list(upload_context.iterdir()) == []


def test_reference_respects_phone_orientation(tmp_path):
    source = tmp_path / "phone.jpg"
    target = tmp_path / "result.jpg"
    image = Image.new("RGB", (1440, 1080), "red")
    image.paste("blue", (720, 0, 1440, 1080))
    exif = image.getexif()
    exif[274] = 6
    image.save(source, exif=exif)
    render._render_reference_image(source, target)
    with Image.open(target) as result:
        assert result.size == (1080, 1440)
        assert result.getpixel((540, 200))[0] > 200
        assert result.getpixel((540, 1200))[2] > 200


def test_missing_image_never_shifts_later_slide_numbers(monkeypatch, tmp_path):
    card = CardNews(topic="Test", rationale="", source_urls=[], caption="", hashtags=[], slides=[
        Slide(role="cover", title="Cover", body=""),
        Slide(role="body", title="Missing", body=""),
        Slide(role="cta", title="CTA", body=""),
    ])
    monkeypatch.setattr(render, "plan_layout", lambda *_: ([{"renderer": "auto"}, {"renderer": "image"}, {"renderer": "auto"}], "test"))
    monkeypatch.setattr(render, "_download_cover_image", lambda *_: None)
    with pytest.raises(RuntimeError, match="2번 카드"):
        render.render_hybrid(card, {}, tmp_path / "output")
    assert not (tmp_path / "output" / "slide_02.jpg").exists()
