import json
from pathlib import Path
import yaml
from cardnews_auto.template_migration import migrate


def test_migration_preserves_existing_data_and_is_repeatable(tmp_path):
    root=tmp_path/'magazines/a';root.mkdir(parents=True)
    cfg=root/'config.yaml'
    active={'profile_id':'a','cover':{'template_id':'old','psd_path':'keep.psd'}}
    cfg.write_text(yaml.safe_dump({'template':{'approved_set':active},'other':42}))
    (tmp_path/'magazines/registry.json').write_text(json.dumps([{'id':'a','config_path':str(cfg)}]))
    draft=root/'template-lab'/('a'*32);draft.mkdir(parents=True)
    (draft/'template.json').write_text('{"name":"old draft"}')
    (draft/'edits.json').write_text('{"revision":7}')
    before=cfg.read_bytes()
    assert len(migrate(tmp_path))==1
    new=tmp_path/'templates/channels/a/items'/('a'*32)/'draft'
    assert (new/'edits.json').read_bytes()==(draft/'edits.json').read_bytes()
    assert migrate(tmp_path)==[]
    assert cfg.read_bytes()==before
    assert json.loads((tmp_path/'templates/channels/a/active.json').read_text())['cover']==active['cover']
