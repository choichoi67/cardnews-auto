from copy import deepcopy

from cardnews_auto.template_lab_api import Publish, publish_spec


def test_reapply_preserves_title_badge_caption_bounds_and_advanced_options():
    previous = {
        'layers': {'eyebrow': 'badge-text', 'title': 'heading', 'body': 'caption'},
        'text_options': {
            'title': {'max_chars': 24, 'line_chars': 12, 'max_lines': 2,
                      'allowed_bounds': [70, 930, 1010, 1220], 'align': 'center'},
            'eyebrow': {'align_to_layer': 'badge', 'auto_width': True, 'padding_x': 27},
            'body': {'max_chars': 10, 'line_chars': 10, 'max_lines': 1,
                     'allowed_bounds': [105, 1260, 1020, 1335]},
        },
        'image_layer': 'photo', 'image_fit': 'exact',
        'image_layers': ['photo', 'extra-photo'], 'hide_layers': ['hidden'],
    }
    before = deepcopy(previous)
    layers = {key: {'text_editable': True, 'y': 20, 'font_size': 24}
              for key in ('badge-text', 'heading', 'caption')}
    layers.update({key: {} for key in ('badge', 'photo', 'extra-photo', 'hidden')})
    payload = Publish(role='cover', revision=1, text_layer='heading',
                      image_layer='photo', eyebrow_layer='badge-text', badge_layer='badge',
                      caption_layer='caption', max_chars=24, line_chars=12, max_lines=2)
    assert publish_spec(payload, layers, [1080, 1440], previous) == previous
    assert previous == before
    removed = payload.model_copy(update={'eyebrow_layer': '', 'badge_layer': '', 'caption_layer': ''})
    result = publish_spec(removed, layers, [1080, 1440], previous)
    assert result['layers'] == {'title': 'heading'}
    assert result['text_options']['title'] == previous['text_options']['title']
