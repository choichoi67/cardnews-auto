"""Production rendering with template-studio versions (PhotoCraft-style text placement, no Photoshop)."""
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from cardnews_auto.models import Slide
from cardnews_auto.template_primitives import layout_text
from cardnews_auto.template_publication import render_slide

FONT = Path(__file__).resolve().parents[1] / "templates" / "fonts" / "Pretendard-Black.otf"


@pytest.fixture
def bundle(tmp_path):
    if not FONT.is_file():
        pytest.skip("project font missing")
    root = tmp_path / "bundle"
    (root / "layers").mkdir(parents=True)
    (root / "fonts").mkdir()
    shutil.copy2(FONT, root / "fonts" / "f.otf")
    Image.new("RGBA", (400, 300), "black").save(root / "layers" / "bg.png")
    Image.new("RGBA", (100, 30), "red").save(root / "layers" / "box.png")
    Image.new("RGBA", (10, 10), "white").save(root / "layers" / "sample.png")
    text = dict(kind="type", visible=True, text_editable=True, font_file="fonts/f.otf", asset="layers/sample.png",
                text_color="#ffffff", tracking=0, source_opacity=255, width=10, height=10)
    layers = [
        dict(id="bg", name="bg", kind="pixel", visible=True, x=0, y=0, width=400, height=300, asset="layers/bg.png"),
        dict(id="box", name="box", kind="shape", visible=True, x=20, y=20, width=100, height=30, asset="layers/box.png",
             source_opacity=255, shape=dict(fill="#ff0000", stroke="#000000", stroke_width=0, fill_enabled=True, stroke_enabled=False)),
        dict(text, id="eyebrow", name="eyebrow", text="AB", font_size=16, leading=20, anchor=[30, 42], align="left", x=30, y=28),
        dict(text, id="title", name="title", text="가나\n다라", font_size=30, leading=36, anchor=[200, 150], align="center", x=180, y=125,
             text_runs=[dict(start=0, end=3, font_file="fonts/f.otf", font_size=30, text_color="#ffffff"),
                        dict(start=3, end=5, font_file="fonts/f.otf", font_size=30, text_color="#0000ff")]),
    ]
    (root / "template.json").write_text(json.dumps({"size": [400, 300], "layers": layers}), encoding="utf-8")
    (root / "edits.json").write_text("{}", encoding="utf-8")
    spec = {"module_role": "cover", "layers": {"eyebrow": "eyebrow", "title": "title"},
            "text_options": {"eyebrow": {"align_to_layer": "box", "auto_width": True, "padding_x": 10},
                             "title": {"max_chars": 20, "line_chars": 10, "max_lines": 2, "allowed_bounds": [20, 100, 380, 260]}}}
    return root, spec


def ink(image, color, box=(0, 0, 400, 300)):
    mask = Image.new("L", image.size)
    pixels = image.load()
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            if sum(abs(a - b) for a, b in zip(pixels[x, y][:3], color)) < 90:
                mask.putpixel((x, y), 255)
    return mask.getbbox()


def test_anchor_alignment_two_tone_and_badge_fit(bundle, tmp_path):
    root, spec = bundle
    out = tmp_path / "out.jpg"
    render_slide(root, spec, Slide(role="cover", eyebrow="WIDE BADGE", title="첫 줄\n둘째 줄", body=""), out)
    image = Image.open(out).convert("RGB")
    white, blue, red = ink(image, (255, 255, 255), (0, 100, 400, 300)), ink(image, (0, 0, 255)), ink(image, (255, 0, 0))
    assert abs((white[0] + white[2]) / 2 - 200) <= 2  # centred on the Photoshop anchor, not the sample's left edge
    assert blue and blue[1] > white[1]  # second line keeps the template's second style
    badge = ink(image, (255, 255, 255), (0, 0, 400, 100))
    assert red[0] == 20 and abs((red[2] - red[0]) - (badge[2] - badge[0] + 20)) <= 3  # box = text width + padding
    assert abs((badge[0] + badge[2]) / 2 - (red[0] + red[2]) / 2) <= 2


def test_empty_badge_hides_its_box_and_overflow_is_rejected(bundle, tmp_path):
    root, spec = bundle
    out = tmp_path / "out.jpg"
    render_slide(root, spec, Slide(role="cover", eyebrow="", title="짧은 제목", body=""), out)
    assert ink(Image.open(out).convert("RGB"), (255, 0, 0)) is None
    spec["text_options"]["title"]["allowed_bounds"] = [150, 100, 250, 260]
    with pytest.raises(RuntimeError, match="허용 영역"):
        render_slide(root, spec, Slide(role="cover", eyebrow="", title="아주 긴 제목입니다", body=""), out)


def test_ai_badge_colour_switch(bundle, tmp_path):
    root, spec = bundle
    out = tmp_path / "out.jpg"
    slide = Slide(role="cover", eyebrow="EPL", title="제목", body="", accent_color="#00FF00")
    render_slide(root, spec, slide, out)  # switch off: the template's red stays
    assert ink(Image.open(out).convert("RGB"), (255, 0, 0)) and not ink(Image.open(out).convert("RGB"), (0, 255, 0))
    render_slide(root, {**spec, "ai_accent": True, "accent_layer": "box"}, slide, out)
    image = Image.open(out).convert("RGB")
    green = ink(image, (0, 255, 0))
    assert green and not ink(image, (255, 0, 0))
    assert ink(image, (17, 17, 17), green)  # light badge colour gets dark text


def test_paragraph_box_reports_overflowing_lines(bundle):
    root, _ = bundle
    layer = json.loads((root / "template.json").read_text(encoding="utf-8"))["layers"][3]
    layer.update(box=[100, 100, 200, 40], anchor=[100, 100])
    *_, overflow = layout_text(root, layer, {"text": "한 줄\n두 줄\n세 줄"})
    assert overflow
    *_, overflow = layout_text(root, layer, {"text": "한 줄"})
    assert not overflow
