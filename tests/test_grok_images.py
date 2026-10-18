import base64
from io import BytesIO
from types import SimpleNamespace as NS

import pytest
from PIL import Image
from cardnews_auto import render
from cardnews_auto.models import CardNews, Slide
from cardnews_auto.usage_meter import price


def test_grok_edit_estimate_includes_reference_images():
    assert price(NS(data=[{}], input_image_count=2), 'xai', 'grok-imagine-image-2.0')[:2] == (.1, 'estimated')


@pytest.mark.parametrize('source_count', [0, 1])
def test_grok_json_routes_dimensions_cache_and_usage(monkeypatch, tmp_path, capsys, source_count):
    monkeypatch.setenv('XAI_API_KEY', 'test-key')
    calls = []
    buffer = BytesIO()
    Image.new('RGB', (1776, 2368), 'green').save(buffer, 'PNG')
    data = {'data': [{'b64_json': base64.b64encode(buffer.getvalue()).decode()}],
            'usage': {'cost_in_usd_ticks': 800000000}}
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return NS(status_code=200, json=lambda: data)
    monkeypatch.setattr(render.requests, 'post', post)
    monkeypatch.setattr(render, 'OpenAI', lambda **_: pytest.fail('Grok must not use OpenAI endpoint'))
    options = {'model': 'grok-imagine-image-2.0', 'enabled': True}
    if source_count:
        path = tmp_path / 'source.jpg'
        Image.new('RGB', (300, 200), 'blue').save(path)
        run = lambda: render._transform_cover_image(path, options, tmp_path/'cache')
    else:
        card = CardNews(topic='music', rationale='', source_urls=[], caption='', hashtags=[],
                        slides=[Slide(role='cover', title='Music', body='')])
        run = lambda: render._generate_news_illustration(card, card.slides[0], 0, options, tmp_path/'cache')
    result = run()
    assert Image.open(result).size == (1776, 2368)
    assert run() == result and len(calls) == 1
    url, request = calls[0]
    assert url == 'https://api.x.ai/v1/images/' + ('edits' if source_count else 'generations')
    payload = request['json']
    if source_count:
        assert render.WATERMARK_CLEANUP_PROMPT in payload['prompt']
        assert 'Billboard/billboard' in payload['prompt']
    assert payload['aspect_ratio'] == '3:4' and payload['resolution'] == '2k'
    assert payload['quality'] == 'medium' and payload['response_format'] == 'b64_json'
    assert 'size' not in payload and 'files' not in request
    if source_count:
        assert payload['image']['url'].startswith('data:image/jpeg;base64,')
    else:
        assert 'image' not in payload and 'images' not in payload
    output = capsys.readouterr().out
    assert '"provider": "xai"' in output and '"usd": 0.08' in output
    assert 'test-key' not in output


def test_missing_grok_key_fails_without_network(monkeypatch):
    monkeypatch.delenv('XAI_API_KEY', raising=False)
    monkeypatch.setattr(render.requests, 'post', lambda *a, **k: pytest.fail('No request without key'))
    with pytest.raises(RuntimeError, match='XAI_API_KEY'):
        render._grok_image_request('test', {})


def test_invalid_grok_key_has_actionable_error_without_secret(monkeypatch):
    monkeypatch.setenv('XAI_API_KEY', 'test-secret')
    monkeypatch.setattr(render.requests, 'post', lambda *a, **k: NS(status_code=400, text='Incorrect API key provided.'))
    with pytest.raises(RuntimeError, match='XAI_API_KEY') as error:
        render._grok_image_request('test', {})
    assert 'test-secret' not in str(error.value)


def test_hybrid_keeps_grok_model_and_labels_illustration(monkeypatch, tmp_path):
    buffer = BytesIO()
    Image.new('RGB', (1200, 1600), 'green').save(buffer, 'PNG')
    monkeypatch.setattr(render, '_download_cover_image', lambda *_: None)
    monkeypatch.setattr(render, 'OpenAI', lambda **_: pytest.fail('Model must stay Grok'))
    monkeypatch.setattr(render, '_grok_image_request', lambda **_: NS(data=[NS(b64_json=base64.b64encode(buffer.getvalue()).decode())]))
    card = CardNews(topic='music', rationale='', source_urls=[], caption='', hashtags=[],
                    slides=[Slide(role='cover', title='Music', body='')])
    files = render.render_hybrid(card, {'publication_frame':'exact', 'cover':{'renderer':'auto'}}, tmp_path/'out',
        image_transform={'model':'grok-imagine-image-2.0', 'enabled':True, 'recompose_for_publication':True})
    assert Image.open(files[0]).size == (1080,1440)
    assert card.slides[0].image_provider == 'grok_generated_illustration'
    assert 'AI 생성 설명 이미지' in card.caption


@pytest.mark.parametrize('status', [200, 400])
def test_grok_moderation_is_not_retried(monkeypatch, tmp_path, status):
    monkeypatch.setenv('XAI_API_KEY', 'test-key')
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return NS(status_code=status, text='moderation_blocked',
                  json=lambda: {'data': [{'respect_moderation': False}]})
    monkeypatch.setattr(render.requests, 'post', post)
    source = tmp_path/'source.png'
    Image.new('RGB', (300, 200), 'blue').save(source)
    options = {'enabled': True, 'model': 'grok-imagine-image-2.0', 'use_original_on_failure': False}
    with pytest.raises(RuntimeError, match='moderation_blocked'):
        render._transform_image_with_fallback(source, options, tmp_path/'cache')
    assert len(calls) == 1
