import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from cardnews_auto import template_lab_api as api, production_policy
from cardnews_auto.models import Slide
from cardnews_auto.template_publication import bundle_hash, render_slide
from cardnews_auto.template_runtime import render
from cardnews_auto import template_store


@pytest.fixture
def studio(tmp_path, monkeypatch):
    owner = {'id': 'a', 'name': 'A', 'config_path': str(tmp_path / 'config.yaml')}
    cfg = {'magazine': {'id': 'a'}, 'template': {}, 'keep': {'value': 7}}
    Path(owner['config_path']).write_text(yaml.safe_dump(cfg), encoding='utf-8')
    monkeypatch.setattr(api, 'MAGAZINES_DIR', tmp_path / 'magazines')
    monkeypatch.setattr(production_policy, 'POLICY_PATH', tmp_path / 'image-policy.yaml')
    def profile(key):
        if key != 'a': raise HTTPException(404)
        return owner
    monkeypatch.setattr(api, 'profile', profile)
    monkeypatch.setattr(api, 'registry', lambda: [owner])
    monkeypatch.setattr(api, 'profile_config', lambda _: yaml.safe_load(Path(owner['config_path']).read_text(encoding='utf-8')))
    root = tmp_path / 'templates' / 'channels' / 'a' / 'items' / ('a'*32) / 'draft'
    (root / 'layers').mkdir(parents=True)
    (root / 'fonts').mkdir()
    font = next(iter(api.fonts().values()), None)
    if font is None: pytest.skip('No installed TTF/OTF')
    shutil.copy2(font, root / 'fonts' / ('font'+font.suffix))
    Image.new('RGBA', (1080,1440), 'red').save(root/'layers/photo.png')
    Image.new('RGBA', (100,30), 'white').save(root/'layers/text.png')
    data = {'version':1, 'name':'sample', 'size':[1080,1440], 'warnings':[], 'comparison':{'rgb_mae_0_255':0}, 'layers':[
        {'id':'photo','name':'Photo','kind':'pixel','visible':True,'x':0,'y':0,'width':1080,'height':1440,'asset':'layers/photo.png'},
        {'id':'text','name':'Text','kind':'type','visible':True,'x':20,'y':20,'width':100,'height':30,'asset':'layers/text.png',
         'font_file':'fonts/font'+font.suffix,'text_editable':True,'text':'old','font_size':24,'leading':30}]}
    (root/'template.json').write_text(json.dumps(data),encoding='utf-8')
    app=FastAPI();app.include_router(api.router)
    return TestClient(app), root, owner


def test_publish_versions_conflicts_restore_and_channel_isolation(studio):
    client, root, owner=studio
    route=f'/api/template-lab/a/{root.parent.name}'
    r=client.post(route+'/render',json={'changes':{},'revision':0})
    assert r.status_code==200, r.text
    assert client.post(route+'/render',json={'changes':{},'revision':0}).status_code==409
    payload={'role':'cover','revision':1,'text_layer':'text','image_layer':'photo'}
    r=client.post(route+'/publish',json=payload)
    assert r.status_code==200, r.text
    first=r.json()['active'];frozen=Path(first['bundle_path'])
    assert client.post(route+'/publish',json=payload).status_code==409
    assert client.post(route+'/publish',json={**payload,'role':'body'}).status_code==200
    assert client.post(route+'/publish',json={'role':'cta','revision':1}).status_code==200
    cfg=yaml.safe_load(Path(owner['config_path']).read_text())
    resolved=production_policy.approved_template(cfg)
    assert resolved['append_cta'] and len(resolved['template_variants']['cta'])==1
    assert cfg['keep']=={'value':7}
    original=bundle_hash(frozen)
    assert client.post(route+'/render',json={'changes':{'photo':{'visible':False}},'revision':1}).status_code==200
    assert bundle_hash(frozen)==original
    assert client.post(route+'/publish',json={**payload,'revision':2,'expected_active':first['template_id']}).status_code==400
    r=client.post(route+'/render',json={'changes':{},'revision':2});assert r.status_code==200
    newer=client.post(route+'/publish',json={**payload,'revision':3,'expected_active':first['template_id']}).json()['active']
    restored=client.post('/api/template-lab/a/activate/'+first['template_id'],json={'role':'cover','revision':0,'expected_active':newer['template_id']})
    assert restored.status_code==200, restored.text
    assert len(client.get('/api/template-lab/a/catalog').json()['history'])==4
    assert client.get(f'/api/template-lab/b/{root.parent.name}').status_code==404
    assert client.post('/api/template-lab/a/activate/'+first['template_id'],json={'role':'body','revision':0}).status_code==400
    (frozen/'layers/photo.png').write_bytes(b'changed')
    cfg=yaml.safe_load(Path(owner['config_path']).read_text())
    with pytest.raises(RuntimeError,match='변경'):
        production_policy.approved_template(cfg)


def test_production_render_uses_saved_edits_and_keeps_cta_fixed(studio,tmp_path):
    client,root,_=studio
    route=f'/api/template-lab/a/{root.parent.name}'
    client.post(route+'/render',json={'changes':{'text':{'x':40}},'revision':0})
    spec=client.post(route+'/publish',json={'role':'cover','revision':1,'text_layer':'text','image_layer':'photo'}).json()['active']
    photo=tmp_path/'new.png';Image.new('RGB',(100,100),'blue').save(photo)
    output=tmp_path/'out.jpg'
    render_slide(spec['bundle_path'],spec,Slide(role='cover',title='New',body=''),output,photo)
    assert Image.open(output).getpixel((500,500))[2]>240
    cta=client.post(route+'/publish',json={'role':'cta','revision':1}).json()['active']
    render_slide(cta['bundle_path'],cta,Slide(role='cta',title='Ignored',body=''),output,photo)
    assert Image.open(output).getpixel((500,500))[0]>240


def test_gradient_preview_save_reopen_and_publish(studio):
    from io import BytesIO

    client, root, _ = studio
    route = f'/api/template-lab/a/{root.parent.name}'
    settings = dict(angle=0, scale=100, offset_x=0, offset_y=0, reverse=False,
                    colors=[dict(position=0, color='#000000'), dict(position=1, color='#ffffff')],
                    alphas=[dict(position=0, opacity=1), dict(position=1, opacity=1)])
    manifest = json.loads((root / 'template.json').read_text(encoding='utf-8'))
    manifest['layers'][0].update(kind='gradientfill', gradient=settings)
    (root / 'template.json').write_text(json.dumps(manifest), encoding='utf-8')
    changes = {'photo': {'gradient': {**settings, 'reverse': True}}}
    response = client.post(route + '/preview', json={'changes': changes})
    assert response.status_code == 200, response.text
    preview = Image.open(BytesIO(response.content))
    assert preview.getpixel((0, 700))[0] == 255
    assert preview.getpixel((1079, 700))[0] < 2
    assert client.post(route + '/render', json={'changes': changes, 'revision': 0}).status_code == 200
    assert client.get(route).json()['edits']['changes']['photo']['gradient']['reverse'] is True
    response = client.post(route + '/publish', json={'role': 'cta', 'revision': 1})
    assert response.status_code == 200, response.text
    published = render(Path(response.json()['active']['bundle_path']))
    assert published.getpixel((0, 700)) == preview.getpixel((0, 700))
    assert client.post(route + '/preview', json={'changes': {'photo': {'gradient': {**settings, 'scale': 0}}}}).status_code == 422


def test_regenerated_shape_retains_source_opacity(tmp_path):
    Image.new('RGBA',(10,10),(0,255,0,51)).save(tmp_path/'tile.png')
    shape={'fill':'#00ff00','stroke':'#000000','stroke_width':0,'fill_enabled':True,'stroke_enabled':False}
    data={'size':[10,10],'layers':[{'id':'a','name':'shape','kind':'shape','visible':True,'x':0,'y':0,'width':10,'height':10,'source_opacity':51,'shape':shape,'asset':'tile.png'}]}
    (tmp_path/'template.json').write_text(json.dumps(data))
    assert render(tmp_path,{'a':{'shape':{**shape,'fill':'#ff0000'}}}).getpixel((5,5))[3]==51


def test_failed_activation_preserves_config_and_active_version(studio, monkeypatch):
    client, root, owner = studio
    route = f'/api/template-lab/a/{root.parent.name}'
    original = Path(owner['config_path']).read_bytes()
    payload = {'role': 'cta', 'revision': 0}
    first = client.post(route+'/publish', json=payload).json()['active']
    active_path = root.parents[2] / 'active.json'
    before = active_path.read_bytes()
    def fail(*args):
        raise OSError('simulated activation failure')
    monkeypatch.setattr(template_store, '_write_state', fail)
    with pytest.raises(OSError):
        client.post(route+'/publish', json={**payload, 'expected_active': first['template_id']})
    assert active_path.read_bytes() == before
    assert Path(owner['config_path']).read_bytes() == original
    assert len(list((root.parent/'versions').glob('*.json'))) == 1
    assert client.post(route+'/render', json={'changes': {}}).status_code == 422


def test_originals_deduplicate_content_without_overwriting_names(tmp_path):
    source = tmp_path/'same.psd'
    source.write_bytes(b'original-one')
    first = template_store.preserve_original(tmp_path, source, source.name)
    assert template_store.preserve_original(tmp_path, source, 'renamed.psd') == first
    source.write_bytes(b'original-two')
    second = template_store.preserve_original(tmp_path, source, source.name)
    assert first != second
    assert (tmp_path/'templates/originals'/first/'source.psd').read_bytes() == b'original-one'


def test_frozen_job_keeps_version_after_active_changes(studio):
    client, root, owner = studio
    route = f'/api/template-lab/a/{root.parent.name}'
    for role in ('cover','body'):
        assert client.post(route+'/publish', json={'role': role,'revision':0,'text_layer':'text','image_layer':'photo'}).status_code == 200
    cfg = yaml.safe_load(Path(owner['config_path']).read_text())
    project = Path(owner['config_path']).parent
    frozen = template_store.resolve_config(cfg, project)
    old = frozen['template']['approved_set']['cover']['template_id']
    client.post(route+'/publish', json={'role':'cover','revision':0,'text_layer':'text','image_layer':'photo','expected_active':old})
    assert production_policy.approved_template(frozen)['cover']['template_id'] == old
    assert production_policy.approved_template(cfg)['cover']['template_id'] != old


def test_broken_template_index_does_not_hide_channel_data(studio):
    _, root, owner = studio
    (root.parents[2]/'active.json').write_text('{broken')
    cfg = yaml.safe_load(Path(owner['config_path']).read_text())
    result = template_store.resolve_config(cfg, Path(owner['config_path']).parent)
    assert result['magazine']['id'] == 'a' and result['keep']['value'] == 7
    assert result['template']['_store_error']
    with pytest.raises(RuntimeError, match='활성 템플릿'):
        production_policy.approved_template(result)
