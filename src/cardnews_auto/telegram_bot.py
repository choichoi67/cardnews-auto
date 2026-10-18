import hashlib
import getpass
import json
import os
import re
import time
import webbrowser
from contextlib import ExitStack
from pathlib import Path
from typing import Callable

import requests

from .dashboard_core import _env_values
from .models import Article, JOB_STATES
from .workflow import approve_candidate_by_id, latest_candidates, load_state, candidate_time, _now, _write_state


class TelegramClient:
    def __init__(self, token: str, chat_id: str | None = None, thread_id: int | None = None):
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN이 없습니다.")
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id) if chat_id else None
        self.thread_id = thread_id

    def call(self, method: str, payload: dict | None = None, timeout: int = 30):
        try:
            response = requests.post(f"{self.base_url}/{method}", json=payload or {}, timeout=timeout + 5)
        except requests.RequestException:
            raise RuntimeError('Telegram 연결 실패: 처리 결과를 확인한 뒤 재시도하세요.') from None
        return self._result(response, method)

    @staticmethod
    def _result(response, method: str):
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Telegram 응답을 읽지 못했습니다. HTTP {response.status_code}") from exc
        if not data.get("ok"):
            description = data.get("description", f"Telegram {method} 실패")
            if response.status_code == 401:
                raise RuntimeError("유효하지 않은 Telegram 토큰입니다. BotFather에서 새 토큰을 발급받아 전체를 복사하세요.")
            raise RuntimeError(description)
        return data["result"]

    def send(self, text: str, reply_markup: dict | None = None):
        if not self.chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID가 없습니다.")
        payload = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        if self.thread_id:
            payload['message_thread_id'] = self.thread_id
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", payload)

    def _upload(self, method: str, data: dict, files: dict) -> dict | list[dict]:
        if not self.chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID가 없습니다.")
        if self.thread_id:
            data['message_thread_id'] = self.thread_id
        try:
            response = requests.post(f"{self.base_url}/{method}", data=data, files=files, timeout=120)
        except requests.RequestException:
            raise RuntimeError('Telegram 업로드 결과가 불확실합니다. 수신 여부 확인 전 재전송하지 마세요.') from None
        return self._result(response, method)

    def send_media_group(self, paths: list[Path], caption: str = "") -> list[dict]:
        if not 2 <= len(paths) <= 10:
            raise RuntimeError("Telegram 묶음 전송은 이미지 2~10장이 필요합니다.")
        media = []
        files = {}
        with ExitStack() as stack:
            for index, path in enumerate(paths):
                key = f"photo{index}"
                handle = stack.enter_context(path.open("rb"))
                files[key] = (path.name, handle, "image/jpeg")
                item = {"type": "photo", "media": f"attach://{key}"}
                if index == 0 and caption:
                    item["caption"] = caption[:1024]
                media.append(item)
            data = {"chat_id": self.chat_id, "media": json.dumps(media, ensure_ascii=False)}
            return self._upload("sendMediaGroup", data, files)

    def send_photo(self, path: Path, caption: str = "") -> dict:
        with path.open("rb") as handle:
            files = {"photo": (path.name, handle, "image/jpeg")}
            return self._upload("sendPhoto", {"chat_id": self.chat_id, "caption": caption[:1024]}, files)


def client_from_env(require_chat: bool = True, *, environ=None) -> TelegramClient:
    values = os.environ if environ is None else environ
    token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = values.get("TELEGRAM_CHAT_ID", "").strip()
    if require_chat and not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID가 없습니다. telegram-setup으로 봇을 연결하세요.")
    return TelegramClient(token, chat_id or None, int(values.get('TELEGRAM_THREAD_ID') or 0) or None)


def channel_environment(config: dict, environ=None) -> dict:
    values = dict(os.environ if environ is None else environ)
    settings = config.get('telegram', {})
    token_name = settings.get('bot_token_env') or 'TELEGRAM_BOT_TOKEN'
    values['TELEGRAM_BOT_TOKEN'] = values.get(token_name, '').strip()
    if settings.get('chat_id'):
        values['TELEGRAM_CHAT_ID'] = str(settings['chat_id'])
    elif token_name != 'TELEGRAM_BOT_TOKEN':
        values['TELEGRAM_CHAT_ID'] = ''
    values['TELEGRAM_THREAD_ID'] = str(settings.get('thread_id') or '')
    return values


def _revision(state: dict) -> str:
    return hashlib.sha256(state["collected_at"].encode()).hexdigest()[:10]


def review_scope(state_path: Path) -> str:
    return hashlib.sha256(str(state_path.resolve()).casefold().encode()).hexdigest()[:10]


def send_review(state_path: Path, max_candidates: int = 10, *, include_pending: bool = False) -> list[dict]:
    state = load_state(state_path)
    client = client_from_env()
    candidates = latest_candidates(state, max_candidates)
    if include_pending:
        approved_id = (state.get("approved") or {}).get("candidate_id")
        candidates = sorted(
            ((index, item) for index, item in enumerate(state.get("candidates", []), 1)
             if (item.get("disposition") or "active") == "active"
             and not item.get("built_count") and item.get("id") != approved_id),
            key=lambda pair: candidate_time(pair[1]), reverse=True,
        )[:max_candidates]
    revision = f"{_revision(state)}.{review_scope(state_path)}"
    receipt_path = state_path.with_name("telegram_review_delivery.json")
    receipt = {"started_at": _now(), "status": "sending", "message_ids": [],
               "candidate_ids": [item.get("id") for _, item in candidates],
               "destination_fingerprint": hashlib.sha256(str(client.chat_id).encode()).hexdigest()[:12]}
    _write_state(receipt_path, receipt)

    def send(text: str, keyboard: dict | None = None) -> dict:
        try:
            result = client.send(text, keyboard)
        except Exception as exc:
            receipt.update(status="failed", finished_at=_now(), error_type=type(exc).__name__)
            _write_state(receipt_path, receipt)
            raise RuntimeError("Telegram 전송 실패: 일부 메시지가 전송됐을 수 있습니다. 전송 기록을 확인하세요.") from None
        receipt["message_ids"].append(result["message_id"])
        _write_state(receipt_path, receipt)
        return result

    collection = (state.get("collections") or [{}])[-1]
    summary = (
        f"📰 뉴스 수집 결과\n카테고리: {state['category']}\n"
        f"확인 {collection.get('received_count', 0)}건 · "
        f"중복 {collection.get('duplicate_count', 0)}건 · 신규 {collection.get('added_count', len(candidates))}건\n"
    )
    summary += (f"검토할 후보 {len(candidates)}건을 전달합니다." if candidates else
                "이번 수집에서 새로 추가된 기사가 없습니다. 기존 후보는 웹 뉴스 목록에 남아 있습니다.")
    results = [send(summary)]
    for position, (index, item) in enumerate(candidates, 1):
        summary = item.get("summary") or "기사 요약을 가져오지 못했습니다. 제목과 원문을 확인하세요."
        published = item.get("published") or "발행 시각 미상"
        text = (
            f"{position}/{len(candidates)}  {item['title']}\n\n"
            f"핵심: {summary[:700]}\n\n"
            f"출처: {item.get('source') or '출처 미상'}\n"
            f"발행: {published}\n"
            f"원문: {item['url']}"
        )
        keyboard = {"inline_keyboard": [[{
            "text": f"✅ {position}번 승인·제작",
            "callback_data": f"approveid:{item['id']}:{revision}",
        }]]}
        results.append(send(text[:4096], keyboard))
    if candidates:
        results.append(send(
            "이번 회차를 제작하지 않으려면 아래 버튼을 누르세요.",
            {"inline_keyboard": [[{"text": "⏸ 이번 회차 보류", "callback_data": f"hold:0:{revision}"}]]},
        ))
    receipt.update(status="sent", finished_at=_now())
    _write_state(receipt_path, receipt)
    print(f"Telegram 전송 완료: 후보 {len(candidates)}건, 메시지 {len(results)}건, 메시지 번호 {receipt['message_ids']}")
    return results


def send_delivery(
    images: list[Path],
    caption: str,
    drive_url: str,
    topic: str,
    source_interpretation: str = "",
    editorial_summary: str = "",
) -> None:
    client = client_from_env()
    drive_line = f"\n\n🔗 Google Drive\n{drive_url}" if drive_url else ""
    client.send((
        f"✅ 카드뉴스 제작 완료\n\n{topic}\n"
        f"총 {len(images)}장{drive_line}"
    )[:4096])
    summary = f"카드뉴스 미리보기 · {len(images)}장"
    if drive_url:
        summary += f"\n🔗 {drive_url}"
    total = len(images)
    for cursor in range(0, total, 10):
        chunk = images[cursor:cursor + 10]
        batch_caption = summary if cursor == 0 else (
            f"추가 카드 {cursor + 1}/{total}" if len(chunk) == 1
            else f"추가 카드 {cursor + 1}~{cursor + len(chunk)}/{total}"
        )
        if len(chunk) == 1:
            client.send_photo(chunk[0], batch_caption)
        else:
            client.send_media_group(chunk, batch_caption)
    full_caption = caption.strip()
    if full_caption:
        client.send(f"📝 게시용 캡션\n\n{full_caption}"[:4096])
    if source_interpretation:
        client.send(f"🧾 원고 검수 · 1/2 원문 해석\n\n{source_interpretation}"[:4096])
    if editorial_summary:
        client.send(f"🧾 원고 검수 · 2/2 편집 요약\n\n{editorial_summary}"[:4096])


def send_build_status(
    status: str, topic: str, job_id: str, *, source: str = "웹 대시보드",
    position: int = 0, total: int = 0, error: str = "", environ=None,
) -> dict:
    position_text = f"{position}번째" if position else "대기 중"
    source_label = {"browser": "브라우저", "telegram": "텔레그램"}.get(source, source)
    client = client_from_env() if environ is None else client_from_env(environ=environ)
    return client.send(JOB_STATES[status]["message"].format(
        topic=topic, job_id=job_id[:8], source=source_label,
        position=position_text, total=total, error=error[:2500],
    )[:4096])


def _revision_field(text: str, number: int, next_number: int | None) -> str:
    boundary = rf"(?=\n\s*{next_number}[.)]|\Z)" if next_number else r"\Z"
    match = re.search(
        rf"(?:^|\n)\s*{number}[.)]\s*[^:\n]*:?\s*(.*?){boundary}",
        text,
        re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _parse_revision_answer(text: str) -> tuple[str, str, int, str, str]:
    slide_text = _revision_field(text, 1, 2)
    instruction = _revision_field(text, 2, 3)
    slide_title = _revision_field(text, 3, 4)
    slide_body = _revision_field(text, 4, 5)
    if not any((slide_text, instruction, slide_title, slide_body)):
        return text.strip(), "", 0, "", ""
    match = re.search(r"\d+", slide_text)
    slide_number = int(match.group()) if match else 0
    cover_title = slide_title if slide_number == 1 else ""
    return instruction, cover_title, slide_number, slide_title, slide_body


def send_revision_controls(
    topic: str,
    profile_id: str,
    job_id: str,
    profile_name: str = "", *, environ=None,
) -> dict:
    client = client_from_env() if environ is None else client_from_env(environ=environ)
    return client.send(
        (
            "결과물이 마음에 들지 않나요?\n\n"
            f"수정 대상 매거진: {profile_name or profile_id}\n"
            f"승인 뉴스 · 주제: {topic}\n\n"
            "수정 지시를 보내거나 같은 기사로 새 버전을 만들 수 있습니다."
        )[:4096],
        {"inline_keyboard": [[
            {"text": "✏️ 수정 후 재제작", "callback_data": f"edit:_:{job_id}"},
            {"text": "♻️ 바로 재제작", "callback_data": f"remake:_:{job_id}"},
        ]]},
    )


def _update_env(env_path: Path, values: dict[str, str]) -> None:
    current = _env_values(env_path)
    current.update(values)
    env_path.write_text(
        "".join(f"{key}={value}\n" for key, value in current.items()), encoding="utf-8",
    )


def setup_interactive(env_path: Path) -> tuple[str, str]:
    print("Telegram @BotFather에서 발급받은 봇 토큰을 입력하세요.")
    token = getpass.getpass("Bot token (화면에 표시되지 않음): ").strip()
    client = TelegramClient(token)
    bot = client.call("getMe")
    username = bot["username"]
    print(f"봇 확인 완료: @{username}")
    _update_env(env_path, {"TELEGRAM_BOT_TOKEN": token})
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    bot_url = f"https://t.me/{username}"
    webbrowser.open(bot_url)
    print(f"휴대폰 Telegram에서 @{username}에게 /start를 보내세요.")
    print("최대 2분 동안 /start 메시지를 기다립니다...")
    deadline = time.monotonic() + 120
    offset = 0
    while time.monotonic() < deadline:
        updates = client.call("getUpdates", {"offset": offset, "timeout": 10, "allowed_updates": ["message"]}, timeout=10)
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message", {})
            chat = message.get("chat", {})
            if chat.get("type") == "private":
                chat_id = str(chat["id"])
                _update_env(env_path, {"TELEGRAM_CHAT_ID": chat_id})
                os.environ["TELEGRAM_CHAT_ID"] = chat_id
                TelegramClient(token, chat_id).send("✅ 카드뉴스 승인 봇 연결 완료")
                return username, chat_id
    raise RuntimeError("2분 안에 /start를 확인하지 못했습니다. 다시 telegram-setup을 실행하세요.")


def listen_forever(
    state_path: Path,
    offset_path: Path,
    on_approved: Callable[[Article], Path | dict] | None = None,
    on_revision: Callable[[str, str, str, int, str, str], dict] | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    resolve_review: Callable | None = None,
    *, routes: dict | None = None, polling_client=None,
) -> None:
    client = polling_client or client_from_env()
    poller = client
    offset_path.parent.mkdir(parents=True, exist_ok=True)
    offset = json.loads(offset_path.read_text(encoding="utf-8")).get("offset", 0) if offset_path.exists() else 0
    if routes is not None and poller.call('getWebhookInfo').get('url'):
        raise RuntimeError('이미 웹훅이 연결된 봇입니다. 기존 서비스를 중단하고 명시적으로 전환하세요.')
    if routes is None:
        client.call("deleteWebhook", {"drop_pending_updates": False})
    print("Telegram 승인 수신 대기 중... Ctrl+C로 종료")
    pending_revisions: dict[tuple, dict] = {}
    while not should_stop():
        updates = poller.call(
            "getUpdates",
            {"offset": offset, "timeout": 25, "allowed_updates": ["callback_query", "message"]},
            timeout=25,
        )
        for update in updates:
            offset = update["update_id"] + 1
            offset_path.write_text(json.dumps({"offset": offset}), encoding="utf-8")
            incoming = update.get('message') or update.get('callback_query', {}).get('message') or {}
            actor = update.get('message', {}).get('from') or update.get('callback_query', {}).get('from') or {}
            destination = (str(incoming.get('chat', {}).get('id', '')), incoming.get('message_thread_id') or 0)
            pending_key = (*destination, str(actor.get('id', '')))
            if routes is not None:
                route = routes.get(destination)
                if route is None:
                    continue
                client = route['client']
                state_path, on_approved = route['state_path'], route.get('on_approved')
                on_revision, resolve_review = route.get('on_revision'), route.get('resolve_review')
                if incoming.get('chat', {}).get('type') in {'group', 'supergroup'}:
                    if not actor.get('id'):
                        continue
                    member = client.call('getChatMember', {'chat_id':client.chat_id, 'user_id':actor['id']})
                    if member.get('status') not in {'creator', 'administrator'}:
                        continue
            pending_revision = pending_revisions.setdefault(pending_key, {})
            message = update.get("message")
            if message:
                message_chat = str(message.get("chat", {}).get("id", ""))
                text = str(message.get("text") or "").strip()
                if text.startswith('/'):
                    command, separator, rest = text.partition(' ')
                    text = command.split('@', 1)[0] + separator + rest
                if message_chat != client.chat_id or not text:
                    continue
                explicit = text.split(maxsplit=2) if text.startswith("/수정 ") else []
                target_job = explicit[1] if len(explicit) >= 3 else pending_revision.get("job_id", "")
                answer = explicit[2] if len(explicit) >= 3 else text
                if text == "/취소":
                    pending_revision.clear()
                    client.send("수정 요청을 취소했습니다.")
                elif target_job and on_revision:
                    try:
                        instruction, cover_title, slide_number, slide_title, slide_body = _parse_revision_answer(answer)
                        on_revision(
                            target_job,
                            instruction,
                            cover_title,
                            slide_number,
                            slide_title,
                            slide_body,
                        )
                        pending_revision.clear()
                    except Exception as exc:
                        client.send(f"❌ 수정본 등록 실패\n{exc}")
                continue
            query = update.get("callback_query")
            if not query:
                continue
            query_chat = str(query.get("message", {}).get("chat", {}).get("id", ""))
            if query_chat != client.chat_id:
                client.call("answerCallbackQuery", {"callback_query_id": query["id"], "text": "승인 권한이 없습니다.", "show_alert": True})
                continue
            try:
                action, raw_index, revision = query.get("data", "").split(":", 2)
                if action in {"edit", "remake"}:
                    if not on_revision:
                        raise RuntimeError("재제작 연결이 아직 준비되지 않았습니다.")
                    if action == "edit":
                        if routes is not None:
                            route['validate_job'](revision)
                        pending_revision['job_id'] = revision
                        context = str(query.get("message", {}).get("text") or "").strip()
                        client.call("answerCallbackQuery", {
                            "callback_query_id": query["id"],
                            "text": "수정할 내용을 채팅으로 보내주세요.",
                        })
                        client.send(
                            f"✏️ 산출물 수정\n\n{context}\n\n"
                            "아래 번호에 맞춰 답변해주세요.\n"
                            "1. 수정할 카드 번호 (표지 1번 · 본문 2번부터 마지막까지)\n"
                            "2. 수정 지시사항\n"
                            "3. 선택한 카드 제목\n"
                            "4. 선택한 카드 본문\n"
                            "5. 참고 이미지 (JPG·PNG·WebP는 브라우저에서 추가)\n\n"
                            "답변 양식\n"
                            "1. 수정할 카드 번호: [숫자]\n"
                            "2. 수정 지시사항: [내용]\n"
                            "3. 선택한 카드 제목: [새 제목]\n"
                            "4. 선택한 카드 본문: [새 본문]\n"
                            "5. 참고 이미지: 브라우저에서 추가\n\n취소: /취소",
                            {'force_reply': True, 'input_field_placeholder': '카드 번호와 수정 내용을 답장하세요.'},
                        )
                    else:
                        result = on_revision(revision, "", "", 0, "", "")
                        position = (result.get("build_job") or {}).get("queue_position", "")
                        client.call("answerCallbackQuery", {
                            "callback_query_id": query["id"],
                            "text": f"재제작 대기열 등록 완료{f' · {position}번째' if position else ''}",
                        })
                    continue
                review_path, approved_handler = state_path, on_approved
                if resolve_review:
                    review_path, approved_handler = resolve_review(action, raw_index, revision)
                elif "." in revision and revision.split(".", 1)[1] != review_scope(state_path):
                    raise RuntimeError("다른 채널의 승인 버튼입니다.")
                state = load_state(review_path)
                if action == "hold":
                    if revision.split(".", 1)[0] != _revision(state):
                        raise RuntimeError("이미 새 수집 회차가 시작되어 이 보류 버튼은 만료되었습니다.")
                    client.call("answerCallbackQuery", {"callback_query_id": query["id"], "text": "이번 회차를 보류했습니다."})
                    client.send("⏸ 이번 후보 회차를 보류했습니다.")
                    continue
                if action != "approveid":
                    raise RuntimeError("만료되었거나 지원하지 않는 승인 버튼입니다.")
                article = approve_candidate_by_id(review_path, raw_index)
                client.call("answerCallbackQuery", {"callback_query_id": query["id"], "text": "승인되었습니다."})
                client.send(f"✅ 승인 완료\n{article.title}")
                if approved_handler:
                    try:
                        result = approved_handler(article)
                        if not isinstance(result, dict):
                            client.send(f"🎨 카드뉴스 제작 완료\n{result}")
                    except Exception as exc:
                        message = str(exc)
                        if "OPENAI_API_KEY" in message or "api_key" in message.lower():
                            client.send("⏸ 제작 보류\nOpenAI API 키가 아직 설정되지 않았습니다. 승인은 정상 저장됐습니다.")
                        else:
                            client.send(f"❌ 제작 실패\n{message}")
            except Exception as exc:
                client.call(
                    "answerCallbackQuery",
                    {"callback_query_id": query["id"], "text": str(exc)[:180], "show_alert": True},
                )
