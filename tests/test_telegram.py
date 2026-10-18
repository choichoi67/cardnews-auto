from pathlib import Path

import pytest

from cardnews_auto.telegram_bot import (
    TelegramClient,
    client_from_env,
    _parse_revision_answer,
    _update_env,
    listen_forever,
    send_build_status,
    send_delivery,
    send_revision_controls,
)


def test_explicit_job_environment_overrides_stale_process_credentials(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "old-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "old-chat")
    client = client_from_env(environ={"TELEGRAM_BOT_TOKEN": "saved-token", "TELEGRAM_CHAT_ID": "saved-chat"})
    assert client.base_url == "https://api.telegram.org/botsaved-token"
    assert client.chat_id == "saved-chat"


def test_update_env_preserves_other_values(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=abc\nTELEGRAM_BOT_TOKEN=old\n", encoding="utf-8")
    _update_env(env, {"TELEGRAM_BOT_TOKEN": "new", "TELEGRAM_CHAT_ID": "123"})
    value = env.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=abc" in value
    assert "TELEGRAM_BOT_TOKEN=new" in value
    assert "TELEGRAM_CHAT_ID=123" in value
    assert "old" not in value


def test_send_delivery_sends_album_then_caption(monkeypatch, tmp_path: Path):
    calls = []

    class FakeClient:
        def send_media_group(self, paths, caption):
            calls.append(("album", [p.name for p in paths], caption))

        def send(self, text):
            calls.append(("message", text))

        def send_photo(self, path, caption):
            calls.append(("photo", path.name, caption))

    monkeypatch.setattr("cardnews_auto.telegram_bot.client_from_env", lambda: FakeClient())
    images = []
    for index in range(2):
        path = tmp_path / f"slide_{index + 1:02d}.jpg"
        path.write_bytes(b"image")
        images.append(path)
    send_delivery(
        images,
        "게시용 캡션",
        "https://drive.google.com/folder",
        "테스트 주제",
        "원문의 의미를 보존한 한국어 해석입니다.",
        "배경과 결과를 포함한 편집 요약입니다.",
    )
    assert calls[0][0] == "message"
    assert "카드뉴스 제작 완료" in calls[0][1]
    assert "https://drive.google.com/folder" in calls[0][1]
    assert calls[1][0] == "album"
    assert calls[1][1] == ["slide_01.jpg", "slide_02.jpg"]
    assert calls[2] == ("message", "📝 게시용 캡션\n\n게시용 캡션")
    assert calls[3][0] == "message"
    assert "1/2 원문 해석" in calls[3][1]
    assert calls[4][0] == "message"
    assert "2/2 편집 요약" in calls[4][1]


def test_send_delivery_sends_every_slide_over_ten(monkeypatch, tmp_path: Path):
    sent = []

    class FakeClient:
        def send_media_group(self, paths, caption):
            sent.extend(path.name for path in paths)

        def send_photo(self, path, caption):
            sent.append(path.name)

        def send(self, text):
            pass

    monkeypatch.setattr("cardnews_auto.telegram_bot.client_from_env", lambda: FakeClient())
    images = []
    for index in range(13):
        path = tmp_path / f"slide_{index + 1:02d}.jpg"
        path.write_bytes(b"image")
        images.append(path)
    send_delivery(images, "", "https://drive.google.com/folder", "긴 카드뉴스")
    assert sent == [path.name for path in images]


def test_send_delivery_without_drive_still_sends_outputs(monkeypatch, tmp_path: Path):
    calls = []

    class FakeClient:
        def send_media_group(self, paths, caption):
            calls.append(("album", len(paths), caption))

        def send_photo(self, path, caption):
            calls.append(("photo", path.name, caption))

        def send(self, text):
            calls.append(("message", text))

    monkeypatch.setattr("cardnews_auto.telegram_bot.client_from_env", lambda: FakeClient())
    image = tmp_path / "slide_01.jpg"
    image.write_bytes(b"image")

    send_delivery([image], "caption", "", "풋매코 결과물")

    assert calls[0] == ("message", "✅ 카드뉴스 제작 완료\n\n풋매코 결과물\n총 1장")
    assert calls[1] == ("photo", "slide_01.jpg", "카드뉴스 미리보기 · 1장")


def test_photo_upload_uses_shared_telegram_error_handling(monkeypatch, tmp_path):
    image = tmp_path / "slide.jpg"
    image.write_bytes(b"image")

    class Response:
        status_code = 400

        def json(self):
            return {"ok": False, "description": "upload rejected"}

    monkeypatch.setattr("cardnews_auto.telegram_bot.requests.post", lambda *args, **kwargs: Response())

    with pytest.raises(RuntimeError, match="upload rejected"):
        TelegramClient("token", "chat").send_photo(image)


def test_media_group_preserves_payload_and_closes_files(monkeypatch, tmp_path):
    paths = [tmp_path / "one.jpg", tmp_path / "two.jpg"]
    for path in paths:
        path.write_bytes(b"image")
    captured = {}

    def upload(_method, data, files):
        captured.update(data=data, handles=[item[1] for item in files.values()])
        assert all(not handle.closed for handle in captured["handles"])
        return [{"message_id": 1}]

    client = TelegramClient("token", "chat")
    monkeypatch.setattr(client, "_upload", upload)
    assert client.send_media_group(paths, "미리보기") == [{"message_id": 1}]
    assert '"caption": "미리보기"' in captured["data"]["media"]
    assert all(handle.closed for handle in captured["handles"])


def test_build_lifecycle_messages_include_job(monkeypatch):
    sent = []

    class FakeClient:
        def send(self, text):
            sent.append(text)
            return {"message_id": len(sent)}

    monkeypatch.setattr("cardnews_auto.telegram_bot.client_from_env", lambda: FakeClient())

    send_build_status(
        "queued", "테스트 주제", "1234567890",
        position=3, total=5, source="browser",
    )
    send_build_status("running", "테스트 주제", "1234567890")
    send_build_status(
        "failed", "테스트 주제", "1234567890", error="Photoshop 연결 실패",
    )

    assert sent == [
        (
            "📥 카드뉴스 제작 대기열 등록\n\n테스트 주제\n요청: 브라우저\n"
            "대기 순서: 3번째 · 전체 5건\n작업 ID: 12345678\n\n"
            "제작이 시작되면 다시 알려드립니다. 브라우저에서도 같은 순번과 상태를 확인할 수 있습니다."
        ),
        (
            "⏳ 카드뉴스 제작 중\n\n테스트 주제\n요청: 웹 대시보드\n"
            "작업 ID: 12345678\n\n브라우저에서도 같은 작업 상태를 확인할 수 있습니다."
        ),
        (
            "❌ 카드뉴스 제작 실패\n\n테스트 주제\n작업 ID: 12345678\n\n"
            "원인: Photoshop 연결 실패"
        ),
    ]


def test_revision_controls_offer_edit_and_remake(monkeypatch):
    sent = []

    class FakeClient:
        def send(self, text, reply_markup=None):
            sent.append((text, reply_markup))
            return {"message_id": 1}

    monkeypatch.setattr("cardnews_auto.telegram_bot.client_from_env", lambda: FakeClient())
    send_revision_controls("테스트 주제", "foreign-hiphop", "a" * 32, "외국 힙합")

    buttons = sent[0][1]["inline_keyboard"][0]
    assert "수정 대상 매거진: 외국 힙합" in sent[0][0]
    assert "승인 뉴스 · 주제: 테스트 주제" in sent[0][0]
    assert "수정" in buttons[0]["text"]
    assert "재제작" in buttons[1]["text"]
    assert len(buttons[0]["callback_data"].encode()) <= 64


def test_numbered_revision_answer_parses_instruction_and_cover_title():
    instruction, cover, slide_number, slide_title, slide_body = _parse_revision_answer(
        "1. 수정할 카드 번호: 2\n"
        "2. 수정 지시사항: 인물 이미지를 더 크게 배치해주세요.\n"
        "3. 선택한 카드 제목: 핵심 수치가 먼저입니다\n"
        "4. 선택한 카드 본문: 본문 두 번째 장은 핵심 수치를 먼저 보여주세요.\n"
        "5. 참고 이미지: 브라우저에서 추가"
    )

    assert "인물 이미지를" in instruction
    assert cover == ""
    assert slide_number == 2
    assert slide_title == "핵심 수치가 먼저입니다"
    assert "본문 두 번째 장" in slide_body


def test_remake_callback_reports_the_returned_queue_position(monkeypatch, tmp_path):
    calls = []
    updates = [{
        "update_id": 1,
        "callback_query": {
            "id": "callback-1",
            "data": "remake:_:job-1",
            "message": {"chat": {"id": 123}},
        },
    }]

    class FakeClient:
        chat_id = "123"

        def call(self, method, payload=None, timeout=30):
            if method == "getUpdates":
                if updates:
                    return [updates.pop()]
                raise StopIteration
            calls.append((method, payload))
            return True

    monkeypatch.setattr("cardnews_auto.telegram_bot.client_from_env", lambda: FakeClient())
    revisions = []

    with pytest.raises(StopIteration):
        listen_forever(
            tmp_path / "workflow.json",
            tmp_path / "offset.json",
            on_revision=lambda *args: revisions.append(args) or {
                "build_job": {"queue_position": 3},
            },
        )

    assert revisions == [("job-1", "", "", 0, "", "")]
    answer = next(payload for method, payload in calls if method == "answerCallbackQuery")
    assert "3번째" in answer["text"]
    assert not answer.get("show_alert")
