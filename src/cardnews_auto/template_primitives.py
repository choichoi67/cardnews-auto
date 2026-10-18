"""Editable text, rectangle and linear-gradient painting for the template renderer.

Text placement and layer-style math follow PhotoCraft (github.com/storytold/photocraft, MIT/Apache-2.0):
crates/text/src/layout.rs (anchor/box baselines, horizontal scale after shaping, box overflow) and
crates/compose/src/effects.rs (whole-pixel shadow offset, spread split, two-box "tent" blur).
"""
import math
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageFilter

SS = 2  # supersampling factor for glyph rendering


def _tent(alpha, width):
    """Photoshop's soft falloff: two box passes of `width` px (PhotoCraft effects.rs `tent`)."""
    if width <= 1:
        return alpha
    blur = ImageFilter.BoxBlur((width - 1) / 2)
    return alpha.filter(blur).filter(blur)


def _style_at(layer, edit, index, replaced, first_break):
    runs = edit.get('text_runs', layer.get('text_runs') or [])
    if replaced and runs:
        # Replaced copy keeps the template's two-tone design: line one takes the first style,
        # the following lines the second (the production rule of the former Photoshop renderer).
        style = runs[1] if len(runs) > 1 and 0 <= first_break < index else runs[0]
    else:
        style = next((r for r in runs if r['start'] <= index < r['end']), runs[-1] if runs else {})
    return (edit.get('font_file', style.get('font_file', layer.get('font_file'))),
            edit.get('font_size', style.get('font_size', layer['font_size'])),
            edit.get('text_color', style.get('text_color', layer.get('text_color', '#ffffff'))))


def layout_text(root, layer, edit):
    """Render a text layer. Returns (tile, left, top, ink box (x0, y0, x1, y1), overflow)."""
    root = Path(root)
    text = edit.get('text', layer.get('text', '')).replace('\r', '\n')
    if len(text) > 2000 or text.count('\n') >= 100:
        raise ValueError('글자는 2000자, 100줄 이내로 입력하세요.')
    replaced = text != layer.get('text', '').replace('\r', '\n')
    hs = float(layer.get('horizontal_scale', 1.0)) or 1.0
    fonts, lines, cursor = {}, [[]], 0
    for char in text:
        if char == '\n':
            lines.append([])
        else:
            file, size, color = _style_at(layer, edit, cursor, replaced, text.find('\n'))
            if not 1 <= size <= 512:
                raise ValueError('글자 크기는 1~512px이어야 합니다.')
            path = (root / file).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError('템플릿 폰트 파일을 찾을 수 없습니다.')
            key = (str(path), round(size * SS))
            if key not in fonts:
                fonts[key] = ImageFont.truetype(*key)
            tracking = edit.get('tracking', layer.get('tracking', 0)) / 1000 * size * SS
            lines[-1].append((char, fonts[key], color, tracking))
        cursor += 1

    def advances(line):
        out = []
        for i, (char, font, _, tracking) in enumerate(line):
            nxt = line[i + 1] if i + 1 < len(line) else None
            pair = font.getlength(char + nxt[0]) - font.getlength(nxt[0]) if nxt and nxt[1] is font else font.getlength(char)
            out.append(pair + tracking)
        return out

    box = layer.get('box')
    if box:  # paragraph text wraps at the box width (after horizontal scale), word by word
        wrapped = []
        for line in lines:
            current, width, words, word = [], 0.0, [], []
            for item in line + [(' ', None, None, 0)]:
                word.append(item)
                if item[0] == ' ':
                    words.append(word)
                    word = []
            for word in words:
                w = sum(advances([c for c in word if c[1]])) * hs / SS
                if current and width + w > box[2] + 0.5:
                    wrapped.append(current)
                    current, width = [], 0.0
                current += [c for c in word if c[1]]
                width += w
            wrapped.append(current)
        lines = wrapped
    leading = edit.get('leading', layer.get('leading', layer['font_size'] * 1.2))
    align = edit.get('align', layer.get('align', 'left'))
    effects = edit.get('text_effects', layer.get('text_effects') or {})
    shadow, stroke = effects.get('shadow') or {}, effects.get('stroke') or {}
    stroke_px = stroke.get('width', 0) if stroke.get('opacity', 1) else 0

    placed, overflow, baseline = [], False, None
    for line in lines:
        adv = advances(line)
        width = sum(adv) * hs / SS
        ascent = max((f.getmetrics()[0] for _, f, _, _ in line), default=round(layer['font_size'] * SS * .8)) / SS
        descent = max((f.getmetrics()[1] for _, f, _, _ in line), default=round(layer['font_size'] * SS * .2)) / SS
        if baseline is None:
            if box:  # first baseline: tallest ascender touches the box top (Photoshop "Ascent")
                ink_tops = [-f.getbbox(c, anchor='ls')[1] for c, f, _, _ in line if c.strip()]
                baseline = box[1] + (max(ink_tops) / SS if ink_tops else ascent)
            elif 'anchor' in layer:
                baseline = layer['anchor'][1]
            else:  # drafts imported before anchors were stored: keep the old top-left placement
                baseline = layer['y'] + ascent
        else:
            baseline += leading
        if box and baseline > box[1] + box[3] + 0.5:
            overflow = True
            break
        if box:
            origin = box[0] + {'left': 0, 'center': (box[2] - width) / 2, 'right': box[2] - width}.get(align, 0)
        else:
            ax = layer['anchor'][0] if 'anchor' in layer else layer['x'] + {'center': layer['width'] / 2, 'right': layer['width']}.get(align, 0)
            origin = ax - {'left': 0, 'center': width / 2, 'right': width}.get(align, 0)
        placed.append((line, adv, origin, baseline, ascent, descent, width))
    if not placed:
        return Image.new('RGBA', (1, 1)), 0, 0, (0, 0, 0, 0), overflow

    pad = math.ceil(stroke_px + shadow.get('distance', 0) + shadow.get('blur', 0) * 2 + 4)
    left = math.floor(min(p[2] for p in placed)) - pad
    top = math.floor(min(p[3] - p[4] for p in placed)) - pad
    right = math.ceil(max(p[2] + p[6] for p in placed)) + pad
    bottom = math.ceil(max(p[3] + p[5] for p in placed)) + pad
    size = ((right - left) * SS, (bottom - top) * SS)
    if size[0] * size[1] > 80_000_000:
        raise ValueError('텍스트 영역이 너무 큽니다.')
    fill, outline = Image.new('RGBA', size), Image.new('RGBA', size)
    for line, adv, origin, base, *_ in placed:
        # Draw unscaled, then stretch the line by the horizontal scale (applied after shaping).
        raw_w = max(1, math.ceil(sum(adv) + stroke_px * SS * 2) + 2)
        raw_h = size[1]
        line_fill, line_stroke = Image.new('RGBA', (raw_w, raw_h)), Image.new('RGBA', (raw_w, raw_h))
        draw_fill, draw_stroke = ImageDraw.Draw(line_fill), ImageDraw.Draw(line_stroke)
        x = stroke_px * SS + 1
        y = (base - top) * SS
        for (char, font, color, _), a in zip(line, adv):
            draw_fill.text((x, y), char, font=font, fill=color, anchor='ls')
            if stroke_px:
                draw_stroke.text((x, y), char, font=font, fill=stroke.get('color', '#000000'), anchor='ls',
                                 stroke_width=round(stroke_px * SS), stroke_fill=stroke.get('color', '#000000'))
            x += a
        scaled_w = max(1, round(raw_w * hs))
        at = (round((origin - left) * SS - (stroke_px * SS + 1) * hs), 0)
        if abs(hs - 1) > 1e-3:
            line_fill = line_fill.resize((scaled_w, raw_h), Image.Resampling.LANCZOS)
            line_stroke = line_stroke.resize((scaled_w, raw_h), Image.Resampling.LANCZOS)
        fill.alpha_composite(line_fill, at)
        outline.alpha_composite(line_stroke, at)
    ink_box = fill.getbbox() or (0, 0, 1, 1)
    ink = (left + ink_box[0] / SS, top + ink_box[1] / SS, left + ink_box[2] / SS, top + ink_box[3] / SS)
    tile = Image.new('RGBA', size)
    if stroke_px:
        outline.putalpha(outline.getchannel('A').point(lambda a: round(a * stroke.get('opacity', 1))))
    body = Image.alpha_composite(outline, fill) if stroke_px else fill
    if shadow and shadow.get('opacity', 0):
        angle = math.radians(shadow.get('angle', 120))
        dx, dy = round(-math.cos(angle) * shadow.get('distance', 0)), round(math.sin(angle) * shadow.get('distance', 0))
        spread = shadow.get('spread', 0)
        grow = round(shadow.get('blur', 0) * spread)
        alpha = body.getchannel('A')
        if grow:
            alpha = alpha.filter(ImageFilter.MaxFilter(grow * SS * 2 + 1))
        alpha = _tent(alpha, shadow.get('blur', 0) * (1 - spread) * SS)
        alpha = alpha.point(lambda a: round(a * shadow['opacity']))
        cast = Image.new('RGBA', size, shadow.get('color', '#000000'))
        cast.putalpha(alpha)
        tile.alpha_composite(cast, (dx * SS, dy * SS))
    tile.alpha_composite(body)
    tile = tile.resize((right - left, bottom - top), Image.Resampling.LANCZOS)
    return tile, left, top, ink, overflow


def gradient_tile(size, settings):
    import numpy as np
    width, height = size
    theta = math.radians(settings['angle'])
    length = (abs(width * math.cos(theta)) + abs(height * math.sin(theta))) * settings['scale'] / 100
    if length <= 0:
        raise ValueError('그라데이션 배율은 0보다 커야 합니다.')
    x = np.arange(width, dtype=np.float32)[None, :] - width * (.5 + settings['offset_x'] / 100)
    y = np.arange(height, dtype=np.float32)[:, None] - height * (.5 + settings['offset_y'] / 100)
    t = np.clip(.5 + (x * math.cos(theta) - y * math.sin(theta)) / length, 0, 1)
    if settings.get('reverse'):
        t = 1 - t

    def interpolate(stops, values):
        result = np.full(t.shape, values[0], dtype=np.float32)
        for i in range(len(stops) - 1):
            start, end = stops[i]['position'], stops[i + 1]['position']
            local = np.clip((t - start) / max(1e-8, end - start), 0, 1)
            midpoint = max(.01, min(.99, stops[i].get('midpoint', .5)))
            local = np.where(local < midpoint, local * .5 / midpoint, .5 + (local - midpoint) * .5 / (1 - midpoint))
            result = np.where(t >= start, values[i] + local * (values[i + 1] - values[i]), result)
        return result

    colors, alphas = settings['colors'], settings['alphas']
    channels = [interpolate(colors, [ImageColor.getrgb(s['color'])[c] for s in colors]) for c in range(3)]
    channels.append(interpolate(alphas, [s['opacity'] * 255 for s in alphas]))
    return Image.fromarray(np.clip(np.stack(channels, axis=2), 0, 255).round().astype('uint8'))


def shape_tile(size, settings):
    width, height = size
    if width * height > 4_000_000:
        raise ValueError('도형은 400만 픽셀 이내로 설정하세요.')
    tile = Image.new('RGBA', (width * 4, height * 4))
    ImageDraw.Draw(tile).rectangle((0, 0, width * 4 - 1, height * 4 - 1),
        fill=settings['fill'] if settings['fill_enabled'] else None,
        outline=settings['stroke'] if settings['stroke_enabled'] and settings['stroke_width'] > 0 else None,
        width=max(1, round(settings['stroke_width'] * 4)))
    return tile.resize((width, height), Image.Resampling.LANCZOS)
