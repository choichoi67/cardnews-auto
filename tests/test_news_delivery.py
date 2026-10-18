import json
from datetime import datetime, timedelta, timezone

import pytest

from cardnews_auto import feeds, telegram_bot, webapp, cli
from cardnews_auto.models import Article
from cardnews_auto.workflow import candidate_time, save_candidates


def test_overview_sorts_mixed_dates_and_keeps_candidates_beyond_sixty(monkeypatch):
    items = [dict(title=f"Old {i}", url=f"https://example.com/{i}",
                  published="Tue, 28 Jul 2026 10:00:00 GMT") for i in range(65)]
    items += [dict(title="Newest", url="https://example.com/new", published="2026-10-13T12:00:00+09:00")]
    profile = dict(id="a", name="A", color="blue", workflow=dict(candidates=items))
    monkeypatch.setattr(webapp, "_registry", lambda: [profile])
    monkeypatch.setattr(webapp, "_public_profile", lambda p: p)
    monkeypatch.setattr(webapp, "newsroom_data", lambda: dict(metrics={}, artifacts=[]))
    monkeypatch.setattr(webapp, "list_audit_events", lambda **kw: [])
    monkeypatch.setattr(webapp, "job_views", lambda: [])
    result = webapp.overview()["newsroom"]["candidates"]
    assert len(result) == 66
    assert result[0]["title"] == "Newest"
    assert candidate_time(dict(published="invalid", first_seen_at="2026-10-13T00:00:00")).tzinfo


def test_collector_looks_past_duplicates_and_excludes_old_page_articles(monkeypatch):
    now = datetime.now(timezone.utc)
    articles = [
        Article(title="Existing album", url="https://example.com/a", published=now.isoformat()),
        Article(title="Completely different football news", url="https://example.com/b", published=(now-timedelta(hours=1)).isoformat()),
        Article(title="Ancient history", url="https://example.com/c", published=(now-timedelta(days=10)).isoformat()),
    ]
    monkeypatch.setattr(feeds, "collect_page_articles", lambda pages: articles)
    monkeypatch.setattr(feeds, "enrich_page_articles", lambda values: values)
    result = feeds.collect_articles(dict(max_candidates=1), [articles[0].model_dump()])
    assert [a.url for a in result] == [articles[0].url, articles[1].url]
    result = feeds.collect_articles(dict(max_candidates=10), [articles[0].model_dump()])
    assert len(result) == 2


def setup_review(tmp_path, monkeypatch, fail_on=None):
    path = tmp_path / "workflow.json"
    article = Article(title="A test article", url="https://example.com/a")
    save_candidates([article], "news", path)
    save_candidates([article], "news", path)
    calls = []

    class Client:
        chat_id = "private-test-destination"

        def send(self, text, keyboard=None):
            calls.append((text, keyboard))
            if len(calls) == fail_on:
                raise RuntimeError("test failure")
            return dict(message_id=len(calls))

    monkeypatch.setattr(telegram_bot, "client_from_env", lambda: Client())
    return path, calls


def test_zero_new_sends_one_clear_summary_and_records_receipt(tmp_path, monkeypatch):
    path, calls = setup_review(tmp_path, monkeypatch)
    telegram_bot.send_review(path)
    assert len(calls) == 1
    assert "확인 1건 · 중복 1건 · 신규 0건" in calls[0][0]
    assert calls[0][1] is None
    receipt = json.loads(path.with_name("telegram_review_delivery.json").read_text())
    assert receipt["status"] == "sent"
    assert receipt["message_ids"] == [1]
    assert "private-test-destination" not in json.dumps(receipt)


def test_manual_resend_selects_pending_even_after_duplicate_collection(tmp_path, monkeypatch):
    path, calls = setup_review(tmp_path, monkeypatch)
    telegram_bot.send_review(path, include_pending=True)
    assert len(calls) == 3
    assert "A test article" in calls[1][0]
    assert calls[1][1]["inline_keyboard"][0][0]["callback_data"].startswith("approveid:")


def test_partial_send_failure_preserves_successful_message_ids(tmp_path, monkeypatch):
    path, calls = setup_review(tmp_path, monkeypatch, fail_on=2)
    with pytest.raises(RuntimeError, match="일부 메시지"):
        telegram_bot.send_review(path, include_pending=True)
    receipt = json.loads(path.with_name("telegram_review_delivery.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["message_ids"] == [1]


def test_collect_does_not_report_success_when_notification_fails(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("news:\n  category: test\n  summarize_in_korean: false\ntelegram:\n  enabled: true\n")
    monkeypatch.setattr("sys.argv", ["cardnews-auto", "--config", str(config), "collect"])
    monkeypatch.setattr(cli, "collect_articles", lambda *a, **kw: [])
    monkeypatch.setattr(cli, "send_review", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("send failure")))
    with pytest.raises(RuntimeError, match="기사 저장은 완료"):
        cli.main()
    assert (tmp_path / "state" / "workflow.json").exists()


def test_football_approval_routes_to_football_even_when_candidate_is_in_both_channels(tmp_path, monkeypatch):
    from cardnews_auto import dashboard_jobs
    from cardnews_auto.workflow import load_state
    profiles = [dict(id="hiphop"), dict(id="football")]
    article = Article(title="Shared article", url="https://example.com/shared")
    for p in profiles:
        save_candidates([article], p["id"], tmp_path / p["id"] / "workflow.json")
    football_path = tmp_path / "football" / "workflow.json"
    state = load_state(football_path)
    monkeypatch.setattr(dashboard_jobs, "registry", lambda: profiles)
    monkeypatch.setattr(dashboard_jobs, "profile_config", lambda p: {"telegram": {"enabled": True}})
    monkeypatch.setattr(dashboard_jobs, "state_path", lambda p: tmp_path / p["id"] / "workflow.json")
    queued = []
    monkeypatch.setattr(dashboard_jobs, "enqueue_article_build", lambda p, a, actor: queued.append(p["id"]))
    revision = telegram_bot._revision(state) + "." + telegram_bot.review_scope(football_path)
    path, handler = dashboard_jobs.resolve_telegram_review("approveid", state["candidates"][0]["id"], revision)
    assert path == football_path
    handler(article)
    assert queued == ["football"]
    with pytest.raises(RuntimeError, match="채널"):
        dashboard_jobs.resolve_telegram_review("approveid", state["candidates"][0]["id"], "stale.unknown")


def test_listener_uses_resolved_channel_for_approval(tmp_path, monkeypatch):
    from cardnews_auto.workflow import load_state
    football_path = tmp_path / "football" / "workflow.json"
    state = save_candidates([Article(title="Football", url="https://example.com/football")], "football", football_path)
    updates = [{"update_id": 1, "callback_query": {
        "id": "query", "data": f"approveid:{state['candidates'][0]['id']}:revision.scope",
        "message": {"chat": {"id": 123}},
    }}]
    queued = []
    class Client:
        chat_id = "123"
        def call(self, method, payload=None, timeout=30):
            if method == "getUpdates":
                if updates:
                    return [updates.pop()]
                raise StopIteration
            return True
        def send(self, *args):
            return {}
    monkeypatch.setattr(telegram_bot, "client_from_env", lambda: Client())
    with pytest.raises(StopIteration):
        telegram_bot.listen_forever(
            tmp_path / "hiphop" / "workflow.json", tmp_path / "offset.json",
            resolve_review=lambda *args: (football_path, lambda article: queued.append(article.title) or {}),
        )
    assert queued == ["Football"]
    assert load_state(football_path)["approved"]["article"]["title"] == "Football"
    assert not (tmp_path / "hiphop" / "workflow.json").exists()
