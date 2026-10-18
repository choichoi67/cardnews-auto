from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / 'src/cardnews_auto/web/static'


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
    original = Elements((STATIC / 'template-studio.html').read_text(encoding='utf-8'))
    shell = Elements((STATIC / 'template-lab-photocraft.html').read_text(encoding='utf-8'))
    required = {name for name in original.ids if name.startswith('lab-') or name == 'studio-slots'}
    assert required <= set(shell.ids)
    assert len(shell.ids) == len(set(shell.ids))
    assert set(shell.targets) <= set(shell.dialogs)
    assert shell.scripts == ['/studio-common.js', '/template-studio.js', '/template-lab-photocraft.js']
    for script in shell.scripts:
        assert (STATIC / script.lstrip('/')).is_file()


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
