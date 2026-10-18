"""Copy legacy drafts into the new store without deleting or switching designs."""
import json
from pathlib import Path
import shutil

import yaml

from .template_store import channel_root, link_or_copy
from .workflow import _write_state


def migrate(project):
    """Run once per install. Legacy PSD originals are preserved on demand by import-existing."""
    project = Path(project).resolve()
    registry = json.loads((project/'magazines/registry.json').read_text(encoding='utf-8'))
    copied = []
    for item in registry:
        channel = item['id']
        destination = channel_root(project, channel)
        source_root = project/'magazines'/channel
        cfg = yaml.safe_load(Path(item['config_path']).read_text(encoding='utf-8'))
        active = cfg.get('template', {}).get('approved_set')
        if active and not (destination/'active.json').exists():
            _write_state(destination/'active.json', {**active,'schema_version':1})
        for manifest in (source_root/'template-lab').glob('*/template.json'):
            draft = destination/'items'/manifest.parent.name/'draft'
            if draft.exists():
                continue
            if list((manifest.parent/'published').glob('*.json')):
                raise ValueError('기존 편집기 확정 버전이 있어 별도 이관 검토가 필요합니다.')
            staging = draft.parent/'incoming'
            if staging.exists():
                raise ValueError('중단된 이관 폴더를 먼저 확인해야 합니다.')
            # Drafts save through new files (atomic replace), so linking keeps one copy on disk.
            shutil.copytree(manifest.parent, staging, copy_function=link_or_copy)
            staging.rename(draft)
            copied.append(str(draft.relative_to(project)))
    return copied
