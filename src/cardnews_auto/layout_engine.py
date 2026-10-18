"""Content-aware card-news layout engine.

This module deliberately has no dependency on Photoshop or a particular PSD.  It
turns a slide and an optional source image into a deterministic layout plan and
renders it with Pillow.  A vision model can later replace :meth:`plan`; the
renderer remains unchanged.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int, bold: bool = False, configured_path: str | None = None):
    path = Path(configured_path) if configured_path else Path("C:/Windows/Fonts") / ("malgunbd.ttf" if bold else "malgun.ttf")
    if not path.is_file():
        raise RuntimeError(f"출력 글꼴을 찾을 수 없습니다: {path.name}")
    return ImageFont.truetype(str(path), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    lines = []
    for paragraph in text.splitlines():
        current = ""
        for word in paragraph.split():
            if draw.textlength(word, font=font) > width:
                raise RuntimeError("한 단어가 글자 영역보다 넓습니다. 문구 또는 템플릿을 조정하세요.")
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=font) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return "\n".join(lines)


def render_auto_layout(
    role: str, title: str, body: str = "", image: Image.Image | None = None,
    output: Path | None = None, size: tuple[int, int] = (1080, 1440), margin: int = 72,
    eyebrow_text: str = "", accent_color: str = "#35e3e3",
    font_path: str | None = None, bold_font_path: str | None = None,
    exact_background: bool = False,
) -> Image.Image:
    w, h = size
    if role == "cover":
        image_box = (0, 0, w, int(h * .68)) if image else None
        title_top, title_size, body_size = int(h * (.64 if image else .2)), 86, 38
    elif role == "cta":
        image_box, title_top, title_size, body_size = None, int(h * .28), 76, 42
    else:
        image_box = (margin, margin, w - margin, int(h * .48)) if image else None
        title_top, title_size, body_size = int(h * (.53 if image else .16)), 66, 38
    title_box = (margin, title_top, w - margin, h - margin)
    canvas = Image.new("RGB", size, "#081b1d")
    draw = ImageDraw.Draw(canvas)
    if image and image_box:
        box, source = image_box, image.convert("RGB")
        if exact_background:
            if source.size != size:
                raise RuntimeError("발행 배경과 캔버스 크기가 다릅니다.")
            box = (0, 0, w, h)
        scale = max((box[2] - box[0]) / source.width, (box[3] - box[1]) / source.height)
        source = source.resize(
            (max(1, int(source.width * scale)), max(1, int(source.height * scale))),
            Image.Resampling.LANCZOS,
        )
        left, top = (source.width - (box[2] - box[0])) // 2, (source.height - (box[3] - box[1])) // 2
        canvas.paste(source.crop((left, top, left + box[2] - box[0], top + box[3] - box[1])), box[:2])
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle((0, title_top - 30 if exact_background else box[3] - 80, w, h), fill=(8, 27, 29, 235))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(canvas)
    if role == "cover" and eyebrow_text:
        eyebrow = (margin, max(24, title_top - 62), margin + 220, title_top - 12)
        color = accent_color
        label = eyebrow_text
        draw.rounded_rectangle(eyebrow, radius=4, fill=color)
        label_font = _font(30, True, bold_font_path)
        box = draw.textbbox((0, 0), label, font=label_font)
        draw.text(
            (
                eyebrow[0] + (eyebrow[2] - eyebrow[0] - (box[2] - box[0])) / 2,
                eyebrow[1] + (eyebrow[3] - eyebrow[1] - (box[3] - box[1])) / 2 - box[1],
            ),
            label,
            font=label_font,
            fill="#111111" if sum(int(color[i:i + 2], 16) for i in (1, 3, 5)) > 450 else "white",
        )
    title_font, body_font = _font(title_size, True, bold_font_path), _font(body_size, False, font_path)
    wrapped_title = _wrap(draw, title, title_font, w - 2 * margin)
    title_bounds = draw.multiline_textbbox(title_box[:2], wrapped_title, font=title_font, spacing=10)
    body_top = title_bounds[3] + 28 if title else title_top
    wrapped_body = _wrap(draw, body, body_font, w - 2 * margin)
    body_bounds = draw.multiline_textbbox((margin, body_top), wrapped_body, font=body_font, spacing=12)
    if title_bounds[3] > h - margin or (body and body_bounds[3] > h - margin):
        raise RuntimeError("카드 글이 출력 영역을 넘습니다. 문장을 다음 카드로 나누거나 문구를 조정하세요.")
    draw.multiline_text(title_box[:2], wrapped_title, font=title_font, fill="#35f0ee", spacing=10)
    if body:
        draw.multiline_text((margin, body_top), wrapped_body, font=body_font, fill="white", spacing=12)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output, quality=95)
    return canvas
