"""Originals, editable drafts and immutable channel versions share one store."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .workflow import _write_state


def link_or_copy(source, target):
    """Store identical files once: hard link (same volume), else copy. Usable as copytree's copy_function."""
    # ponytail: a file overwritten in place also changes its links; every consumer re-checks sha256 before use.
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def channel_root(project, channel):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', channel):
        raise ValueError('채널 ID가 올바르지 않습니다.')
    base = Path(project).resolve() / 'templates' / 'channels'
    root = base / channel
    if not root.resolve().is_relative_to(base.resolve()):
        raise ValueError('템플릿 저장소 밖의 경로입니다.')
    return root


def active_set(project, channel, legacy=None):
    path = channel_root(project, channel) / 'active.json'
    if not path.exists():
        return deepcopy(legacy or {})
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('profile_id') != channel or data.get('schema_version') != 1:
        raise ValueError('템플릿 활성 목록의 채널 또는 형식이 올바르지 않습니다.')
    return data


def resolve_config(cfg, project):
    result = deepcopy(cfg)
    channel = result.get('magazine', {}).get('id')
    if channel:
        template = result.setdefault('template', {})
        try:
            active = active_set(project, channel, template.get('approved_set'))
        except (ValueError, OSError):
            # A broken template index must block production, not hide newsroom data.
            template['_store_error'] = '활성 템플릿 목록을 읽을 수 없습니다. 템플릿 저장소를 확인하세요.'
            return result
        if active:
            template['approved_set'] = active
        template['_version_snapshot'] = True
    return result


def activate(project, channel, active, role, spec):
    value = {**active, 'schema_version': 1, 'profile_id': channel, role: spec}
    _write_state(channel_root(project, channel) / 'active.json', value)


def preserve_original(project, source, filename):
    source = Path(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    root = Path(project) / 'templates' / 'originals'
    target = root / digest
    if target.exists():
        if hashlib.sha256((target / 'source.psd').read_bytes()).hexdigest() != digest:
            raise ValueError('보관된 원본이 변경되었습니다.')
        return digest
    staging = root / ('.incoming-' + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    try:
        # A real copy: the original must survive the source path being overwritten later.
        shutil.copy2(source, staging / 'source.psd')
        _write_state(staging / 'source.json', {'schema_version': 1, 'original_id': digest,
                     'filename': Path(filename).name, 'sha256': digest,
                     'created_at': datetime.now(timezone.utc).isoformat()})
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return digest
