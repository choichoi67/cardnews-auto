import pytest

from cardnews_auto.openai_setup import _clean_api_key, _select_model


def test_clean_api_key_accepts_env_assignment_and_hidden_whitespace():
    assert _clean_api_key(' OPENAI_API_KEY="sk-proj-abc\u200b\n" ') == "sk-proj-abc"


def test_select_model_keeps_configured_when_available():
    assert _select_model({"custom-model", "gpt-5-mini"}, "custom-model") == "custom-model"


def test_select_model_uses_compatible_fallback():
    assert _select_model({"gpt-4.1-mini"}, "missing") == "gpt-4.1-mini"


def test_select_model_rejects_incompatible_account():
    with pytest.raises(RuntimeError):
        _select_model({"text-embedding-3-small"}, "missing")
