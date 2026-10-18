"""Shared image policy and the channel's active template-studio versions."""
from copy import deepcopy
from pathlib import Path

import yaml

from .template_publication import bundle_hash
from .template_store import channel_root, resolve_config

POLICY_PATH = Path(__file__).resolve().parents[2] / "image-policy.yaml"


def apply_image_policy(cfg: dict) -> dict:
    result = deepcopy(cfg)
    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    for key in ("image_sources", "image_transform"):
        result[key] = deepcopy(policy[key])
    return result


def approved_template(cfg: dict) -> dict:
    """Resolve the active cover/body (and optional CTA) versions before writing, not after."""
    if not cfg.get('template', {}).get('_version_snapshot'):
        cfg = resolve_config(cfg, POLICY_PATH.parent)
    template = deepcopy(cfg.get("template", {}))
    if template.get('_store_error'):
        raise RuntimeError(template['_store_error'])
    active = template.get("approved_set")
    if not active:
        raise RuntimeError("이 채널의 승인된 템플릿이 없습니다. 템플릿 제작에서 표지·본문 버전을 적용하세요.")
    if active.get("profile_id") != cfg.get("magazine", {}).get("id"):
        raise RuntimeError("승인된 템플릿의 채널이 현재 채널과 다릅니다.")
    owner_root = channel_root(POLICY_PATH.parent, active['profile_id']) / 'items'
    for role in ('cover', 'body', *(['cta'] if active.get('cta') else [])):
        spec = deepcopy(active.get(role) or {})
        if spec.get('renderer') != 'template-lab':
            raise RuntimeError(f'{role} 템플릿이 템플릿 제작 버전이 아닙니다. 템플릿 제작에서 적용하세요.')
        path = Path(spec['bundle_path'])
        if not path.resolve().is_relative_to(owner_root.resolve()) or not path.is_dir() or bundle_hash(path) != spec.get('bundle_sha256'):
            raise RuntimeError(f'{role} 템플릿 버전이 변경되거나 사라졌습니다. 템플릿 제작에서 다시 적용하세요.')
        template[role] = spec
    template["template_variants"] = {role: [template[role]] for role in ("cover", "body")}
    template['template_variants']['cta'] = [template['cta']] if active.get('cta') else []
    template['append_cta'] = bool(active.get('cta'))
    return template
