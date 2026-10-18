import asyncio
from io import BytesIO
import json
from pathlib import Path
import re
import threading
import uuid
import zipfile
import hashlib
import os
import shutil
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from PIL import Image, ImageOps

from .dashboard_core import (MAGAZINES_DIR, profile, save_psd_upload, _atomic_json,
                             registry, profile_config, CONFIG_LOCK)
from .template_publication import bundle_hash
from . import template_store
from .template_lab import import_psd
from .template_runtime import render

router = APIRouter(prefix="/api/template-lab")
_lock = threading.Lock()


class ColorStop(BaseModel):
    model_config = ConfigDict(extra='forbid')
    position: float = Field(ge=0, le=1)
    color: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    midpoint: float = Field(default=.5, gt=0, lt=1)


class AlphaStop(BaseModel):
    model_config = ConfigDict(extra='forbid')
    position: float = Field(ge=0, le=1)
    opacity: float = Field(ge=0, le=1)
    midpoint: float = Field(default=.5, gt=0, lt=1)


class Gradient(BaseModel):
    model_config = ConfigDict(extra='forbid')
    angle: float = Field(ge=-360, le=360)
    scale: float = Field(gt=0, le=1000)
    offset_x: float = Field(ge=-100, le=100)
    offset_y: float = Field(ge=-100, le=100)
    reverse: bool = False
    colors: list[ColorStop] = Field(min_length=2, max_length=32)
    alphas: list[AlphaStop] = Field(min_length=2, max_length=32)

    @model_validator(mode='after')
    def ordered(self):
        for stops in (self.colors, self.alphas):
            if any(a.position >= b.position for a, b in zip(stops, stops[1:])):
                raise ValueError('그라데이션 정지점은 위치가 증가해야 합니다.')
        return self


class Shape(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fill: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    stroke: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    stroke_width: float = Field(ge=0, le=100)
    fill_enabled: bool = True
    stroke_enabled: bool = True


class TextRun(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: int = Field(ge=0, le=2000)
    end: int = Field(ge=1, le=2001)
    font_file: str = Field(pattern=r'^fonts/[a-zA-Z0-9_-]+\.(ttf|otf)$')
    font_size: float = Field(ge=1, le=512)
    text_color: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')


class Shadow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    color: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    opacity: float = Field(ge=0, le=1)
    distance: float = Field(ge=0, le=200)
    angle: float = Field(ge=-360, le=360)
    blur: float = Field(ge=0, le=200)


class Stroke(BaseModel):
    model_config = ConfigDict(extra='forbid')
    color: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    width: float = Field(ge=0, le=100)
    opacity: float = Field(ge=0, le=1)


class TextEffects(BaseModel):
    model_config = ConfigDict(extra='forbid')
    shadow: Shadow | None = None
    stroke: Stroke | None = None


class LayerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible: bool | None = None
    locked: bool | None = None
    deleted: bool | None = None
    text_runs: list[TextRun] | None = Field(default=None, max_length=100)
    text_effects: TextEffects | None = None
    text: str | None = Field(default=None, max_length=2000)
    x: int | None = Field(default=None, ge=-10000, le=10000)
    y: int | None = Field(default=None, ge=-10000, le=10000)
    opacity: float | None = Field(default=None, ge=0, le=1)
    blend_mode: Literal['NORMAL', 'MULTIPLY', 'SCREEN', 'OVERLAY', 'DARKEN', 'LIGHTEN', 'SOFT_LIGHT'] | None = None
    width: int | None = Field(default=None, ge=1, le=4096)
    height: int | None = Field(default=None, ge=1, le=4096)
    rotation: float | None = Field(default=None, ge=-360, le=360)
    font_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{16}$')
    font_file: str | None = Field(default=None, pattern=r'^fonts/[a-zA-Z0-9_-]+\.(ttf|otf)$')
    font_size: float | None = Field(default=None, ge=1, le=512)
    leading: float | None = Field(default=None, ge=1, le=1024)
    tracking: float | None = Field(default=None, ge=-200, le=1000)
    text_color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')
    align: Literal['left', 'center', 'right'] | None = None
    shape: Shape | None = None
    gradient: Gradient | None = None
    asset: str | None = Field(default=None, pattern=r'^layers/replacement-[0-9a-f]{32}\.png$')
    crop_x: float | None = Field(default=None, ge=0, le=1)
    crop_y: float | None = Field(default=None, ge=0, le=1)
    crop_zoom: float | None = Field(default=None, ge=1, le=5)


class Edits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, LayerEdit] = Field(default_factory=dict, max_length=200)
    order: list[str] | None = Field(default=None, max_length=200)
    revision: int | None = Field(default=None, ge=0)


class Publish(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['cover', 'body', 'cta']
    revision: int = Field(ge=0)
    expected_active: str = ''
    text_layer: str = ''
    image_layer: str = ''
    max_chars: int = Field(default=80, ge=1, le=2000)
    line_chars: int = Field(default=20, ge=1, le=200)
    max_lines: int = Field(default=4, ge=1, le=30)
    # Cover extras: badge text with its box (box width follows the text) and a one-line caption.
    eyebrow_layer: str = ''
    badge_layer: str = ''
    caption_layer: str = ''
    caption_max_chars: int = Field(default=18, ge=1, le=200)
    allowed_bounds: dict[str, list[float]] = Field(default_factory=dict)
    hide_layers: list[str] = Field(default_factory=list, max_length=200)
    image_layers: list[str] = Field(default_factory=list, max_length=200)


def publish_spec(payload: Publish, layers: dict, size: list, previous: dict | None = None) -> dict:
    """Production mapping for a cover/body version: text fields, limits, allowed areas and photo layers."""
    previous = previous or {}
    field = 'title' if payload.role == 'cover' else 'body'
    mapping = {field: payload.text_layer}
    options = {field: {'max_chars': payload.max_chars, 'line_chars': payload.line_chars, 'max_lines': payload.max_lines}}
    if payload.role == 'cover' and payload.eyebrow_layer:
        mapping['eyebrow'] = payload.eyebrow_layer
        options['eyebrow'] = {'max_chars': 8, 'line_chars': 8, 'max_lines': 1}
        if payload.badge_layer:
            options['eyebrow'].update(align_to_layer=payload.badge_layer, auto_width=True, padding_x=18)
    if payload.role == 'cover' and payload.caption_layer:
        mapping['body'] = payload.caption_layer
        options['body'] = {'max_chars': payload.caption_max_chars, 'line_chars': payload.caption_max_chars, 'max_lines': 1}
    for name, key in mapping.items():
        old_options = previous.get('text_options', {}).get(name, {}) if previous.get('layers', {}).get(name) == key else {}
        if old_options:
            controlled = options[name] if name == field else {}
            if name == 'eyebrow' and payload.badge_layer:
                controlled = {'align_to_layer': payload.badge_layer}
            if name == 'body' and payload.role == 'cover' and 'caption_max_chars' in payload.model_fields_set:
                controlled = {key: payload.caption_max_chars for key in ('max_chars', 'line_chars')}
            options[name] = {**old_options, **controlled}
            if name == 'eyebrow' and not payload.badge_layer:
                options[name].pop('align_to_layer', None)
        layer = layers.get(key, {})
        if not layer.get('text_editable'):
            raise HTTPException(400, f'{name}: 편집 가능한 글자 레이어를 지정하세요.')
        bounds = payload.allowed_bounds.get(name, options[name].get('allowed_bounds'))
        if bounds is None and not layer.get('box') and not old_options:
            # Default area: full width with margins, from the sample's top down to the allowed line count.
            lines = options[name]['max_lines']
            bounds = [20, layer['y'] - 10, size[0] - 20, min(size[1], layer['y'] + layer.get('leading', layer['font_size'] * 1.2) * lines + 20)]
        if bounds is not None:
            options[name]['allowed_bounds'] = [float(v) for v in bounds]
    hidden = payload.hide_layers if 'hide_layers' in payload.model_fields_set else previous.get('hide_layers', [])
    images = payload.image_layers if 'image_layers' in payload.model_fields_set else previous.get('image_layers', [])
    unknown = [k for k in [payload.badge_layer, *hidden, *images] if k and k not in layers]
    if unknown:
        raise HTTPException(400, '존재하지 않는 레이어를 지정했습니다.')
    return {'layers': mapping, 'text_options': options, 'image_layer': payload.image_layer, 'image_fit': 'exact',
            'image_layers': list(dict.fromkeys([payload.image_layer, *images])),
            'hide_layers': hidden}


@router.get('/channels')
def channels():
    return [{'id': item['id'], 'name': item['name']} for item in registry()]


@router.get('/{profile_id}/catalog')
def catalog(profile_id: str):
    cfg = profile_config(profile(profile_id))
    active = template_store.active_set(MAGAZINES_DIR.parent, profile_id, cfg.get('template', {}).get('approved_set'))
    history = [json.loads(p.read_text(encoding='utf-8')) for p in folder(profile_id).glob('*/versions/*.json')]
    slots = {role: dict(active.get(role) or {}) for role in ('cover', 'body', 'cta')}
    for spec in slots.values():
        if spec.get('renderer') != 'template-lab' or not spec.get('draft_id'):
            continue
        draft = detail(folder(profile_id, spec['draft_id']))
        saved = json.loads((Path(spec['bundle_path']) / 'edits.json').read_text(encoding='utf-8'))
        default_order = [layer['id'] for layer in draft['layers']]
        spec['draft_matches_active'] = all(
            draft['edits'].get(key, default) == saved.get(key, default)
            for key, default in [('changes', {}), ('order', default_order)]
        )
    cover = slots['cover']
    box = cover.get('accent_layer') or cover.get('text_options', {}).get('eyebrow', {}).get('align_to_layer')
    if box and cover.get('bundle_path'):
        # Show the badge layer by name so the user can see which layer the AI colour switch paints.
        layers = json.loads((Path(cover['bundle_path']) / 'template.json').read_text(encoding='utf-8'))['layers']
        cover['accent_layer_name'] = next((l['name'] for l in layers if l['id'] == box), box)
    return {'active': slots, 'history': history}


@lru_cache(maxsize=1)
def fonts():
    roots = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts',
             Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/Windows/Fonts']
    return {hashlib.sha256(str(p).encode()).hexdigest()[:16]: p
            for root in roots for p in sorted(root.glob('*')) if p.suffix.lower() in {'.ttf', '.otf'}}


@router.get('/fonts')
def font_list():
    return [{'id': key, 'name': path.stem} for key, path in fonts().items()]


def options_for(root, edits):
    options = edits.model_dump(exclude_none=True, exclude={'revision'})
    layers = {l['id']: l for l in detail(root)['layers']}
    for key, change in options['changes'].items():
        if key not in layers:
            raise ValueError('알 수 없는 레이어입니다.')
        layer = layers[key]
        if any(k in change for k in ('text', 'text_runs', 'text_effects', 'font_id', 'font_file', 'font_size', 'leading', 'tracking', 'text_color', 'align')) and not layer.get('text_editable'):
            raise ValueError('편집 가능한 텍스트 레이어가 아닙니다.')
        if 'shape' in change and not layer.get('shape'):
            raise ValueError('편집 가능한 사각형 레이어가 아닙니다.')
        if 'gradient' in change and not layer.get('gradient'):
            raise ValueError('편집 가능한 그라데이션 레이어가 아닙니다.')
        if 'asset' in change and layer['kind'] not in {'pixel', 'smartobject'}:
            raise ValueError('사진 교체는 픽셀 레이어에서만 가능합니다.')
        if 'font_id' in change:
            path = fonts().get(change['font_id'])
            if not path:
                raise ValueError('설치된 폰트를 찾을 수 없습니다.')
            change['font_file'] = f"fonts/{change['font_id']}{path.suffix.lower()}"
            if not (root / change['font_file']).exists():
                shutil.copy2(path, root / change['font_file'])
    return options


def folder(profile_id, template_id=None):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", profile_id):
        raise HTTPException(404, "채널을 찾을 수 없습니다.")
    profile(profile_id)
    root = template_store.channel_root(MAGAZINES_DIR.parent, profile_id) / 'items'
    if template_id is None:
        return root
    if not re.fullmatch(r"[0-9a-f]{32}", template_id):
        raise HTTPException(404, "템플릿을 찾을 수 없습니다.")
    root = root / template_id / 'draft'
    if not root.resolve().is_relative_to(template_store.channel_root(MAGAZINES_DIR.parent, profile_id).resolve()):
        raise HTTPException(400, '템플릿 저장소 밖의 경로입니다.')
    if not (root / "template.json").is_file():
        raise HTTPException(404, "템플릿을 찾을 수 없습니다.")
    return root


def detail(root):
    data = json.loads((root / "template.json").read_text(encoding="utf-8"))
    data["id"] = root.parent.name
    data["edits"] = json.loads((root / "edits.json").read_text(encoding="utf-8")) if (root / "edits.json").exists() else {}
    data['revision'] = data['edits'].get('revision', 0)
    return data


@router.get("/{profile_id}")
def templates(profile_id: str):
    with _lock:
        return [{"id": path.parent.parent.name, "name": json.loads(path.read_text(encoding="utf-8"))["name"]}
                for path in sorted(folder(profile_id).glob("*/draft/template.json"))]


@router.post("/{profile_id}")
async def upload(profile_id: str, file: UploadFile = File(...), role: Literal['cover', 'body', 'cta'] | None = Form(None)):
    root = folder(profile_id)
    if Path(file.filename or "").suffix.lower() != ".psd":
        raise HTTPException(400, "PSD 파일을 선택하세요.")
    key = uuid.uuid4().hex
    source = root / f"{key}.psd"
    try:
        await save_psd_upload(file, source)
        def convert():
            with _lock:
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                for manifest in root.glob('*/draft/template.json'):
                    existing = json.loads(manifest.read_text(encoding='utf-8'))
                    if existing.get('source_sha256') == digest and existing.get('role') == role:
                        return detail(manifest.parent)
                data = import_psd(source, root / key / 'draft', Path(file.filename).stem)
                original = template_store.preserve_original(MAGAZINES_DIR.parent, source, file.filename)
                data.update(schema_version=1, original_id=original, source_sha256=digest)
                if role:
                    data.update(role=role, name=f"{ {'cover': '표지', 'body': '본문', 'cta': 'CTA'}[role]} · {data['name']}")
                _atomic_json(root / key / 'draft' / 'template.json', data)
            return detail(root / key / 'draft')
        return await asyncio.to_thread(convert)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"PSD 변환 실패: {exc}") from exc
    finally:
        source.unlink(missing_ok=True)


@router.get("/{profile_id}/{template_id}")
def get_template(profile_id: str, template_id: str):
    with _lock:
        return detail(folder(profile_id, template_id))


@router.post("/{profile_id}/{template_id}/render")
def render_template(profile_id: str, template_id: str, edits: Edits):
    root = folder(profile_id, template_id)
    try:
        with _lock:
            revision = detail(root)['revision']
            if edits.revision is None:
                raise HTTPException(422, '저장할 초안 버전이 필요합니다. 다시 불러오세요.')
            if edits.revision != revision:
                raise HTTPException(409, '다른 창에서 저장했습니다. 다시 불러온 뒤 수정하세요.')
            options = options_for(root, edits)
            bounds = {}
            image = render(root, **options, bounds=bounds)
            staging = root / "preview.tmp.png"
            image.save(staging)
            staging.replace(root / "preview.png")
            _atomic_json(root / "edits.json", {**options, 'revision': revision + 1})
        return {"ok": True, "bounds": bounds, 'revision': revision + 1}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/{profile_id}/{template_id}/preview')
def preview(profile_id: str, template_id: str, edits: Edits):
    root = folder(profile_id, template_id)
    try:
        with _lock:
            bounds = {}
            result = render(root, **options_for(root, edits), bounds=bounds)
        stream = BytesIO()
        result.save(stream, 'PNG')
        return Response(stream.getvalue(), media_type='image/png', headers={'X-Layer-Bounds': json.dumps(bounds)})
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/{profile_id}/{template_id}/replace/{layer_id}')
async def replace_image(profile_id: str, template_id: str, layer_id: str, file: UploadFile = File(...)):
    root = folder(profile_id, template_id)
    layer = next((l for l in detail(root)['layers'] if l['id'] == layer_id and l['kind'] in {'pixel', 'smartobject'}), None)
    if layer is None:
        raise HTTPException(400, '사진 레이어를 선택하세요.')
    raw = await file.read(20 * 1024 * 1024 + 1)
    await file.close()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, '사진은 20MB 이내여야 합니다.')
    try:
        source = Image.open(BytesIO(raw))
        if source.width * source.height > 20_000_000:
            raise ValueError('사진은 2천만 픽셀 이내여야 합니다.')
        source = ImageOps.exif_transpose(source).convert('RGBA')
        name = f'layers/replacement-{uuid.uuid4().hex}.png'
        source.save(root / name)
        return {'asset': name}
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, f'사진을 열 수 없습니다: {exc}') from exc


@router.get("/{profile_id}/{template_id}/images/{name}")
def template_image(profile_id: str, template_id: str, name: str):
    root = folder(profile_id, template_id)
    if name not in {"reference.png", "preview.png", "difference.png"}:
        raise HTTPException(404, "이미지가 없습니다.")
    return FileResponse(root / name, headers={"Cache-Control": "no-store"})


@router.get('/{profile_id}/{template_id}/layer-image/{layer_id}')
def layer_image(profile_id: str, template_id: str, layer_id: str):
    from .template_runtime import asset_path
    root = folder(profile_id, template_id)
    layer = next((l for l in detail(root)['layers'] if l['id'] == layer_id), None)
    if layer is None:
        raise HTTPException(404, '레이어가 없습니다.')
    return FileResponse(asset_path(root, layer['asset']))


@router.get("/{profile_id}/{template_id}/download")
def download(profile_id: str, template_id: str):
    root = folder(profile_id, template_id)
    stream = BytesIO()
    with _lock, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file() and 'published' not in path.relative_to(root).parts and path.name not in {'render.py', 'template_primitives.py'}:
                archive.write(path, path.relative_to(root).as_posix())
        archive.write(Path(__file__).with_name('template_runtime.py'), 'render.py')
        archive.write(Path(__file__).with_name('template_primitives.py'), 'template_primitives.py')
        archive.writestr('requirements.txt', 'Pillow>=11.0.0\nnumpy>=1.26\n')
        archive.writestr('README.txt', 'Python 3.11+\npython -m pip install -r requirements.txt\n'
                         'python render.py --output result.png\npython render.py --changes edits.json --output edited.png\n')
    return Response(stream.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="python-template.zip"'})


@router.post('/{profile_id}/{template_id}/publish')
def publish(profile_id: str, template_id: str, payload: Publish):
    root = folder(profile_id, template_id)
    with _lock, CONFIG_LOCK:
        data = detail(root)
        if data['revision'] != payload.revision:
            raise HTTPException(409, '저장 버전이 변경됐습니다. 다시 불러오세요.')
        if data['size'] != [1080, 1440]:
            raise HTTPException(400, '제작용 템플릿은 1080×1440이어야 합니다.')
        owner = profile(profile_id)
        cfg = profile_config(owner)
        active = template_store.active_set(MAGAZINES_DIR.parent, profile_id, cfg.get('template', {}).get('approved_set'))
        if active.get(payload.role, {}).get('template_id', '') != payload.expected_active:
            raise HTTPException(409, '사용 중인 템플릿이 변경됐습니다. 목록을 새로 불러오세요.')
        layers = {l['id']: l for l in data['layers']}
        saved = data['edits']
        mapped = [payload.text_layer, payload.image_layer, payload.eyebrow_layer, payload.caption_layer, payload.badge_layer]
        if any(saved.get('changes', {}).get(key, {}).get('deleted') for key in mapped if key):
            raise HTTPException(400, '삭제된 레이어는 제작에 연결할 수 없습니다. 레이어 매핑을 다시 선택하세요.')
        if payload.role != 'cta':
            text_layer, photo = layers.get(payload.text_layer, {}), layers.get(payload.image_layer, {})
            if not text_layer.get('text_editable') or photo.get('kind') not in {'pixel', 'smartobject'}:
                raise HTTPException(400, '편집 가능한 글자와 사진 레이어를 지정하세요.')
            if any(not saved.get('changes', {}).get(k, {}).get('visible', layers[k]['visible'])
                   for k in (payload.text_layer, payload.image_layer)):
                raise HTTPException(400, '제작에 사용할 글자·사진 레이어는 표시 상태여야 합니다.')
        release = uuid.uuid4().hex
        target = root.parent / 'versions' / release
        target.mkdir(parents=True)
        try:
            for name in ('template.json', 'layers', 'fonts'):
                source = root / name
                if source.is_dir(): shutil.copytree(source, target / name, copy_function=template_store.link_or_copy)
                else: shutil.copy2(source, target / name)
            options = {k: v for k, v in saved.items() if k in {'changes', 'order'}}
            _atomic_json(target / 'edits.json', options)
            render(target).save(target / 'preview.png')
            spec = {'renderer': 'template-lab', 'bundle_path': str(target.resolve()),
                    'bundle_sha256': bundle_hash(target), 'template_id': release, 'module_role': payload.role,
                    'draft_id': template_id, 'name': data['name'], 'preserve_original': payload.role == 'cta'}
            if payload.role != 'cta':
                previous = active.get(payload.role, {})
                spec.update(publish_spec(payload, layers, data['size'], previous if previous.get('draft_id') == template_id else None))
                # A new version of the same cover keeps the AI badge-colour switch.
                if previous.get('ai_accent') and previous.get('accent_layer') in layers:
                    spec.update(ai_accent=True, accent_layer=previous['accent_layer'])
            _atomic_json(target.with_suffix('.json'), {'role': payload.role, 'spec': spec})
            template_store.activate(MAGAZINES_DIR.parent, profile_id, active, payload.role, spec)
        except Exception:
            shutil.rmtree(target)
            target.with_suffix('.json').unlink(missing_ok=True)
            raise
        return {'ok': True, 'active': spec}


class AiAccent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['cover'] = 'cover'
    enabled: bool
    expected_active: str = ''


@router.post('/{profile_id}/ai-accent')
def set_ai_accent(profile_id: str, payload: AiAccent):
    """Toggle per-article AI badge colour on the active cover. The version files stay untouched."""
    folder(profile_id)
    with _lock, CONFIG_LOCK:
        cfg = profile_config(profile(profile_id))
        active = template_store.active_set(MAGAZINES_DIR.parent, profile_id, cfg.get('template', {}).get('approved_set'))
        spec = dict(active.get(payload.role) or {})
        if spec.get('template_id', '') != payload.expected_active:
            raise HTTPException(409, '활성 버전이 바뀌었습니다. 새로 불러오세요.')
        if spec.get('renderer') != 'template-lab':
            raise HTTPException(400, '템플릿 제작에서 표지 버전을 먼저 적용하세요.')
        box = spec.get('accent_layer') or spec.get('text_options', {}).get('eyebrow', {}).get('align_to_layer')
        layers = {l['id']: l for l in json.loads((Path(spec['bundle_path']) / 'template.json').read_text(encoding='utf-8'))['layers']}
        if not box or not layers.get(box, {}).get('shape'):
            raise HTTPException(400, '색을 바꿀 배지 상자(사각형) 레이어가 없는 표지입니다.')
        spec.update(ai_accent=payload.enabled, accent_layer=box)
        template_store.activate(MAGAZINES_DIR.parent, profile_id, active, payload.role, spec)
        return {'ok': True, 'active': spec}


@router.post('/{profile_id}/activate/{release_id}')
def activate_version(profile_id: str, release_id: str, payload: Publish):
    if not re.fullmatch(r'[0-9a-f]{32}', release_id):
        raise HTTPException(404, '버전이 없습니다.')
    with _lock, CONFIG_LOCK:
        matches = list(folder(profile_id).glob(f'*/versions/{release_id}.json'))
        if len(matches) != 1:
            raise HTTPException(404, '버전이 없습니다.')
        saved = json.loads(matches[0].read_text(encoding='utf-8'))
        spec = saved['spec']
        if saved['role'] != payload.role:
            raise HTTPException(400, '역할이 다른 버전입니다.')
        bundle = Path(spec['bundle_path']).resolve()
        if not bundle.is_relative_to(folder(profile_id).resolve()) or not bundle.is_dir() or bundle_hash(bundle) != spec['bundle_sha256']:
            raise HTTPException(400, '보관된 버전 파일이 변경됐습니다.')
        owner = profile(profile_id)
        cfg = profile_config(owner)
        active = template_store.active_set(MAGAZINES_DIR.parent, profile_id, cfg.get('template', {}).get('approved_set'))
        if active.get(payload.role, {}).get('template_id', '') != payload.expected_active:
            raise HTTPException(409, '활성 버전이 바뀌었습니다. 새로 불러오세요.')
        template_store.activate(MAGAZINES_DIR.parent, profile_id, active, payload.role, spec)
        return {'ok': True}
