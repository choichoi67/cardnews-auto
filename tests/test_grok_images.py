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


@pytest.mark.parametrize('source_count', [1])
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
    result = run()
    assert Image.open(result).size == (1776, 2368)
    assert run() == result and len(calls) == 1
    url, request = calls[0]
    assert request['timeout'] == (10.0, 300.0)
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


@pytest.mark.parametrize('seconds,expected', [(180, 180.0), (1, 30.0)])
def test_grok_timeout_is_configurable_and_does_not_repeat_paid_request(monkeypatch, seconds, expected):
    monkeypatch.setenv('XAI_API_KEY', 'test-secret')
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        raise render.requests.ReadTimeout('slow server')
    monkeypatch.setattr(render.requests, 'post', post)
    with pytest.raises(RuntimeError, match='자동 재요청하지 않았습니다') as error:
        render._grok_image_request('test', {'timeout_seconds': seconds})
    assert len(calls) == 1
    assert calls[0]['timeout'] == (10.0, expected)
    assert 'test-secret' not in str(error.value)


def test_invalid_grok_key_has_actionable_error_without_secret(monkeypatch):
    monkeypatch.setenv('XAI_API_KEY', 'test-secret')
    monkeypatch.setattr(render.requests, 'post', lambda *a, **k: NS(status_code=400, text='Incorrect API key provided.'))
    with pytest.raises(RuntimeError, match='XAI_API_KEY') as error:
        render._grok_image_request('test', {})
    assert 'test-secret' not in str(error.value)


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
