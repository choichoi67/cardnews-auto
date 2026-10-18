"""Per-request API usage. Prices verified 2026-10-13; USD standard tier estimates.
Sources: https://developers.openai.com/api/docs/pricing
https://docs.x.ai/developers/cost-tracking
https://docs.x.ai/developers/models/grok-imagine-image-2.0
SDK-internal network retries cannot be billed independently without response usage.
"""
import json
import threading
import time
import uuid

PREFIX = '[NOWA_PROGRESS] '
_lock = threading.Lock()
TEXT_RATES = {'gpt-6-sol': (2, .2, 2.5, 10), 'gpt-6-luna': (.1, .01, .125, .5)}


def as_dict(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return vars(value) if hasattr(value, '__dict__') else {}


def price(response, provider, model):
    usage = as_dict(getattr(response, 'usage', None) if not isinstance(response, dict) else response.get('usage'))
    if provider == 'xai' and usage.get('cost_in_usd_ticks') is not None:
        return float(usage['cost_in_usd_ticks']) / 1e10, 'reported', usage
    if provider == 'xai' and model == 'grok-imagine-image-2.0':
        data = getattr(response, 'data', []) or []
        if data:
            return .08 * len(data) + .01 * getattr(response, 'input_image_count', 0), 'estimated', usage  # 2k / medium plus reference images.
    if not usage or usage.get('input_tokens') is None or usage.get('output_tokens') is None:
        return None, 'unknown', usage
    tier = getattr(response, 'service_tier', None)
    if tier not in (None, 'auto', 'default', 'standard'):
        return None, 'unknown', usage
    details = as_dict(usage.get('input_tokens_details'))
    cached = int(details.get('cached_tokens', 0) or 0)
    writes = int(details.get('cache_creation_tokens', 0) or 0)
    input_count, output_count = int(usage['input_tokens']), int(usage['output_tokens'])
    base_model = next((key for key in TEXT_RATES if model == key or model.startswith(key + '-20')), None)
    if base_model:
        rate, cache_rate, write_rate, out_rate = TEXT_RATES[base_model]
        if input_count > 272000:
            rate, cache_rate, write_rate, out_rate = rate*2, cache_rate*2, write_rate*2, out_rate*1.5
        cost = (max(0,input_count-cached-writes)*rate + cached*cache_rate + writes*write_rate + output_count*out_rate)/1e6
        outputs = getattr(response, 'output', []) or []
        cost += .01 * sum((item.get('type') if isinstance(item, dict) else getattr(item, 'type', None)) == 'web_search_call' for item in outputs)
        return cost, 'estimated', usage
    if model.startswith('gpt-image-2.5-sunburst'):
        # Do not guess the text/image split of cached tokens or unknown output modalities.
        if cached or details.get('text_tokens') is None or details.get('image_tokens') is None:
            return None, 'unknown', usage
        output_details = as_dict(usage.get('output_tokens_details'))
        if output_details.get('text_tokens', 0):
            return None, 'unknown', usage
        if int(details['text_tokens']) + int(details['image_tokens']) != input_count:
            return None, 'unknown', usage
        return (int(details['text_tokens'])*5 + int(details['image_tokens'])*8 + output_count*30)/1e6, 'estimated', usage
    return None, 'unknown', usage


def emit_usage(label, provider, model, seconds, cost, basis, usage=None, status='completed'):
    event = dict(kind='usage', id=uuid.uuid4().hex, label=label, provider=provider, model=model,
                 seconds=round(seconds,3), usd=cost, basis=basis, usage=usage or {}, status=status,
                 pricing_date='2026-10-13')
    with _lock:
        print(PREFIX + json.dumps(event, ensure_ascii=False), flush=True)


def metered_call(label, provider, tracked_model, callback, **kwargs):
    started = time.monotonic()
    try:
        response = callback(**kwargs)
    except Exception:
        emit_usage(label, provider, tracked_model, time.monotonic()-started, None, 'unknown', status='failed')
        raise
    try:
        cost, basis, usage = price(response, provider, tracked_model)
    except Exception:
        cost, basis, usage = None, 'unknown', {}
    emit_usage(label, provider, tracked_model, time.monotonic()-started, cost, basis, usage)
    return response


def cache_hit(label, model):
    emit_usage(label, 'cache', model, 0, 0, 'cache', status='cached')
