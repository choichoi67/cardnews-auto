from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / 'src/cardnews_auto/web/static'


def test_legacy_editor_redirects_to_single_workspace():
    source = (STATIC / 'template-studio.html').read_text(encoding='utf-8')
    assert 'location.replace("/template-lab-photocraft.html"' in source
    assert 'id="lab-editor"' not in source
    dashboard = (STATIC / 'index.html').read_text(encoding='utf-8')
    assert 'href="/template-studio.html"' not in dashboard
    assert 'href="/template-lab-photocraft.html"' in dashboard


class Elements(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids = []
        self.dialogs = []
        self.targets = []
        self.scripts = []
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if 'id' in attributes:
            self.ids.append(attributes['id'])
        if tag == 'dialog':
            self.dialogs.append(attributes['id'])
        if 'data-dialog' in attributes:
            self.targets.append(attributes['data-dialog'])
        if tag == 'script' and 'src' in attributes:
            self.scripts.append(attributes['src'].split('?')[0])


def test_photocraft_shell_preserves_editor_bindings():
    shell = Elements((STATIC / 'template-lab-photocraft.html').read_text(encoding='utf-8'))
    required = {'lab-channel', 'lab-editor', 'lab-library', 'lab-upload', 'lab-file',
                'lab-role', 'lab-publish', 'lab-render', 'lab-properties', 'lab-layers',
                'studio-slots', 'lab-stage', 'lab-status', 'lab-undo', 'lab-redo'}
    assert required <= set(shell.ids)
    assert len(shell.ids) == len(set(shell.ids))
    assert set(shell.targets) <= set(shell.dialogs)
    assert shell.scripts == ['/studio-common.js', '/template-studio.js', '/template-lab-photocraft.js']
    for script in shell.scripts:
        assert (STATIC / script.lstrip('/')).is_file()
    source = (STATIC / 'template-lab-photocraft.html').read_text(encoding='utf-8')
    assert source.index('id="studio-slots"') < source.index('<dialog')
    assert 'id="lab-legacy"' not in source
    assert '/template-studio.html' not in source


def test_shell_is_served_without_replacing_existing_editor():
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.mount('/', StaticFiles(directory=STATIC, html=True))
    client = TestClient(app)
    for filename in ('template-studio.html', 'template-lab-photocraft.html',
                     'template-lab-photocraft.css', 'template-lab-photocraft.js'):
        response = client.get('/' + filename)
        assert response.status_code == 200
        assert response.content == (STATIC / filename).read_bytes()
