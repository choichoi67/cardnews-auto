"""Immutable studio releases: Korean copy wrapping and one-slide production rendering."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import textwrap

from PIL import Image, ImageOps

from .template_primitives import layout_text
from .template_runtime import render


def bundle_hash(root):
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file():
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('템플릿 외부 파일은 사용할 수 없습니다.')
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _semantic_wrap_lines(value: str, width: int, break_long_words: bool = False) -> list[str]:
    """Wrap Korean copy without separating short modifiers, names, or quoted works."""
    protected = re.sub(r'([”’"])\s+(이라고|이라는|라고|라며|고|며|은|는|이|가|을|를|도)', r"\1\2", value)
    for pattern in (
        r"[“‘][^”’]+[”’](?:이라고|이라는|라고|라며|고|며)(?:\s+[가-힣]+(?:습니다|입니다)\.?)?",
        r"[《〈“‘][^》〉”’]+[》〉”’]",
        r"(?<![\w가-힣])\d+\s+[가-힣A-Za-z][\w가-힣·'-]*",
        r"(?<![\w가-힣])(?:새|첫|한|두|세)\s+[^\s]+",
    ):
        protected = re.sub(pattern, lambda m: m.group(0).replace(" ", " ") if len(m.group(0)) <= width else m.group(0), protected)
    lines = [line.replace(" ", " ") for line in textwrap.wrap(
        protected, width=width, break_long_words=break_long_words, break_on_hyphens=False)]
    dangling = re.compile(r"(?:라고|라며|고|며|면서|해서|지만)$")
    for index in range(len(lines) - 1):
        words = lines[index].split()
        if not words or not dangling.search(words[-1]):
            continue
        candidate = f"{words[-1]} {lines[index + 1]}"
        if len(words) > 1 and len(candidate) <= width:
            lines[index] = " ".join(words[:-1])
            lines[index + 1] = candidate
    return lines


def wrap_text(value: str, options: dict) -> str:
    """Fit copy to a template text slot without shrinking the font. Lines are joined with \\r."""
    preserve_manual_lines = bool(options.get("preserve_manual_lines", False))
    strict = bool(options.get("strict_korean_wrap", False))
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [" ".join(part.split()) for part in (normalized.split("\n") if preserve_manual_lines else [normalized.replace("\n", " ")]) if part.strip()]
    value = "\n".join(paragraphs)
    max_chars = int(options.get("max_chars", 0))
    if max_chars and len(value.replace("\n", "")) > max_chars:
        if strict:
            raise RuntimeError(f"한국어 줄맞춤 규칙: 본문이 템플릿 허용 길이 {max_chars}자를 넘었습니다. "
                               "어절을 자르지 않고 원고를 더 짧게 다시 작성해야 합니다.")
        value = value.replace("\n", " ")[: max(1, max_chars - 1)].rstrip() + "…"
        paragraphs = [value]
    line_chars = int(options.get("line_chars", 0))
    max_lines = int(options.get("max_lines", 0))
    if not line_chars and max_chars and max_lines:
        line_chars = max(1, max_chars // max_lines)
    if not line_chars:
        return value.replace("\n", "\r")
    oversized = next((w for p in paragraphs for w in p.split() if len(w) > line_chars), "")
    if oversized and strict:
        raise RuntimeError(f"한국어 줄맞춤 규칙: '{oversized}' 어절이 한 줄 허용 길이 {line_chars}자를 넘었습니다. "
                           "단어를 자르지 말고 더 짧은 표현을 사용해야 합니다.")
    lines = [line for p in paragraphs for line in _semantic_wrap_lines(p, line_chars, break_long_words=not strict)]
    max_lines = max_lines or len(lines)
    if len(lines) > max_lines:
        if strict:
            raise RuntimeError(f"한국어 줄맞춤 규칙: 본문이 템플릿 허용 줄 수 {max_lines}줄을 넘었습니다. "
                               "어절을 자르지 않고 원고를 더 짧게 다시 작성해야 합니다.")
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(1, line_chars - 1)].rstrip() + "…"
    return "\r".join(lines)


def text_options(spec: dict, field: str) -> dict:
    options = dict(spec.get("text_options", {}).get(field, {}))
    role = spec.get("module_role")
    if role == "cover" and field == "title":
        # The writer chooses the two-line break of the cover headline; keep it.
        options.update(preserve_manual_lines=True, max_lines=2)
    elif role == "body" and field in {"title", "body"}:
        options.update(preserve_manual_lines=True, strict_korean_wrap=True)
    return options


def _inside(ink, area, tolerance=4):
    return (ink[0] >= area[0] - tolerance and ink[1] >= area[1] - tolerance
            and ink[2] <= area[2] + tolerance and ink[3] <= area[3] + tolerance)


def render_slide(root, spec, slide, output, photo=None, page=""):
    """Fill one immutable template version with a slide's copy and photo, then check the text fits."""
    root = Path(root)
    data = json.loads((root / 'template.json').read_text(encoding='utf-8'))
    options = json.loads((root / 'edits.json').read_text(encoding='utf-8'))
    changes = deepcopy(options.get('changes', {}))
    layers = {layer['id']: layer for layer in data['layers']}
    width, height = data['size']
    fixed = spec.get('preserve_original')
    image_key = spec.get('image_layer')
    if not fixed:
        for key in spec.get('hide_layers', []):
            changes.setdefault(key, {})['visible'] = False
        # Sample photos/cut-outs of the template never show behind a new article image.
        for key in spec.get('image_layers', []):
            if key != image_key:
                changes.setdefault(key, {})['visible'] = False
    assets = {}
    if image_key and not fixed:
        if photo:
            with Image.open(photo) as source:
                assets[image_key] = ImageOps.exif_transpose(source).convert('RGBA')
            changes.setdefault(image_key, {})['visible'] = True
            if spec.get('image_fit') == 'exact':
                # Production photos arrive as validated 1080×1440 frames; anything else is cover-fitted.
                changes[image_key].update(x=0, y=0, width=width, height=height)
        else:
            changes.setdefault(image_key, {})['visible'] = False

    accent, accent_key = getattr(slide, 'accent_color', ''), spec.get('accent_layer')
    if not fixed and spec.get('ai_accent') and accent_key in layers and re.fullmatch(r'#[0-9A-Fa-f]{6}', accent or ''):
        # "AI 배지 색" switch: the article's colour fills the badge box; its text takes a readable contrast.
        box_edit = changes.setdefault(accent_key, {})
        box_edit['shape'] = {**layers[accent_key].get('shape', {}), **box_edit.get('shape', {}), 'fill': accent}
        red, green, blue = (int(accent[i:i + 2], 16) for i in (1, 3, 5))
        contrast = '#111111' if 0.2126 * red + 0.7152 * green + 0.0722 * blue > 150 else '#FFFFFF'
        for field, key in spec.get('layers', {}).items():
            if spec.get('text_options', {}).get(field, {}).get('align_to_layer') == accent_key:
                changes.setdefault(key, {})['text_color'] = contrast
    values = {} if fixed else {"eyebrow": slide.eyebrow, "title": slide.title, "body": slide.body, "page": page}
    areas = {}
    for field, key in ({} if fixed else spec.get('layers', {})).items():
        opts = text_options(spec, field)
        box_key = opts.get('align_to_layer')
        if not str(values.get(field, '')).strip():
            changes.setdefault(key, {})['visible'] = False
            if box_key:
                changes.setdefault(box_key, {})['visible'] = False
            continue
        edit = changes.setdefault(key, {})
        edit.update(text=wrap_text(values[field], {**opts, 'strict_korean_wrap': True}).replace('\r', '\n'), visible=True)
        layer = layers[key]
        areas[field] = opts.get('allowed_bounds') or (
            [layer['box'][0], layer['box'][1], layer['box'][0] + layer['box'][2], layer['box'][1] + layer['box'][3]]
            if layer.get('box') else [layer['x'], layer['y'], layer['x'] + layer['width'], layer['y'] + layer['height']])
        if box_key:  # badge: the box follows the text width and the text sits in its centre
            box = layers[box_key]
            box_edit = changes.setdefault(box_key, {})
            box_edit['visible'] = True
            _, left, top, ink, _ = layout_text(root, layer, edit)
            bx, by = box_edit.get('x', box['x']), box_edit.get('y', box['y'])
            bw, bh = box_edit.get('width', box['width']), box_edit.get('height', box['height'])
            if opts.get('auto_width', field == 'eyebrow'):
                bw = min(round(ink[2] - ink[0] + 2 * float(opts.get('padding_x', 18))), width - bx)
                box_edit['width'] = bw
            edit['x'] = round(left + bx + bw / 2 + float(opts.get('offset_x', 0)) - (ink[0] + ink[2]) / 2)
            edit['y'] = round(top + by + bh / 2 + float(opts.get('offset_y', 0)) - (ink[1] + ink[3]) / 2)
            areas[field] = [bx, by, bx + bw, by + bh]

    report = {}
    image = render(root, changes=changes, order=options.get('order'), assets=assets, report=report)
    keys = {} if fixed else {f: k for f, k in spec.get('layers', {}).items() if f in areas}
    for field, key in keys.items():
        if key in report.get('overflow', []):
            raise RuntimeError(f"{field} 문구가 템플릿 글상자를 넘칩니다. 문구를 줄이거나 템플릿을 조정하세요.")
        ink = report['ink'][key]
        if not _inside(ink, areas[field]) or not _inside(ink, (0, 0, width, height)):
            raise RuntimeError(f"{field} 문구가 템플릿 허용 영역을 벗어났습니다. 글꼴·크기·좌표는 변경하지 않았습니다. "
                               "문구를 줄이거나 더 넓은 템플릿을 선택하세요.")
    checked = [(field, report['ink'][key]) for field, key in keys.items()]
    for index, (field, a) in enumerate(checked):
        for other, b in checked[:index]:
            if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                raise RuntimeError(f"{field}와 {other} 글 영역이 겹칩니다. 문구나 템플릿을 조정하세요.")
    image.convert('RGB').save(output, quality=95, subsampling=0)
    return output
