"""Every test runs without the production .env: no real API keys, so no test can reach a paid API."""
import pytest

SECRETS = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_WRITER_MODEL", "XAI_API_KEY",
           "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


@pytest.fixture(autouse=True)
def no_production_secrets(monkeypatch):
    # Importing webapp loads .env at collection time; clear what it set before each test runs.
    for name in SECRETS:
        monkeypatch.delenv(name, raising=False)
    from cardnews_auto import cli, dashboard_core, webapp
    for module in (dashboard_core, cli, webapp):
        monkeypatch.setattr(module, "load_env", lambda *args, **kwargs: None)
