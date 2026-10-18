import json
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageChops
from psd_tools import PSDImage
from psd_tools.constants import BlendMode
from psd_tools.api.layers import PixelLayer, Group

from cardnews_auto import template_lab_api as api
from cardnews_auto.template_lab import import_psd
from cardnews_auto.template_runtime import asset_path, render


@pytest.fixture
def source(tmp_path):
    psd = PSDImage.new("RGB", (12, 12))
    PixelLayer.frompil(Image.new("RGBA", (12, 12), "red"), psd, "same name")
    PixelLayer.frompil(Image.new("RGBA", (4, 4), "blue"), psd, "same name", top=2, left=3)
    path = tmp_path / "sample.psd"
    psd.save(path)
    return path


def test_roundtrip_order_visibility_and_portable_code(source, tmp_path):
    root = tmp_path / "bundle"
    data = import_psd(source, root)
    assert data["stack_order"] == "bottom-to-top"
    assert len({l["id"] for l in data["layers"]}) == 2
    assert render(root).getpixel((3, 2)) == (0, 0, 255, 255)
    assert render(root, order=["layer-001", "layer-000"]).getpixel((3, 2)) == (255, 0, 0, 255)
    assert render(root, {"layer-001": {"visible": False}}).getpixel((3, 2)) == (255, 0, 0, 255)
    assert render(root, {"layer-001": {"x": 7, "y": 7}}).getpixel((7, 7)) == (0, 0, 255, 255)
    (root / "edits.json").write_text(json.dumps({"changes": {"layer-001": {"visible": False}}}))
    output = tmp_path / "result.png"
    source.unlink()
    # The download bundles the runtime next to the template; do the same here.
    runtime = Path(api.__file__).parent
    shutil.copy2(runtime / "template_runtime.py", root / "render.py")
    shutil.copy2(runtime / "template_primitives.py", root / "template_primitives.py")
    subprocess.run([sys.executable, str(root / "render.py"), "--output", str(output)], check=True)
    assert Image.open(output).getpixel((3, 2)) == (255, 0, 0, 255)


def test_runtime_rejects_bad_order_and_path(source, tmp_path):
    root = tmp_path / "bundle"
    import_psd(source, root)
    for order in [[], ["layer-000", "layer-000"], ["unknown"]]:
        with pytest.raises(ValueError):
            render(root, order=order)
    with pytest.raises(ValueError):
        render(root, {"unknown": {}})
    with pytest.raises(ValueError):
        render(root, {"layer-001": {"opacity": 2}})
    with pytest.raises(ValueError):
        asset_path(root, "../sample.psd")


def test_import_does_not_overwrite(source, tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        import_psd(source, root)
    assert marker.read_text() == "keep"


def test_api_upload_save_reopen_export_and_validation(source, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "MAGAZINES_DIR", tmp_path / "magazines")
    def profile(key):
        if key != "football":
            raise api.HTTPException(404)
    monkeypatch.setattr(api, "profile", profile)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    route = "/api/template-lab/football"
    response = client.post(route, files={"file": ("cover.psd", source.read_bytes())})
    assert response.status_code == 200, response.text
    data = response.json()
    item = f"{route}/{data['id']}"
    assert client.get(route).json() == [{"id": data["id"], "name": "cover"}]
    assert client.post(item + "/render", json={"changes": {"layer-001": {"visible": False}}, "revision": 0}).status_code == 200
    assert client.get(item).json()["edits"]["changes"]["layer-001"]["visible"] is False
    assert client.post(item + "/render", json={"order": ["bad"], "revision": 1}).status_code == 400
    assert client.post(item + "/render", json={"changes": {"layer-001": {"opacity": 2}}}).status_code == 422
    assert client.get(item + "/images/reference.png").status_code == 200
    assert client.get(item + "/images/template.json").status_code == 404
    archive = zipfile.ZipFile(BytesIO(client.get(item + "/download").content))
    assert {"render.py", "template.json", "edits.json"} <= set(archive.namelist())
    assert not any(n.endswith(".psd") for n in archive.namelist())
    assert client.get("/api/template-lab/other").status_code == 404
    assert client.post(route, files={"file": ("bad.psd", b"not a psd")}).status_code == 400
    assert client.post(route, files={"file": ("bad.txt", b"not a psd")}).status_code == 400

    saved = client.get(item).json()['edits']
    preview = client.post(item + '/preview', json={'changes': {'layer-001': {'x': 7, 'y': 7, 'width': 2, 'height': 2}}})
    assert preview.status_code == 200
    assert Image.open(BytesIO(preview.content)).getpixel((7, 7)) == (0, 0, 255, 255)
    assert json.loads(preview.headers['X-Layer-Bounds'])['layer-001'] == [7, 7, 2, 2]
    assert client.get(item).json()['edits'] == saved
    assert client.post(item + '/preview', json={'changes': {'layer-001': {'font_size': 40}}}).status_code == 400
    photo = BytesIO()
    Image.new('RGB', (6, 4), 'lime').save(photo, 'PNG')
    replacement = client.post(item + '/replace/layer-001', files={'file': ('photo.png', photo.getvalue())})
    assert replacement.status_code == 200
    preview = client.post(item + '/preview', json={'changes': {'layer-001': replacement.json()}})
    assert Image.open(BytesIO(preview.content)).getpixel((3, 2)) == (0, 255, 0, 255)
    assert client.post(item + '/replace/layer-001', files={'file': ('bad.png', b'bad')}).status_code == 400
    assert 'template_primitives.py' in archive.namelist()


def test_editable_primitives_and_gradient_validation(tmp_path):
    from cardnews_auto.template_primitives import shape_tile, gradient_tile, layout_text
    from pydantic import ValidationError
    settings = dict(angle=0, scale=100, offset_x=0, offset_y=0,
                    colors=[dict(position=0, color='#000000'), dict(position=1, color='#ffffff')],
                    alphas=[dict(position=0, opacity=1), dict(position=1, opacity=0)])
    gradient = gradient_tile((100, 20), settings)
    assert gradient.getpixel((0, 10)) == (0, 0, 0, 255)
    assert gradient.getpixel((99, 10))[0] > 250
    assert gradient.getpixel((99, 10))[3] < 5
    reversed_tile = gradient_tile((100, 20), {**settings, 'reverse': True})
    assert reversed_tile.getpixel((0, 10)) == (255, 255, 255, 0)
    api.Gradient(**settings)
    with pytest.raises(ValidationError):
        api.Gradient(**{**settings, 'colors': list(reversed(settings['colors']))})
    shape = shape_tile((40, 20), dict(fill='#00ff00', stroke='#ff0000', stroke_width=3, fill_enabled=True, stroke_enabled=True))
    assert shape.getpixel((20, 10)) == (0, 255, 0, 255)
    assert shape.getpixel((1, 10))[0] > 240
    with pytest.raises(ValueError):
        shape_tile((4096, 4096), {})
    font = next(iter(api.fonts().values()), None)
    if font is not None:
        shutil.copy2(font, tmp_path / ('font' + font.suffix))
        layer = dict(font_size=24, leading=30, text='Test', text_color='#ff0000', font_file='font' + font.suffix,
                     x=0, y=0, width=10, height=10)
        small = layout_text(tmp_path, layer, {})[0]
        large = layout_text(tmp_path, layer, {'font_size': 48, 'text': 'Test Test Test', 'text_color': '#0000ff'})[0]
        assert large.width > small.width * 2 and large.height > small.height
        assert large.getchannel('R').getextrema() == (0, 0)


def test_line_ending_change_keeps_original_pixels(tmp_path):
    root = tmp_path
    Image.new("RGBA", (4, 4), "red").save(root / "tile.png")
    (root / "template.json").write_text(json.dumps({"size": [4, 4], "layers": [{
        "id": "a", "visible": True, "x": 0, "y": 0, "asset": "tile.png", "text": "A\rB"
    }]}))
    assert ImageChops.difference(render(root).convert("RGB"), render(root, {"a": {"text": "A\nB"}}).convert("RGB")).getbbox() is None


def test_passthrough_groups_preserve_order_and_inherited_visibility(tmp_path):
    psd = PSDImage.new("RGB", (12, 12))
    PixelLayer.frompil(Image.new("RGBA", (12, 12), "red"), psd, "base")
    group = Group.new(psd, "group")
    group.blend_mode = BlendMode.PASS_THROUGH
    PixelLayer.frompil(Image.new("RGBA", (4, 4), "blue"), group, "child", top=2, left=3)
    path = tmp_path / "group.psd"
    psd.save(path)
    data = import_psd(path, tmp_path / "visible")
    assert [l["name"] for l in data["layers"]] == ["base", "child"]
    assert data["layers"][1]["group_path"] == ["group"]
    assert render(tmp_path / "visible").getpixel((3, 2)) == (0, 0, 255, 255)
    group.visible = False
    psd.save(path)
    data = import_psd(path, tmp_path / "hidden")
    assert data["layers"][1]["visible"] is False
    assert render(tmp_path / "hidden").getpixel((3, 2)) == (255, 0, 0, 255)
    group.opacity = 128
    psd.save(path)
    with pytest.raises(ValueError, match="그룹"):
        import_psd(path, tmp_path / "unsupported")
