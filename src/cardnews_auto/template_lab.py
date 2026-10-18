"""PSD import is separate from the portable, Photoshop-free runtime."""
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil

from PIL import ImageChops, ImageFont, ImageStat
from psd_tools import PSDImage
from psd_tools.constants import Resource
from psd_tools.psd.engine_data import List as EngineList

from .template_runtime import render
from .template_properties import properties, rgb

PROJECT_FONTS = Path(__file__).resolve().parents[2] / "templates" / "fonts"


def plain(value):
    if hasattr(value, "items"):
        return {str(plain(key)): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, EngineList)):
        return [plain(item) for item in value]
    return plain(value.value) if hasattr(value, "value") else value


@lru_cache(maxsize=1)
def _font_index():
    """PSDs name fonts by PostScript name; files may be named otherwise, so index both."""
    index = {}
    roots = [PROJECT_FONTS, Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
             Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    for root in roots:
        for path in sorted(root.glob("*")) if root.is_dir() else ():
            if path.suffix.lower() not in {".ttf", ".otf"}:
                continue
            index.setdefault(path.stem.casefold(), path)
            try:
                family, style = ImageFont.truetype(str(path), 10).getname()
            except OSError:
                continue
            index.setdefault(f"{family}-{style}".replace(" ", "").casefold(), path)
            if style in {"Regular", "Normal"}:
                index.setdefault(family.replace(" ", "").casefold(), path)
    return index


def find_font(name):
    return _font_index().get(name.replace(" ", "").casefold())


def _text_geometry(layer, engine, resources):
    """Photoshop placement, ported from PhotoCraft's crates/text/src/psd.rs (MIT/Apache-2.0):
    point text starts at the anchor baseline; paragraph text fills its BoxBounds."""
    xx, xy, yx, yy, tx, ty = (float(v) for v in layer.transform)
    base = (resources.get("StyleSheetSet") or [{}])[0].get("StyleSheetData", {})
    style = {**base, **engine["StyleRun"]["RunArray"][0]["StyleSheet"]["StyleSheetData"]}
    paragraph = engine["ParagraphRun"]["RunArray"][0]["ParagraphSheet"]["Properties"]
    size = float(style.get("FontSize", 12))
    geometry = {
        "anchor": [tx, ty],
        # Horizontal scale is applied after shaping, on top of the layer's own x/y scale.
        "horizontal_scale": float(style.get("HorizontalScale", 1.0)) * (abs(xx) / abs(yy) if yy else 1.0),
        "leading": max(1, (size * float(paragraph.get("AutoLeading", 1.2)) if style.get("AutoLeading", True)
                           else float(style.get("Leading", size * 1.2))) * abs(yy)),
    }
    shapes = engine.get("EngineDict", {}).get("Rendered", {}).get("Shapes", {}).get("Children") or [{}]
    bounds = shapes[0].get("Cookie", {}).get("Photoshop", {}).get("BoxBounds")
    if shapes[0].get("ShapeType") == 1 and bounds and len(bounds) == 4:
        left, top, right, bottom = (float(v) for v in bounds)
        geometry["box"] = [tx + left * xx, ty + top * yy, (right - left) * abs(xx), (bottom - top) * abs(yy)]
    return geometry


def import_psd(source, target, name=None):
    source, target = Path(source), Path(target)
    psd = PSDImage.open(source)
    if psd.width * psd.height > 20_000_000 or len(list(psd.descendants())) > 200:
        raise ValueError("실험실은 2천만 픽셀, 200개 레이어 이하의 PSD를 지원합니다.")
    if psd.color_mode.name != "RGB":
        raise ValueError("실험실에서는 RGB PSD만 지원합니다.")
    for layer in psd.descendants():
        if layer.is_group():
            if (layer.blend_mode.name != "PASS_THROUGH" or layer.opacity != 255
                    or layer.has_mask() or layer.has_vector_mask() or len(layer.effects) or layer.clipping):
                raise ValueError(f"{layer.name}: 마스크·효과·불투명도가 적용된 그룹은 아직 지원하지 않습니다.")
            continue
        if layer.clipping or layer.blend_mode.name not in {"NORMAL", "SOFT_LIGHT"} or layer.kind not in {"pixel", "type", "gradientfill", "shape", "solidcolorfill", "smartobject"}:
            raise ValueError(f"{layer.name}: 그룹·클리핑·비표준 혼합·스마트 오브젝트는 아직 지원하지 않습니다.")
    global_angle = float(psd.image_resources.get_data(Resource.GLOBAL_ANGLE, 120) or 120)
    target.mkdir(parents=True, exist_ok=False)
    try:
        (target / "layers").mkdir()
        (target / "fonts").mkdir()
        reference = psd.topil()
        if reference is None:
            raise ValueError("PSD에 저장된 합성 미리보기가 없습니다.")
        reference.convert("RGBA").save(target / "reference.png")
        data = {"version": 2, "name": name or source.stem, "size": list(psd.size),
                "stack_order": "bottom-to-top", "layers": [], "warnings": [
                    "텍스트·사각형·선형 그라데이션은 속성 편집 후 Python으로 다시 그립니다. 변경하지 않은 레이어는 원본 픽셀을 사용합니다.",
                    "Photoshop 엔진을 복제한 것은 아닙니다. 글자 힌팅·자동 커닝·그라데이션 보간과 디더링은 차이가 날 수 있습니다."]}
        def leaves(container, group_path=()):
            for child in container:
                if child.is_group():
                    yield from leaves(child, (*group_path, child.name))
                else:
                    yield child, group_path

        for index, (layer, group_path) in enumerate(leaves(psd)):
            key = f"layer-{index:03d}"
            viewport = layer.bbox if layer.kind == 'pixel' else (0, 0, *psd.size)
            tile = layer.composite(viewport=viewport, layer_filter=lambda _: True)
            if tile is None:
                raise ValueError(f"{layer.name}: 레이어를 렌더링할 수 없습니다.")
            tile = tile.convert("RGBA")
            box = tile.getbbox() or (0, 0, 1, 1)
            tile.crop(box).save(target / "layers" / f"{key}.png")
            item = {"id": key, "name": layer.name, "kind": layer.kind,
                    "bbox": list(layer.bbox), "x": box[0] + viewport[0], "y": box[1] + viewport[1],
                    "width": box[2] - box[0], "height": box[3] - box[1],
                    "visible": layer.is_visible(), "source_opacity": layer.opacity,
                    "group_path": list(group_path),
                    "blend_mode": layer.blend_mode.name, "effects": [type(e).__name__ for e in layer.effects],
                    "asset": f"layers/{key}.png", "text_editable": False}
            item.update(properties(layer))
            if layer.kind == "type":
                item.update(_import_text(layer, key, target, data["warnings"], global_angle))
            data["layers"].append(item)
        manifest = target / "template.json"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = render(target)
        result.save(target / "preview.png")
        diff = ImageChops.difference(reference.convert("RGB"), result.convert("RGB"))
        data["comparison"] = {"rgb_mae_0_255": sum(ImageStat.Stat(diff).mean) / 3,
                              "exact_pixel_match": diff.getbbox() is None}
        diff.save(target / "difference.png")
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    except Exception:
        shutil.rmtree(target)
        raise


def _import_text(layer, key, target, warnings, global_angle):
    engine, resources = plain(layer.engine_dict), plain(layer.resource_dict)
    styles = engine["StyleRun"]["RunArray"]
    style = styles[0]["StyleSheet"]["StyleSheetData"]
    font_name = resources["FontSet"][int(style.get("Font", 0))]["Name"]
    transform = [float(v) for v in layer.transform]
    scale = abs(transform[3])
    size = max(1, round(float(style.get("FontSize", 12)) * scale))
    values = style.get("FillColor", {}).get("Values", [1, 1, 1, 1])
    item = dict(text=layer.text, font_name=font_name, font_size=size,
                tracking=float(style.get('Tracking', 0)),
                text_color='#%02x%02x%02x' % tuple(round(float(v) * 255) for v in values[1:4]),
                align={0: 'left', 1: 'right', 2: 'center'}.get(int(engine['ParagraphRun']['RunArray'][0]['ParagraphSheet']['Properties'].get('Justification', 0)), 'left'),
                transform=transform, engine_data=engine, resource_data=resources)
    item.update(_text_geometry(layer, engine, resources))
    font = find_font(font_name)
    if not font:
        warnings.append(f"{layer.name}: {font_name} 폰트가 없어 원본 글자 이미지만 보존합니다.")
        return item
    item["font_file"] = f"fonts/{key}{font.suffix.lower()}"
    shutil.copy2(font, target / item["font_file"])
    item['text_runs'] = []
    offset, missing_font = 0, False
    for run_index, (run, length) in enumerate(zip(styles, engine['StyleRun']['RunLengthArray'])):
        rs = run['StyleSheet']['StyleSheetData']
        run_font = find_font(resources['FontSet'][int(rs.get('Font', 0))]['Name'])
        if not run_font:
            missing_font = True
        else:
            run_file = f'fonts/{key}-run-{run_index}{run_font.suffix.lower()}'
            shutil.copy2(run_font, target / run_file)
            run_values = rs.get('FillColor', {}).get('Values', [1, 1, 1, 1])
            item['text_runs'].append({'start': offset, 'end': offset + int(length), 'font_file': run_file,
                                      'font_size': max(1, float(rs.get('FontSize', 12)) * scale),
                                      'text_color': '#%02x%02x%02x' % tuple(round(float(v) * 255) for v in run_values[1:4])})
        offset += int(length)
    item['text_effects'] = {}
    supported = True
    for effect in layer.effects:
        if not effect.enabled:
            continue
        kind = type(effect).__name__
        if kind == 'DropShadow':
            angle = global_angle if getattr(effect, 'use_global_light', False) else float(effect.angle)
            item['text_effects']['shadow'] = dict(color=rgb(effect.color), opacity=float(effect.opacity) / 100,
                distance=float(effect.distance), angle=angle, blur=float(effect.size),
                spread=float(getattr(effect, 'choke', 0) or 0) / 100)
        elif kind == 'Stroke' and effect.position == b'OutF':
            item['text_effects']['stroke'] = dict(color=rgb(effect.color), width=float(effect.size), opacity=float(effect.opacity) / 100)
        else:
            supported = False
    item['text_editable'] = not missing_font and supported and abs(transform[1]) < 1e-6 and abs(transform[2]) < 1e-6
    if item['text_effects']:
        warnings.append(f'{layer.name}: 글자 효과는 PhotoCraft 방식으로 재현합니다. 적용 전 미리보기를 확인하세요.')
    return item
