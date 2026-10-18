import json
import pytest
from PIL import Image
from cardnews_auto.portrait_frame import publication_frame
from cardnews_auto import render
from cardnews_auto.models import CardNews, Slide


def test_exact_background_preserves_pixels_and_rejects_crop_or_upscale(tmp_path):
    source = tmp_path / "source.png"
    image = Image.new("RGB", (1080, 1440), "navy")
    image.paste("red", (0, 0, 100, 100))
    image.save(source)
    result = publication_frame(source, tmp_path / "cache")
    assert result == source
    assert Image.open(result).tobytes() == image.tobytes()
    for size, message in [((2292,1200), "3:4"), ((540,720), "해상도")]:
        Image.new("RGB", size).save(source)
        with pytest.raises(RuntimeError, match=message):
            publication_frame(source, tmp_path / "cache")


def test_large_portrait_only_downscales_once(tmp_path):
    source = tmp_path / "large.png"
    Image.new("RGB", (1536,2048), "red").save(source)
    result = publication_frame(source, tmp_path / "cache")
    assert Image.open(result).size == (1080,1440)
    assert publication_frame(result, tmp_path / "cache") == result


@pytest.mark.parametrize("size", [(1080,1440), (1200,1600), (1536,2048), (2160,2880)])
def test_valid_generated_portraits_are_accepted_once_and_reused_from_cache(tmp_path, monkeypatch, size):
    import base64
    from io import BytesIO
    from types import SimpleNamespace
    source = tmp_path / "source.png"
    Image.new("RGB", (1080,1440)).save(source)
    generated = Image.new("RGB", size, "navy")
    generated.paste("red", (0, 0, size[0]//4, size[1]//4))
    data = BytesIO()
    generated.save(data, "PNG")
    calls = []
    def edit(**kwargs):
        calls.append(kwargs["size"])
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(data.getvalue()).decode())])
    monkeypatch.setattr(render, "OpenAI", lambda **_: SimpleNamespace(images=SimpleNamespace(edit=edit)))
    options = {"enabled":True, "size":"1536x2048", "quality":"high", "fallback_quality":""}
    result = render._transform_image_with_fallback(source, options, tmp_path / "generated")
    assert Image.open(result).size == size
    assert render._transform_image_with_fallback(source, options, tmp_path / "generated") == result
    assert calls == ["1536x2048"]  # No size retry, including on a cache hit.
    published = publication_frame(result, tmp_path / "published")
    with Image.open(published) as final:
        assert final.size == (1080,1440)
        assert final.tobytes() == generated.resize((1080,1440), Image.Resampling.LANCZOS).tobytes()


@pytest.mark.parametrize("size", [(768,1024), (1077,1436), (1080,1441), (2048,1536), (1024,1536)])
def test_generated_images_and_publication_share_minimum_size_and_ratio(tmp_path, size):
    source = tmp_path / "invalid.png"
    Image.new("RGB", size).save(source)
    with pytest.raises(render.ImageSizeMismatch):
        render._validate_generated_image(source)
    with pytest.raises(RuntimeError):
        publication_frame(source, tmp_path / "published")


def test_corrupt_generation_is_still_rejected(tmp_path):
    source = tmp_path / "broken.png"
    source.write_bytes(b"not an image")
    with pytest.raises(OSError):
        render._validate_generated_image(source)


def test_cover_and_body_use_same_generation_size_and_publication_pixels(tmp_path, monkeypatch):
    source = tmp_path / "generated.png"
    Image.new("RGB", (1536,2048), "navy").save(source)
    second = tmp_path / "body.png"
    Image.new("RGB", (1536,2048), "red").save(second)
    monkeypatch.setattr(render, "_download_cover_image", lambda url, *_: source if url == "cover" else second)
    requests = []
    def edit(path, options, cache):
        requests.append((options["size"], options["quality"]))
        return path
    monkeypatch.setattr(render, "_transform_image_with_fallback", edit)
    captured = {}
    def studio(_root, spec, slide, target, background=None, page=""):
        captured[slide.role] = (spec["image_fit"], Image.open(background).tobytes())
        Image.open(background).save(target)
    monkeypatch.setattr(render, "render_slide", studio)
    from cardnews_auto.template_publication import bundle_hash
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "template.json").write_text("{}", encoding="utf-8")
    spec = {"renderer": "template-lab", "bundle_path": str(bundle), "bundle_sha256": bundle_hash(bundle)}
    cfg = {"publication_frame": "exact", "cta": {"renderer":"auto"}, **{r: dict(spec) for r in ("cover", "body")}}
    card = CardNews(topic="test", rationale="", source_urls=[], caption="", hashtags=[],
                    slides=[Slide(role=r, title="test", body="", image_url=r) for r in ("cover", "body")] + [Slide(role="cta",title="",body="")])
    render.render_hybrid(card, cfg, tmp_path / "out", image_transform={"enabled":True, "recompose_for_publication":True, "max_transformed_slides":1})
    assert requests == [("1536x2048","high")] * 2
    assert captured["cover"][0] == captured["body"][0] == "slot"
    for role, path in (("cover", source), ("body", second)):
        expected = publication_frame(path, tmp_path / "expected")
        assert captured[role][1] == Image.open(expected).tobytes()
