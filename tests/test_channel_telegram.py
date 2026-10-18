import pytest
import requests

from cardnews_auto import dashboard_jobs
from cardnews_auto.runtime_lock import exclusive_runtime
from cardnews_auto.telegram_bot import TelegramClient, channel_environment, listen_forever


def test_channels_share_one_poller_but_reject_other_channels_jobs(monkeypatch, tmp_path):
    profiles = [{'id': 'music'}, {'id': 'football'}]
    monkeypatch.setattr(dashboard_jobs, 'profile_config', lambda profile: {
        'telegram': {'enabled': True, 'chat_id': '-1' if profile['id'] == 'music' else '-2'}})
    monkeypatch.setattr(dashboard_jobs, 'state_path', lambda profile: tmp_path / profile['id'])
    monkeypatch.setattr(dashboard_jobs, 'get_job', lambda job_id: {'profile_id': job_id})
    revisions = []
    monkeypatch.setattr(dashboard_jobs, 'revision_handler', lambda *args: revisions.append(args))
    environment = {'TELEGRAM_BOT_TOKEN': 'test-token', 'TELEGRAM_CHAT_ID': 'old'}
    groups = dashboard_jobs.telegram_routes(profiles, environment)
    assert len(groups) == 1
    routes = next(iter(groups.values()))['routes']
    assert set(routes) == {('-1', 0), ('-2', 0)}
    routes[('-1', 0)]['on_revision']('music', 'edit')
    with pytest.raises(RuntimeError):
        routes[('-2', 0)]['on_revision']('music', 'edit')
    assert revisions == [('music', 'edit')]
    assert environment['TELEGRAM_CHAT_ID'] == 'old'


def test_channel_thread_and_missing_custom_token_do_not_leak_defaults(monkeypatch):
    environment = {'TELEGRAM_BOT_TOKEN': 'default', 'TELEGRAM_CHAT_ID': 'personal'}
    routed = channel_environment({'telegram': {'bot_token_env': 'TELEGRAM_OTHER_TOKEN'}}, environment)
    assert routed['TELEGRAM_BOT_TOKEN'] == routed['TELEGRAM_CHAT_ID'] == ''
    client = TelegramClient('dummy', '-1', 42)
    captured = []
    monkeypatch.setattr(client, 'call', lambda method, payload: captured.append(payload))
    client.send('test')
    assert captured[0]['message_thread_id'] == 42


def test_network_errors_do_not_expose_bot_token(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError('https://api.telegram.org/botSECRET/sendMessage')
    monkeypatch.setattr('cardnews_auto.telegram_bot.requests.post', fail)
    with pytest.raises(RuntimeError) as error:
        TelegramClient('SECRET', '-1').send('test')
    assert 'SECRET' not in str(error.value)


def test_runtime_lock_rejects_duplicate_and_releases(tmp_path):
    path = tmp_path / 'runtime.lock'
    with exclusive_runtime(path):
        with pytest.raises(RuntimeError):
            with exclusive_runtime(path):
                pytest.fail('duplicate runtime acquired lock')
    with exclusive_runtime(path):
        assert path.exists()


def test_listener_only_accepts_admin_in_assigned_room(tmp_path):
    revisions = []
    answers = []
    updates = []
    for index, (chat, actor) in enumerate([(-9, 1), (-1, 2), (-1, 1)], 1):
        updates.append({'update_id': index, 'callback_query': {
            'id': str(index), 'from': {'id': actor}, 'data': 'remake:_:music',
            'message': {'chat': {'id': chat, 'type': 'group'}}}})

    class Client:
        chat_id = '-1'

        def call(self, method, payload=None, timeout=30):
            if method == 'getWebhookInfo':
                return {}
            if method == 'getUpdates':
                if not updates:
                    raise StopIteration
                return [updates.pop(0)]
            if method == 'getChatMember':
                return {'status': 'creator' if payload['user_id'] == 1 else 'member'}
            if method == 'answerCallbackQuery':
                answers.append(payload)
            return True

    client = Client()
    routes = {('-1', 0): {'client': client, 'state_path': tmp_path / 'state.json',
                          'on_revision': lambda *args: revisions.append(args) or {}}}
    with pytest.raises(StopIteration):
        listen_forever(tmp_path / 'state.json', tmp_path / 'offset.json',
                       routes=routes, polling_client=client)
    assert revisions == [('music', '', '', 0, '', '')]
    assert len(answers) == 3
    assert [answer['show_alert'] for answer in answers] == [True, True, False]


def test_expired_callback_answer_does_not_stop_listener_or_repeat_revision(tmp_path):
    revisions = []
    polls = []

    class Client:
        chat_id = '-1'

        def call(self, method, payload=None, timeout=30):
            if method == 'getWebhookInfo':
                return {}
            if method == 'getUpdates':
                polls.append(payload['offset'])
                if len(polls) > 1:
                    raise StopIteration
                return [{'update_id': 10, 'callback_query': {'id': 'expired',
                        'data': 'remake:_:job', 'from': {'id': 1},
                        'message': {'chat': {'id': -1, 'type': 'private'}}}}]
            if method == 'answerCallbackQuery':
                raise RuntimeError('query is too old')

    client = Client()
    routes = {('-1', 0): {'client': client, 'state_path': tmp_path / 'state.json',
                          'on_revision': lambda *args: revisions.append(args) or {}}}
    with pytest.raises(StopIteration):
        listen_forever(tmp_path / 'state.json', tmp_path / 'offset.json',
                       routes=routes, polling_client=client)
    assert polls == [0, 11]
    assert len(revisions) == 1
