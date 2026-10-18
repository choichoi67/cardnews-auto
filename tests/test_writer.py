from types import SimpleNamespace
import pytest

from cardnews_auto import writer
from cardnews_auto.models import Article, CardNews, Slide


def editorial_card():
    return CardNews(topic="새 앨범", rationale="", source_urls=[], caption="", hashtags=[], slides=[
        Slide(role="cover", title="가수의\n새 앨범", body="보조 설명", eyebrow="뉴스"),
        Slide(title="반복 제목", body="가수가 새 앨범을 발표했습니다.", image_query="artist recording studio"),
        Slide(title="반복 제목", body="공연 일정도 함께 공개했습니다.", image_query="artist live concert"),
        Slide(role="body", title="마지막 제목", body="새 작품에는 협업곡도 담겼습니다.", image_query="artist collaboration recording"),
    ])


def test_writer_restores_only_mapped_cover_auxiliary_fields(monkeypatch):
    value = editorial_card()
    captured = []
    def parse(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(output_parsed=value)
    monkeypatch.setattr(writer, "OpenAI", lambda: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    cfg = {"brand":{"name":"test", "handle":"test", "tone":"test"}, "news":{"category":"music"},
           "generation":{"model":"test", "slide_count":4, "hashtags":0},
           "template":{"cover":{"layers":{"title":"headline", "body":"subtitle", "eyebrow":"badge"},
                                    "text_options":{"body":{"max_chars":18,"max_lines":1},
                                                    "eyebrow":{"max_chars":6,"max_lines":1}}}}}
    result = writer.create_cardnews([Article(title="새 앨범",url="https://example.com")], cfg)
    assert result.slides[0].eyebrow == "뉴스"
    assert result.slides[0].body == "보조 설명"
    assert all(not s.title and not s.eyebrow for s in result.slides[1:])
    assert "반드시 채울 필드와 PSD 제한" in captured[0]["input"][1]["content"]


def test_cover_auxiliary_copy_and_body_only_cards_preserve_complete_content():
    card = editorial_card()
    original = [s.body for s in card.slides[1:]]
    writer._normalize_editorial_cards(card, {"max_chars": 72, "line_chars": 24, "max_lines": 3})
    assert card.slides[0].body == "보조 설명"
    assert card.slides[0].eyebrow == "뉴스"
    assert all(s.title == s.eyebrow == "" for s in card.slides[1:])
    assert [" ".join(s.body.split()) for s in card.slides[1:]] == original
    assert all(2 <= len(s.body.splitlines()) <= 3 for s in card.slides[1:])
    assert all(" ".join(s.body.split()) in s.image_prompt for s in card.slides[1:])
    assert card.slides[-1].title == ""
    assert all(s.role == "body" for s in card.slides[1:])
    assert len(card.slides) == 4


@pytest.mark.parametrize("kind", ["long", "unfinished", "duplicate_body"])
def test_bad_editorial_units_request_rewrite_without_creating_extra_cards(kind):
    card = editorial_card()
    if kind == "long":
        card.slides[1].body = "가수가 새로운 앨범의 제작 과정과 공연 일정에 관한 자세한 설명을 공개했습니다. " * 4
    elif kind == "unfinished":
        card.slides[1].body = "가수가 새로운 앨범을 발표하고"
    elif kind == "duplicate_body":
        card.slides[2].body = card.slides[1].body
    with pytest.raises(RuntimeError):
        writer._normalize_editorial_cards(card, {"max_chars": 72, "line_chars": 24, "max_lines": 3})
    assert len(card.slides) == 4


@pytest.mark.parametrize("query", ["", "ARTIST RECORDING STUDIO"])
def test_missing_or_cloned_queries_are_rebuilt_from_each_cards_body(query):
    card = editorial_card()
    card.slides[2].image_query = query
    body = card.slides[2].body
    writer._normalize_editorial_cards(card, {"max_chars": 72, "line_chars": 24, "max_lines": 3})
    assert card.slides[2].image_query == body
    assert card.slides[1].image_query != card.slides[2].image_query


def test_football_body_respects_two_line_template():
    card = editorial_card()
    writer._normalize_editorial_cards(card, {"max_chars": 32, "line_chars": 16, "max_lines": 2})
    assert all(len(s.body.splitlines()) == 2 for s in card.slides[1:])
    assert all(len(line) <= 16 for s in card.slides[1:] for line in s.body.splitlines())


def test_unused_body_titles_are_removed_without_second_api_call(monkeypatch):
    article = Article(title="승인 기사", url="https://example.com/news", source="Example")
    bad = CardNews(
        topic=article.title, rationale="test", source_urls=[article.url],
        slides=[
            Slide(title="승인 기사의\n핵심 변화", body="다음 장에서 이유를 확인합니다"),
            Slide(title="가나다라마바사아자차카타파하가나다라마바사", body=""),
            Slide(title="마지막 핵심 사실을\n짧게 정리했습니다.", body=""),
            Slide(role="body", title="", body="새 작품에는 협업곡도 담겼습니다.", image_query="artist collaboration"),
        ],
        caption="caption", hashtags=["news"],
    )
    bad.slides[1].body = "가수가 새 앨범을 발표했습니다."
    bad.slides[1].image_query = "artist album studio"
    bad.slides[2].body = "공연 일정도 함께 공개했습니다."
    bad.slides[2].image_query = "artist concert stage"

    good = bad.model_copy(deep=True)
    good.slides[1].title = "첫 번째 핵심 사실을\n짧게 전했습니다."
    calls = []

    class Responses:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_parsed=bad if len(calls) == 1 else good)

    monkeypatch.setattr(writer, "OpenAI", lambda: SimpleNamespace(responses=Responses()))
    cfg = {
        "brand": {"name": "테스트", "handle": "@test", "tone": "선명하게"},
        "news": {"category": "음악"},
        "generation": {
            "model": "test", "slide_count": 4, "hashtags": 1,
            "research_enabled": True,
        },
    }

    writer.create_cardnews([article], cfg)

    assert calls[0]["tools"] == [{"type": "web_search"}]
    assert len(calls) == 1


def test_caption_prompt_is_applied_and_approved_title_controls_topic(monkeypatch):
    captured = {}
    article = Article(title="현재 승인된 명곡 뉴스", url="https://example.com/current", source="Source")
    parsed = CardNews(
        topic="카이 세낫 이전 주제",
        rationale="test",
        source_urls=[article.url],
        slides=[
            Slide(role="cover", title="현재 승인된\n명곡 뉴스", body="본문"),
            Slide(title="첫 번째 본문이 자연스럽게 이어지는 내용입니다", body="첫 소식을 설명합니다.", image_query="artist recording studio"),
            Slide(title="두 번째 본문도 맥락을 이어서 설명합니다", body="내용"),
            Slide(role="body", title="", body="새 작품에는 협업곡도 담겼습니다.", image_query="artist collaboration"),
        ],
        caption="caption",
        hashtags=["one", "two"],
    )

    parsed.slides[1].body = "가수가 새 앨범을 발표했습니다."
    parsed.slides[1].image_query = "artist album studio"
    parsed.slides[2].body = "공연 일정도 함께 공개했습니다."
    parsed.slides[2].image_query = "artist concert stage"

    class Responses:
        def parse(self, **kwargs):
            captured["prompt"] = kwargs["input"][1]["content"]
            return SimpleNamespace(output_parsed=parsed)

    monkeypatch.setattr(writer, "OpenAI", lambda: SimpleNamespace(responses=Responses()))
    cfg = {
        "brand": {"name": "외힙명곡", "handle": "@test", "tone": "정확하고 간결하게"},
        "news": {"category": "외힙명곡"},
        "generation": {
            "model": "test-model",
            "slide_count": 4,
            "hashtags": 2,
            "caption_prompt": "**Song - [곡명]** 다음 줄에 **Artist - [아티스트]**",
            "body_prompt": " ",
            "cover_prompt": "",
        },
    }

    result = writer.create_cardnews([article], cfg)

    assert result.topic == article.title
    assert "채널 전용 캡션 작성 규칙" in captured["prompt"]
    assert "**Song - [곡명]**" in captured["prompt"]
    assert "첫 번째 후보는 사용자가 승인한 제작 주제" in captured["prompt"]
    assert writer.DEFAULT_BODY_PROMPT in captured["prompt"]
    assert writer.DEFAULT_COVER_PROMPT in captured["prompt"]


def test_prompt_preserves_factual_cover_and_edited_body_draft(monkeypatch):
    captured = {}
    monkeypatch.setenv("OPENAI_MODEL", "old-model")
    monkeypatch.setenv("OPENAI_WRITER_MODEL", "gpt-6-sol")
    article = Article(
        title="래퍼 A, 새 앨범 발매일 공개",
        url="https://example.com/album",
        summary="래퍼 A가 공식 계정에서 새 앨범 발매일을 공개했다.",
        source="Example",
    )
    parsed = CardNews(
        topic=article.title,
        rationale="test",
        source_urls=[article.url],
        slides=[
            Slide(title="발매일을\n공개했다", body="다음 장에서 이유가 드러납니다"),
            Slide(title="첫 번째 맥락을 자연스럽게 이어서 설명합니다", body="내용"),
            Slide(title="두 번째 맥락도 새로운 사실과 함께 전합니다", body="내용"),
            Slide(role="body", title="", body="새 작품에는 협업곡도 담겼습니다.", image_query="artist collaboration"),
        ],
        caption="caption",
        hashtags=["news"],
    )

    parsed.slides[1].body = "가수가 새 앨범을 발표했습니다."
    parsed.slides[1].image_query = "artist album studio"
    parsed.slides[2].body = "공연 일정도 함께 공개했습니다."
    parsed.slides[2].image_query = "artist concert stage"

    class Responses:
        def parse(self, **kwargs):
            captured["model"] = kwargs["model"]
            captured["system"] = kwargs["input"][0]["content"]
            captured["prompt"] = kwargs["input"][1]["content"]
            return SimpleNamespace(output_parsed=parsed)

    monkeypatch.setattr(writer, "OpenAI", lambda: SimpleNamespace(responses=Responses()))
    cfg = {
        "brand": {"name": "테스트", "handle": "@test", "tone": "빠르고 선명하게"},
        "news": {"category": "음악"},
        "generation": {
            "model": "test-model",
            "body_prompt": "[ROLE & SYSTEM GOAL] ZERO length restrictions\n- IF NO: FAIL. Shorten and rewrite the quotation passage before returning JSON.\n채널 고유 문체 유지",
            "slide_count": 4,
            "hashtags": 1,
        },
    }

    writer.create_cardnews([article], cfg)

    prompt = captured["prompt"]
    assert captured["model"] == "gpt-6-sol"
    assert "ZERO length restrictions" not in prompt
    assert "Do not count characters" not in prompt
    assert "채널 고유 문체 유지" in prompt
    assert "정보성 매거진의 표지 헤드라인" in prompt
    assert "근거 없는 숫자·날짜·인용·관계·결과를 추가하지 않습니다" in prompt
    assert "확정된 body_draft를 순서대로 나눈 본문 카드" in prompt
    assert "각 카드의 마지막 줄은 연결어미로 멈추지 않고" in prompt
    assert "PSD 배치 엔진이 채널 템플릿의 실제 허용량" in prompt
    assert "20–35 Korean characters per line" not in prompt
    assert "리그면 EPL·라리가·세리에A" not in prompt
    assert "핵심 팀 엠블럼의 대표 색상" not in prompt
    assert "2장은 사건이 왜 중요한지" not in prompt
    assert "3장은 기사에서 확인되는 가장 강한 사실" not in prompt
    assert "정보 손실 없이" in captured["system"]
    assert "핵심 사실을 모두 보존" in captured["system"]
    assert writer.DEFAULT_BODY_PROMPT in prompt
    assert writer.DEFAULT_COVER_PROMPT in prompt


def test_clean_caption_removes_links_sources_and_markdown_bold():
    caption = (
        "**✅ [뉴스] 드레이크의 새 소식**\n\n"
        "핵심 내용입니다. ([HotNewHipHop](https://example.com/news))\n"
        "출처: https://example.com/news\n"
        "**Artist - Drake**"
    )

    cleaned = writer.clean_caption(caption)

    assert cleaned == "✅ [뉴스] 드레이크의 새 소식\n\n핵심 내용입니다.\nArtist - Drake"
    assert "http" not in cleaned
    assert "**" not in cleaned


def test_cover_prompt_uses_psd_capacity_and_normalizes_title(monkeypatch):
    captured = {}
    article = Article(title="긴 승인 뉴스", url="https://example.com/news", source="Example")
    parsed = CardNews(
        topic=article.title,
        rationale="test",
        source_urls=[article.url],
        slides=[
            Slide(title="아이스-T가 꺼낸\n20년 만의 솔로 랩", body="다음 장에서 배경이 이어집니다"),
            Slide(title="첫 본문을 자연스럽게 이어서 충분히 작성합니다", body="내용"),
            Slide(title="다음 본문도 새로운 맥락을 이어서 설명합니다", body="내용"),
            Slide(role="body", title="", body="새 작품에는 협업곡도 담겼습니다.", image_query="artist collaboration"),
        ],
        caption="caption",
        hashtags=["news"],
    )

    parsed.slides[1].body = "가수가 새 앨범을 발표했습니다."
    parsed.slides[1].image_query = "artist album studio"
    parsed.slides[2].body = "공연 일정도 함께 공개했습니다."
    parsed.slides[2].image_query = "artist concert stage"

    class Responses:
        def parse(self, **kwargs):
            captured["prompt"] = kwargs["input"][1]["content"]
            return SimpleNamespace(output_parsed=parsed)

    monkeypatch.setattr(writer, "OpenAI", lambda: SimpleNamespace(responses=Responses()))
    cfg = {
        "brand": {"name": "테스트", "handle": "@test", "tone": "선명하게"},
        "news": {"category": "음악"},
        "generation": {"model": "test", "slide_count": 4, "hashtags": 1},
        "template": {
            "cover": {
                "text_options": {
                    "title": {"max_chars": 20, "line_chars": 10, "max_lines": 2, "allow_shrink": False},
                },
            },
        },
    }

    result = writer.create_cardnews([article], cfg)

    assert "각 줄은 최대 10자" in captured["prompt"]
    assert "전체는 줄바꿈 제외 최대 20자" in captured["prompt"]
    assert len(result.slides[0].title.split("\n")) == 2
    assert all(len(line) <= 10 for line in result.slides[0].title.split("\n"))


def test_channel_cover_prompt_is_applied_with_psd_override(monkeypatch):
    captured = {}
    article = Article(title="아이스-T 새 앨범", url="https://example.com/news", source="Example")
    parsed = CardNews(
        topic=article.title,
        rationale="test",
        source_urls=[article.url],
        slides=[
            Slide(title="아이스-T\n새 앨범 공개", body="다음 장에서 배경이 이어집니다"),
            Slide(title="첫 본문을 자연스럽게 이어서 충분히 작성합니다", body="내용"),
            Slide(title="다음 본문도 새로운 맥락을 이어서 설명합니다", body="내용"),
            Slide(role="body", title="", body="새 작품에는 협업곡도 담겼습니다.", image_query="artist collaboration"),
        ],
        caption="caption",
        hashtags=["news"],
    )

    parsed.slides[1].body = "가수가 새 앨범을 발표했습니다."
    parsed.slides[1].image_query = "artist album studio"
    parsed.slides[2].body = "공연 일정도 함께 공개했습니다."
    parsed.slides[2].image_query = "artist concert stage"

    class Responses:
        def parse(self, **kwargs):
            captured["prompt"] = kwargs["input"][1]["content"]
            return SimpleNamespace(output_parsed=parsed)

    monkeypatch.setattr(writer, "OpenAI", lambda: SimpleNamespace(responses=Responses()))
    cfg = {
        "brand": {"name": "테스트", "handle": "@test", "tone": "정보성"},
        "news": {"category": "음악"},
        "generation": {
            "model": "test",
            "slide_count": 4,
            "hashtags": 1,
            "cover_prompt": "첫 줄에는 핵심 주체, 둘째 줄에는 구체적인 사건을 씁니다.",
        },
        "template": {
            "cover": {
                "text_options": {
                    "title": {"max_chars": 20, "line_chars": 10, "max_lines": 2},
                },
            },
        },
    }

    writer.create_cardnews([article], cfg)

    assert "전사 공통 표지 규칙과 채널 추가 규칙" in captured["prompt"]
    assert "첫 줄에는 핵심 주체" in captured["prompt"]
    assert "실제 PSD 제한(2줄, 줄당 10자, 전체 20자)" in captured["prompt"]
    assert writer.DEFAULT_COVER_PROMPT in captured["prompt"]


def test_uploaded_channel_cover_capacity_replaces_legacy_base_capacity():
    cfg = {
        "template": {
            "cover": {"text_options": {"title": {"max_chars": 20, "line_chars": 10, "max_lines": 2}}},
            "template_variants": {
                "cover": [{
                    "text_options": {
                        "title": {"max_chars": 24, "line_chars": 12, "max_lines": 2},
                        "body": {"max_chars": 10, "line_chars": 10, "max_lines": 1},
                    },
                }],
            },
        },
    }

    assert writer._title_options(cfg, "cover", 26, 2) == {
        "max_chars": 24, "max_lines": 2, "line_chars": 12,
    }
    assert writer._title_options(cfg, "cover", 18, 1, "body")["max_chars"] == 10


def test_total_slide_overflow_requests_bounded_rewrite(monkeypatch):
    article = Article(title="승인 기사",url="https://example.com/news")
    def make(count):
        return CardNews(topic=article.title,rationale="",source_urls=[article.url],caption="",hashtags=["뉴스"],slides=[
            Slide(role="cover",title="승인 기사의\n핵심 변화",body="음악"),
            *[Slide(role="body",title="음악의 변화",body=f"{i}번째 소식을 설명합니다.", image_query=f"artist scene {i}") for i in range(count)],
            Slide(role="body",title="",body="새 작품에는 협업곡도 담겼습니다.",image_query="artist collaboration")])
    calls=[]
    def parse(**request):
        calls.append(request)
        return SimpleNamespace(output_parsed=make(11 if len(calls)==1 else 10))
    monkeypatch.setattr(writer,"OpenAI",lambda:SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    cfg={"brand":{"name":"테스트","handle":"@test","tone":"간결"},"news":{"category":"음악"},
         "generation":{"model":"test","slide_count":"auto","min_slides":4,"max_slides":12,"hashtags":1},
         "template":{"body":{"text_options":{"body":{"max_chars":96,"line_chars":24,"max_lines":4}}}}}
    result=writer.create_cardnews([article],cfg)
    assert len(result.slides)==12 and len(calls)==2
    retry=calls[1]["input"][1]["content"]
    assert "본문은 최대 11장" in retry and "72자 이하" in retry
    assert "표현과 사실을 그대로 유지" not in retry


def test_cover_headline_accepts_valid_football_actions_and_sentence_endings():
    for title in ["윌리엄스 최전방\n공격수 기용", "윌리엄스의\n포지션 전환", "문도 데포르티보\n오늘의 1면", "윌리엄스가\n최전방에 나선다.", "선발 명단\n공개!"]:
        assert writer._cover_headline_error(title) == ""
    for title in ["윌리엄스의\n활약을 위한", "최전방 기용에\n대한"]:
        assert writer._cover_headline_error(title)


def test_source_metadata_is_owned_by_approved_input(monkeypatch):
    article = Article(title="승인 기사",url="https://example.com/approved")
    cfg={"brand":{"name":"테스트","handle":"@test","tone":"간결"},"news":{"category":"음악"},
         "generation":{"model":"test","slide_count":4,"hashtags":1,"research_enabled":True}}
    for model_urls in [[], [article.url + "?utm_source=search"], ["https://other.example/research"], [article.url,article.url]]:
        parsed=CardNews(topic=article.title,rationale="",source_urls=model_urls,caption="",hashtags=["뉴스"],slides=[
            Slide(role="cover",title="승인 기사의\n핵심 변화",body="음악"),
            Slide(role="body",title="첫 번째 설명",body="기사 설명입니다.", image_query="artist studio"),
            Slide(role="body",title="두 번째 설명",body="후속 설명입니다.", image_query="artist concert"),
            Slide(role="body",title="",body="새 작품에는 협업곡도 담겼습니다.",image_query="artist collaboration")])
        before=[s.body for s in parsed.slides]
        calls=[]
        def parse(**request):
            calls.append(request)
            return SimpleNamespace(output_parsed=parsed)
        monkeypatch.setattr(writer,"OpenAI",lambda:SimpleNamespace(responses=SimpleNamespace(parse=parse)))
        result=writer.create_cardnews([article],cfg)
        assert result.source_urls == [article.url]
        assert [" ".join(s.body.split()) for s in result.slides[1:-1]] == before[1:-1]
        assert result.slides[0].body == ""
        assert len(calls)==1
