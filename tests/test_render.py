import pytest
from PIL import Image
from cardnews_auto.template_publication import bundle_hash, text_options, wrap_text
from cardnews_auto.render import (
    DEFAULT_POSE_EDIT_PROMPT,
    _image_edit_size,
    _render_reference_image,
    _transform_cover_image,
    _transform_image_with_fallback,
    render_hybrid,
)


def studio_spec(tmp_path, role, **extra):
    """A minimal immutable template-studio version for render_hybrid tests."""
    bundle = tmp_path / "bundles" / role
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "template.json").write_text("{}", encoding="utf-8")
    return {"renderer": "template-lab", "bundle_path": str(bundle), "bundle_sha256": bundle_hash(bundle), **extra}


def test_reference_image_preserves_landscape_content_on_vertical_canvas(tmp_path):
    from PIL import Image

    source = tmp_path / "source.jpg"
    target = tmp_path / "target.jpg"
    Image.new("RGB", (1200, 600), "red").save(source)

    _render_reference_image(source, target)

    with Image.open(target) as image:
        assert image.size == (1080, 1440)
        assert image.getpixel((540, 720))[0] > 200


def test_reference_image_centers_landscape_without_face_guessing(tmp_path):
    source, target = tmp_path / "source.png", tmp_path / "target.jpg"
    image = Image.new("RGB", (1200, 600), "blue")
    image.paste("white", (550, 250, 650, 350))
    image.save(source)
    _render_reference_image(source, target)
    with Image.open(target) as result:
        assert result.size == (1080, 1440)
        assert sum(result.getpixel((540, 720))) > 700


def test_psd_text_is_truncated_to_template_limit():
    result = wrap_text("긴 설명문이 표지 부제 영역을 넘어가는 문제를 막아야 합니다", {"max_chars": 12})
    assert result.endswith("…")
    assert len(result) == 12


def test_cover_title_is_fitted_by_text_not_font_size():
    result = wrap_text(
        "카이 세낫 경호원 폭행 혐의 소송에 이름 올랐다",
        {"max_chars": 20, "line_chars": 10, "max_lines": 2, "allow_shrink": False},
    )
    assert len(result.replace("\r", "")) <= 20
    assert len(result.split("\r")) <= 2


def test_cover_newline_is_normalized_and_hard_wrapped():
    options = text_options(
        {
            "module_role": "cover",
            "text_options": {
                "title": {"max_chars": 20, "max_lines": 2, "allow_shrink": False},
            },
        },
        "title",
    )
    result = wrap_text(
        "드레이크\n투어 암시",
        options,
    )
    lines = result.split("\r")
    assert "\n" not in result
    assert len(lines) == 2
    assert lines == ["드레이크", "투어 암시"]
    assert all(len(line) <= 10 for line in lines)
    assert len(result.replace("\r", "")) <= 20


def test_korean_body_wrap_preserves_manual_lines_and_whole_words():
    result = wrap_text(
        "새 앨범을 공개합니다.\n팬들의 기대가 커집니다.",
        {
            "max_chars": 40,
            "line_chars": 14,
            "max_lines": 2,
            "preserve_manual_lines": True,
            "strict_korean_wrap": True,
        },
    )

    assert result == "새 앨범을 공개합니다.\r팬들의 기대가 커집니다."


def test_korean_wrap_keeps_names_modifiers_and_work_titles_together():
    result = wrap_text(
        "50 센트는 새 음악과 《Loyal To The Game》 제작 배경을 설명했습니다.",
        {
            "max_chars": 80,
            "line_chars": 16,
            "max_lines": 6,
            "preserve_manual_lines": False,
            "strict_korean_wrap": True,
        },
    )

    assert "50\r센트" not in result
    assert "새\r음악" not in result
    assert "Loyal To\rThe Game" not in result


def test_korean_wrap_attaches_quote_particle_and_reporting_verb():
    result = wrap_text(
        '50 센트는 “AI가 아니다” 고 밝혔습니다.',
        {
            "max_chars": 60,
            "line_chars": 18,
            "max_lines": 4,
            "preserve_manual_lines": False,
            "strict_korean_wrap": True,
        },
    )

    assert "” 고" not in result
    assert "”\r고" not in result
    assert not any(
        line.endswith(("라고", "라며", "고", "며"))
        for line in result.split("\r")[:-1]
    )


def test_korean_body_wrap_rejects_cutting_a_long_word():
    import pytest

    with pytest.raises(RuntimeError, match="어절"):
        wrap_text(
            "매우긴고유명사프로젝트입니다.",
            {
                "max_chars": 40,
                "line_chars": 10,
                "max_lines": 2,
                "preserve_manual_lines": True,
                "strict_korean_wrap": True,
            },
        )


def test_image_edit_size_preserves_landscape_ratio(tmp_path):
    from PIL import Image

    image_path = tmp_path / "source.jpg"
    Image.new("RGB", (2292, 1200)).save(image_path)
    assert _image_edit_size(image_path) == "2048x1072"


def test_image_edit_size_upscales_small_source_to_api_minimum(tmp_path):
    from PIL import Image

    image_path = tmp_path / "small.jpg"
    Image.new("RGB", (944, 688)).save(image_path)
    width, height = map(int, _image_edit_size(image_path).split("x"))

    assert width * height >= 1_048_576
    assert abs((width / height) - (944 / 688)) < 0.03


def test_pose_edit_uses_default_prompt_when_channel_prompt_is_empty(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PIL import Image

    source = tmp_path / "source.jpg"
    Image.new("RGB", (1080, 1440), "white").save(source)
    captured = {}
    client_options = {}

    class FakeImages:
        def edit(self, **kwargs):
            captured.update(kwargs)
            import base64
            from io import BytesIO
            generated = BytesIO()
            Image.new("RGB", (1080, 1440), "white").save(generated, "PNG")
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(generated.getvalue()).decode())])

    def fake_openai(**kwargs):
        client_options.update(kwargs)
        return SimpleNamespace(images=FakeImages())

    monkeypatch.setattr("cardnews_auto.render.OpenAI", fake_openai)

    result = _transform_cover_image(
        source,
        {
            "enabled": True,
            "model": "gpt-image-2",
            "quality": "high",
            "size": "source_ratio_2k",
            "prompt": "",
        },
        tmp_path / "cache",
    )

    assert Image.open(result).size == (1080, 1440)
    from cardnews_auto.render import WATERMARK_CLEANUP_PROMPT
    assert captured["prompt"] == DEFAULT_POSE_EDIT_PROMPT + "\n\n" + WATERMARK_CLEANUP_PROMPT
    assert captured["model"] == "gpt-image-2"
    assert captured["size"] == "1088x1440"
    assert client_options == {"timeout": 90.0, "max_retries": 1}


def test_image_edit_retries_at_medium_quality(monkeypatch, tmp_path):
    from PIL import Image

    source = tmp_path / "source.jpg"
    Image.new("RGB", (1080, 1440), "white").save(source)
    qualities = []

    def fake_transform(image_path, options, cache_dir):
        qualities.append(options["quality"])
        if len(qualities) == 1:
            raise RuntimeError("temporary high-quality failure")
        target = cache_dir / "fallback.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fallback")
        return target

    monkeypatch.setattr("cardnews_auto.render._transform_cover_image", fake_transform)
    result = _transform_image_with_fallback(
        source,
        {"enabled": True, "quality": "high", "fallback_quality": "medium"},
        tmp_path / "cache",
    )
    assert qualities == ["high", "medium"]
    assert result.read_bytes() == b"fallback"


def test_public_figure_image_block_uses_original_without_quality_retry(monkeypatch, tmp_path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    attempts = []

    def blocked_transform(image_path, options, cache_dir):
        attempts.append(options["quality"])
        raise RuntimeError("moderation_blocked: public-figure")

    monkeypatch.setattr("cardnews_auto.render._transform_cover_image", blocked_transform)

    result = _transform_image_with_fallback(
        source,
        {
            "enabled": True,
            "quality": "high",
            "fallback_quality": "medium",
            "use_original_on_failure": True,
        },
        tmp_path / "cache",
    )

    assert result == source
    assert attempts == ["high"]


def test_timeout_uses_original_without_slow_quality_retry(monkeypatch, tmp_path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    attempts = []

    def timeout(_image_path, options, _cache_dir):
        attempts.append(options["quality"])
        raise RuntimeError("Request timed out.")

    monkeypatch.setattr("cardnews_auto.render._transform_cover_image", timeout)
    result = _transform_image_with_fallback(
        source,
        {"quality": "high", "fallback_quality": "medium", "use_original_on_failure": True},
        tmp_path,
    )

    assert result == source
    assert attempts == ["high"]


def test_render_hybrid_never_downloads_or_replaces_cta_image(monkeypatch, tmp_path):
    from cardnews_auto.models import CardNews, Slide

    card = CardNews(
        topic="Topic",
        rationale="Why",
        source_urls=["https://example.com/story"],
        slides=[
            Slide(role="cover", title="Cover", body="Lead"),
            Slide(
                role="cta",
                title="Follow",
                body="CTA",
                image_url="https://bad.example/should-not-download.jpg",
            )
        ],
        caption="Caption",
        hashtags=["news"],
    )
    downloaded = []

    def fake_download(image_url, _cache_dir):
        downloaded.append(image_url)
        return None

    monkeypatch.setattr("cardnews_auto.render._download_cover_image", fake_download)

    paths = render_hybrid(
        card,
        {
            "cover": {"renderer": "preview"},
            "cta": {"renderer": "preview"},
        },
        tmp_path / "output",
        image_transform={"max_parallel": 1, "max_transformed_slides": 0},
    )

    assert len(paths) == 2
    assert all(path.exists() for path in paths)
    assert "https://bad.example/should-not-download.jpg" not in downloaded


def test_cta_ignores_legacy_cover_reuse_without_second_download(monkeypatch, tmp_path):
    from cardnews_auto.models import CardNews, Slide

    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (1080, 1440), "white").save(cover)
    downloads, rendered = [], []

    def fake_download(url, _cache):
        downloads.append(url)
        return cover

    def fake_render(_root, _spec, slide, target, image_path=None, page=""):
        rendered.append((slide.role, image_path))
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1080, 1440), "white").save(target)
        return target

    monkeypatch.setattr("cardnews_auto.render._download_cover_image", fake_download)
    monkeypatch.setattr("cardnews_auto.render.render_slide", fake_render)
    card = CardNews(
        topic="Topic",
        rationale="Why",
        source_urls=[],
        slides=[
            Slide(role="cover", title="Cover", body="", image_url="cover-url"),
            Slide(role="cta", title="", body=""),
        ],
        caption="",
        hashtags=[],
    )

    render_hybrid(
        card,
        {
            "cover": studio_spec(tmp_path, "cover"),
            "cta": studio_spec(tmp_path, "cta", preserve_original=True, reuse_cover_image=True, image_layer="CTA 사진"),
        },
        tmp_path / "output",
        image_transform={"max_parallel": 1, "max_transformed_slides": 0},
    )

    assert downloads == ["cover-url"]
    assert sorted(rendered, key=str) == sorted([("cover", cover), ("cta", None)], key=str)


def test_body_download_failure_stops_before_repeating_cover(monkeypatch, tmp_path):
    from cardnews_auto.models import CardNews, Slide

    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (1080, 1440), "white").save(cover)
    downloads = []
    rendered = []

    def fake_download(url, _cache):
        downloads.append(url)
        return cover if url == "cover-url" else None

    def fake_render(_root, _spec, slide, target, image_path=None, page=""):
        rendered.append((slide.role, image_path))
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1080, 1440), "white").save(target)
        return target

    monkeypatch.setattr("cardnews_auto.render._download_cover_image", fake_download)
    monkeypatch.setattr("cardnews_auto.render.render_slide", fake_render)
    card = CardNews(
        topic="Topic",
        rationale="Why",
        source_urls=[],
        slides=[
            Slide(role="cover", title="Cover", body="Lead", image_url="cover-url"),
            Slide(role="body", title="Body", body="Text", image_url="broken-url"),
            Slide(role="cta", title="", body=""),
        ],
        caption="",
        hashtags=[],
    )

    with pytest.raises(RuntimeError, match="2번 카드"):
        render_hybrid(
            card,
            {role: studio_spec(tmp_path, role) for role in ("cover", "body", "cta")},
            tmp_path / "output",
            image_transform={"max_parallel": 1, "max_transformed_slides": 0},
        )

    assert downloads == ["cover-url", "broken-url"]
    assert rendered == []


@pytest.mark.parametrize("frame", [None, "exact"])
def test_common_recompose_transforms_every_card_independent_of_frame(monkeypatch, tmp_path, frame):
    from cardnews_auto.models import CardNews, Slide
    from PIL import Image

    source = tmp_path / "source.jpg"
    Image.new("RGB", (1080, 1440), "white").save(source)
    body_source = tmp_path / "body.jpg"
    Image.new("RGB", (1080,1440), "navy").save(body_source)
    transformed, prompts = [], []
    monkeypatch.setattr(
        "cardnews_auto.render._download_cover_image",
        lambda _url, _cache: source if _url == "cover" else body_source,
    )

    def transform(path, options, _cache):
        transformed.append(path)
        prompts.append(options["prompt"])
        return path

    monkeypatch.setattr("cardnews_auto.render._transform_image_with_fallback", transform)
    card = CardNews(
        topic="Topic",
        rationale="Why",
        source_urls=[],
        slides=[
            Slide(role="cover", title="Cover", body="Lead", image_url="cover"),
            Slide(role="body", title="Body", body="Text", image_url="body"),
            Slide(role="cta", title="", body=""),
        ],
        caption="",
        hashtags=[],
    )

    render_hybrid(
        card,
        {"publication_frame":frame, **{role: {"renderer": "auto"} for role in ("cover", "body", "cta")}},
        tmp_path / "output",
        image_transform={"enabled": True, "recompose_for_publication":True, "max_transformed_slides": 1},
    )

    assert transformed == [source, body_source]
    assert "Reference context JSON" in prompts[0]
    assert "no later crop or zoom" in prompts[0]
    assert "Slide headline" not in prompts[0]
    assert "REFERENCE" in prompts[0] or "Reference" in prompts[0]

@pytest.mark.parametrize("replacement_mode", ["pool", "search", "empty"])
def test_blocked_edit_uses_only_unique_publication_ready_original(monkeypatch, tmp_path, replacement_mode):
    from cardnews_auto.models import CardNews, Slide
    from cardnews_auto import render
    import json
    sources = {}
    for name, size, color in [("cover", (1080,1440), "navy"), ("blocked", (1600,900), "red"),
                              ("duplicate", (1080,1440), "navy"), ("small", (600,800), "green"),
                              ("replacement", (1080,1440), "yellow")]:
        path = tmp_path / f"{name}.png"
        Image.new("RGB", size, color).save(path)
        sources[name] = path
    monkeypatch.setattr(render, "_download_cover_image", lambda url, cache: sources.get(url))
    calls = []
    def transform(path, options, cache):
        calls.append(path)
        if path == sources["blocked"]:
            options.update(_outcome="original_fallback", _failure_reason="AI 안전 검사에서 이미지 생성이 차단됐습니다.")
        return path
    monkeypatch.setattr(render, "_transform_image_with_fallback", transform)
    rendered = {}
    def output(_root, _spec, slide, target, image_path=None, page=""):
        rendered[int(page.split("/")[0]) - 1] = image_path
        Image.new("RGB", (1080,1440), "white").save(target)
        return target
    monkeypatch.setattr(render, "render_slide", output)
    specs = {role: studio_spec(tmp_path, role) for role in ("cover", "body", "cta")}
    specs["publication_frame"] = "exact"
    candidates = [dict(url=x) for x in ["duplicate", "small"] + (["replacement"] if replacement_mode == "pool" else [])]
    card = CardNews(topic="test", rationale="test", source_urls=[], caption="", hashtags=[], slides=[
        Slide(role="cover",title="Cover",body="",image_url="cover"),
        Slide(role="body",title="Body",body="",image_url="blocked",image_candidates=candidates),
        Slide(role="cta",title="CTA",body="")])
    out = tmp_path / "output"
    recovery_calls = []
    def recover(slide, excluded):
        recovery_calls.append(excluded)
        assert "blocked" in excluded and "cover" in excluded
        return [dict(url="replacement")] if replacement_mode == "search" else []
    if replacement_mode != "empty":
        render_hybrid(card, specs, out, image_transform={"enabled":True, "generate_missing":False}, original_image_recovery=recover)
        assert card.slides[1].image_url == "replacement"
        assert rendered[1] == sources["replacement"]
    else:
        with pytest.raises(RuntimeError, match="2번 카드.*확보한 후보"):
            render_hybrid(card, specs, out, image_transform={"enabled":True, "generate_missing":False}, original_image_recovery=recover)
        assert rendered == {}
    assert len(recovery_calls) == (0 if replacement_mode == "pool" else 1)
    assert calls == [sources["cover"], sources["blocked"]]
    audit = json.loads((out / "image_selection.json").read_text(encoding="utf-8"))
    assert any(x["status"] == "fallback_duplicate_content" for x in audit)
    assert any(x["status"] == "fallback_invalid_frame" for x in audit)
