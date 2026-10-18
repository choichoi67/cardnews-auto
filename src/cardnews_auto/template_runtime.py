"""Portable Pillow/NumPy renderer. Copied into each exported template bundle."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageOps

if __package__:
    from .template_primitives import layout_text, gradient_tile, shape_tile
else:
    from template_primitives import layout_text, gradient_tile, shape_tile


def asset_path(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("템플릿 안의 파일만 사용할 수 있습니다.")
    return path


def render(root, changes=None, order=None, bounds=None, assets=None, report=None):
    """`report`, when given, receives text ink boxes ({'ink': {id: (x0, y0, x1, y1)}}) and overflowing box texts."""
    root = Path(root)
    data = json.loads((root / "template.json").read_text(encoding="utf-8"))
    if changes is None and order is None and (root / "edits.json").exists():
        saved = json.loads((root / "edits.json").read_text(encoding="utf-8"))
        changes, order = saved.get("changes"), saved.get("order")
    layers = {item["id"]: item for item in data["layers"]}
    changes = changes or {}
    order = list(layers) if order is None else order
    if len(order) != len(layers) or set(order) != set(layers) or set(changes) - set(layers):
        raise ValueError("레이어 ID 또는 순서가 올바르지 않습니다.")
    canvas = Image.new("RGBA", tuple(data["size"]))
    for key in order:
        layer, edit = layers[key], changes.get(key, {})
        if any(not 1 <= edit[k] <= 4096 for k in ('width', 'height') if k in edit):
            raise ValueError('레이어 크기는 1~4096px이어야 합니다.')
        if edit.get("deleted") or not edit.get("visible", layer["visible"]):
            continue
        x, y = edit.get("x", layer["x"]), edit.get("y", layer["y"])
        text_changed = "text" in edit and edit["text"].replace("\r", "\n") != layer.get("text", "").replace("\r", "\n")
        text_changed |= any(k in edit and edit[k] != layer.get(k) for k in ('text_runs', 'text_effects', 'font_file', 'font_size', 'text_color', 'leading', 'tracking', 'align'))
        generated = False
        if assets and key in assets:
            tile = assets[key].copy()
        elif text_changed:
            if not layer.get("font_file"):
                raise ValueError(f'{layer["name"]}: 원본 폰트가 없어 글자를 바꿀 수 없습니다.')
            # shortcut: horizontal text shaping is approximate; use a shaping engine for warped text.
            if not layer.get("text_editable"):
                raise ValueError("복합 스타일 텍스트는 현재 이미지로만 보존합니다.")
            asset_path(root, edit.get('font_file', layer['font_file']))
            tile, left, top, ink, overflow = layout_text(root, layer, edit)
            # Explicit x/y (badge centring, editor drags) move the tile; otherwise Photoshop's anchor decides.
            dx, dy = (x - left, y - top) if ('x' in edit or 'y' in edit) else (0, 0)
            x, y = left + dx, top + dy
            if report is not None:
                report.setdefault('ink', {})[key] = (ink[0] + dx, ink[1] + dy, ink[2] + dx, ink[3] + dy)
                if overflow:
                    report.setdefault('overflow', []).append(key)
            generated = True
        elif 'gradient' in edit:
            if not layer.get('gradient'):
                raise ValueError('편집 가능한 그라데이션이 아닙니다.')
            tile = gradient_tile((layer['width'], layer['height']), edit['gradient'])
            generated = True
        elif layer.get('shape') and ('shape' in edit or 'width' in edit or 'height' in edit):
            tile = shape_tile((edit.get('width', layer['width']), edit.get('height', layer['height'])),
                              {**layer['shape'], **edit.get('shape', {})})
            generated = True
        else:
            tile = Image.open(asset_path(root, edit.get('asset', layer["asset"]))).convert("RGBA")
        if 'asset' in edit or (assets and key in assets):
            zoom = edit.get('crop_zoom', 1)
            xfocus, yfocus = edit.get('crop_x', .5), edit.get('crop_y', .5)
            if not 1 <= zoom <= 5 or not 0 <= xfocus <= 1 or not 0 <= yfocus <= 1:
                raise ValueError('사진 자르기 범위를 확인하세요.')
            w, h = tile.width / zoom, tile.height / zoom
            left, top = (tile.width - w) * xfocus, (tile.height - h) * yfocus
            tile = tile.crop((round(left), round(top), round(left+w), round(top+h)))
            tile = ImageOps.fit(tile, (edit.get('width', layer['width']), edit.get('height', layer['height'])), Image.Resampling.LANCZOS, centering=(xfocus, yfocus))
        if 'width' in edit or 'height' in edit:
            tile = tile.resize((edit.get('width', tile.width), edit.get('height', tile.height)), Image.Resampling.LANCZOS)
        if edit.get('rotation', 0):
            tile = tile.rotate(-edit['rotation'], resample=Image.Resampling.BICUBIC, expand=True)
        opacity = edit.get("opacity", 1.0)
        if not 0 <= opacity <= 1:
            raise ValueError("불투명도는 0~1이어야 합니다.")
        if generated or 'asset' in edit or (assets and key in assets):
            opacity *= layer.get('source_opacity', 255) / 255
        if opacity != 1:
            tile.putalpha(tile.getchannel("A").point(lambda value: round(value * opacity)))
        if bounds is not None:
            bounds[key] = [int(x), int(y), tile.width, tile.height]
        if report is not None and layer['kind'] == 'type':
            report.setdefault('ink', {}).setdefault(key, (x, y, x + tile.width, y + tile.height))
        blend_mode = edit.get('blend_mode', layer.get('blend_mode', 'NORMAL'))
        if 'blend_mode' in edit and blend_mode not in {'NORMAL', 'MULTIPLY', 'SCREEN', 'OVERLAY', 'DARKEN', 'LIGHTEN', 'SOFT_LIGHT'}:
            raise ValueError('지원하지 않는 혼합 모드입니다.')
        if blend_mode in {'MULTIPLY', 'SCREEN', 'OVERLAY', 'DARKEN', 'LIGHTEN', 'SOFT_LIGHT'}:
            import numpy as np
            overlay = Image.new('RGBA', canvas.size)
            overlay.alpha_composite(tile, (int(x), int(y)))
            a = np.asarray(canvas, dtype=np.float32) / 255
            b = np.asarray(overlay, dtype=np.float32) / 255
            cb, cs = a[..., :3], b[..., :3]
            if blend_mode == 'MULTIPLY':
                mixed = cb * cs
            elif blend_mode == 'SCREEN':
                mixed = 1 - (1-cb)*(1-cs)
            elif blend_mode == 'OVERLAY':
                mixed = np.where(cb <= .5, 2*cb*cs, 1-2*(1-cb)*(1-cs))
            elif blend_mode == 'DARKEN':
                mixed = np.minimum(cb, cs)
            elif blend_mode == 'LIGHTEN':
                mixed = np.maximum(cb, cs)
            else:
                d = np.where(cb <= .25, ((16*cb-12)*cb+4)*cb, np.sqrt(cb))
                mixed = np.where(cs <= .5, cb-(1-2*cs)*cb*(1-cb), cb+(2*cs-1)*(d-cb))
            ab, ass = a[..., 3:4], b[..., 3:4]
            alpha = ass + ab*(1-ass)
            rgb = ((1-ass)*ab*cb+(1-ab)*ass*cs+ab*ass*mixed) / np.maximum(alpha, 1e-8)
            canvas = Image.fromarray(np.clip(np.concatenate((rgb, alpha), axis=2)*255, 0, 255).round().astype('uint8'))
        else:
            canvas.alpha_composite(tile, (int(x), int(y)))
    return canvas


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Photoshop 없이 저장한 PSD 레이어를 합성합니다.")
    parser.add_argument("--output", default="render.png")
    parser.add_argument("--changes", help="JSON file containing changes and optional order")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    options = json.loads(Path(args.changes).read_text(encoding="utf-8")) if args.changes else {}
    render(root, **{k: v for k, v in options.items() if k in {'changes', 'order'}}).save(args.output)
