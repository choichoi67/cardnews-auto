import json

import pytest
from PIL import Image
from pydantic import ValidationError

from cardnews_auto.template_lab_api import LayerEdit
from cardnews_auto.template_runtime import render


@pytest.mark.parametrize('mode, expected', [
    ('NORMAL', 192), ('MULTIPLY', 96), ('SCREEN', 224),
    ('OVERLAY', 192), ('DARKEN', 128), ('LIGHTEN', 192), ('SOFT_LIGHT', 155),
])
def test_blend_modes_render_and_reopen_saved_edits(tmp_path, mode, expected):
    layers = []
    for name, color in [('back', 128), ('front', 192)]:
        Image.new('RGBA', (2, 2), (color, color, color, 255)).save(tmp_path / f'{name}.png')
        layers.append(dict(id=name, name=name, kind='pixel', visible=True,
                           x=0, y=0, width=2, height=2, asset=f'{name}.png'))
    (tmp_path / 'template.json').write_text(json.dumps({'size': [2, 2], 'layers': layers}))
    change = LayerEdit(blend_mode=mode).model_dump(exclude_none=True)
    result = render(tmp_path, {'front': change})
    assert abs(result.getpixel((0, 0))[0] - expected) <= 1
    (tmp_path / 'edits.json').write_text(json.dumps({'changes': {'front': change}}))
    assert render(tmp_path).tobytes() == result.tobytes()
    transparent = render(tmp_path, {'back': {'visible': False}, 'front': change})
    assert transparent.getpixel((0, 0)) == (192, 192, 192, 255)


def test_blend_mode_rejects_unknown_values():
    with pytest.raises(ValidationError):
        LayerEdit(blend_mode='unsupported')
