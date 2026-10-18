from .usage_meter import metered_call
import json
import logging
import os
import re

from openai import OpenAI

from .models import Article, CardNews
from .build_progress import progress
from .template_publication import wrap_text


logger = logging.getLogger(__name__)


DEFAULT_BODY_PROMPT = """[카드별 요약·퇴고 규칙]
승인 기사에 있는 핵심 사실을 자연스러운 한국어 카드뉴스로 편집합니다.
1. source_interpretation에 사실·인명·수치·인과관계를 정리하고 editorial_summary에 핵심 소식을 요약합니다.
2. 먼저 서로 다른 핵심 사실별로 본문 카드 구성을 정합니다. 같은 내용을 말만 바꾸어 반복하지 않습니다.
3. 각 본문은 제목 없이 body만 사용합니다. title과 eyebrow는 빈 문자열입니다.
4. body는 실제 PSD 폭에 맞는 2~3줄 분량의 완결된 문장입니다. 2줄짜리 PSD에는 2줄만 작성합니다.
5. 긴 문장을 여러 카드로 잘라 보내지 말고, 각 카드만 읽어도 주체와 사건이 이해되도록 간결하게 퇴고합니다.
6. 핵심 사실과 수치를 왜곡하지 않습니다. 부가 설명은 캡션에 남기며 모든 문장을 카드에 억지로 옮기지 않습니다.
7. body_draft에는 최종 본문 문단을 순서대로 담습니다. 마지막 카드 배치까지 확인하고 미완성 문장과 중복 내용을 고칩니다.
8. 카드별 본문에 맞는 인물·장소·행동·대상을 image_query로 작성합니다. 기사 제목이나 이전 검색어를 복제하지 않습니다.
9. 표지는 기사 대표 이미지와 title 및 PSD에 매핑된 배지·보조 문구를 표시합니다. 두 번째부터 마지막 장까지 모두 뉴스 본문과 해당 내용의 이미지입니다. CTA·팔로우·댓글 유도 카드를 만들지 않습니다."""

DEFAULT_COVER_PROMPT = """정보성 매거진의 표지 헤드라인을 정확히 2줄로 작성합니다.
slides[0].title에는 줄바꿈 문자를 정확히 한 번 넣어 두 줄을 직접 구분합니다. 한 줄 또는 세 줄 이상은 허용하지 않습니다.
최우선 목표는 ‘한국 독자가 1초 안에 이해하는 명확성’과 ‘다음 장을 넘기게 만드는 강한 후킹’입니다. 두 목표의 우선순위는 같습니다.
원문을 모르는 독자도 표지만 보고 핵심 인물·사건·변화를 설명할 수 있어야 합니다.
입력된 사실에서 현재성·구체성·대중적 관심도·사건의 명확성이 가장 높은 사실 하나를 중심으로 선택합니다.
첫 줄에는 핵심 인물·작품·브랜드·기관 등 주체와 현재 맥락을 제시합니다.
둘째 줄에는 예상 밖의 발표·공개·협업·반전·수치·결과 중 가장 강한 사실을 전달합니다.
두 줄은 분리된 카피가 아니라 하나의 기사 제목처럼 자연스럽게 이어져야 합니다.
핵심 고유명사는 가능한 앞에 배치하고, 의미 단위와 고유명사를 중간에서 끊지 않습니다.
‘누가 무엇을 했는지’가 빠진 분위기형 문구는 금지합니다. 질문형보다 간결하고 단정한 사실 전달형을 우선합니다.
‘충격·대박·난리’처럼 내용 없는 자극어는 쓰지 않습니다. 대신 실제 발언, 금액, 날짜, 관계, 반전처럼 사실 자체가 강한 대목을 전면에 둡니다.
독자가 이미 아는 배경을 전제로 하지 말고, 해외 업계 용어나 관계는 익숙한 한국어로 바꿉니다.
두 줄에서 같은 내용을 바꾸어 반복하거나 감탄사·과도한 수식어를 사용하지 않습니다.
근거 없는 숫자·날짜·인용·관계·결과를 추가하지 않습니다.
내부적으로 관점이 다른 후보 5개를 만들고 각각 이해도 40점·후킹 40점·사실 정확성 20점으로 평가합니다.
이해도나 후킹 중 하나라도 약한 후보는 버리고, 두 조건을 동시에 만족하는 하나만 최종 표지 제목으로 사용합니다."""


def clean_caption(value: str) -> str:
    value = re.sub(r"\s*\(\s*\[[^\]]+]\(https?://[^)\s]+\)\s*\)", "", value)
    value = re.sub(r"\[([^\]]+)]\(https?://[^)\s]+\)", r"\1", value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"(?im)^\s*(?:출처|source)\s*[:：].*(?:\r?\n|$)", "", value)
    value = value.replace("**", "")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _normalize_editorial_cards(card: CardNews, options: dict) -> None:
    """Validate the final editorial units; never paginate a sentence into cloned cards."""
    card.slides[0].role = "cover"
    seen_bodies, seen_queries = set(), set()
    for number, slide in enumerate(card.slides[1:], 2):
        slide.role = "body"
        slide.title = slide.eyebrow = ""
        text = " ".join(slide.body.split())
        if not re.search(r'[.!?。！？][”’\"\']*$', text):
            raise RuntimeError(f"{number}번 본문을 완결된 문장으로 다시 편집하세요. 연결어에서 끊지 마세요.")
        identity = re.sub(r"\W+", "", text).casefold()
        if identity in seen_bodies:
            raise RuntimeError(f"{number}번 본문이 앞 카드와 같습니다. 다른 핵심 사실을 배정하세요.")
        seen_bodies.add(identity)
        lines = wrap_text(text, {**options, "strict_korean_wrap": True}).split("\r")
        if len(lines) == 1:
            words = text.split()
            splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
            if splits:
                lines = list(min(splits, key=lambda pair: abs(len(pair[0]) - len(pair[1]))))
        if not 2 <= len(lines) <= options["max_lines"]:
            raise RuntimeError(f"{number}번 본문을 2~{options['max_lines']}줄의 완결된 내용으로 다시 편집하세요.")
        slide.body = "\n".join(lines)
        query = re.sub(r"\W+", "", slide.image_query).casefold()
        if not query or query in seen_queries:
            # Search can interpret Korean prose; use this card's facts instead of cloning a query.
            slide.image_query = text
            query = identity
        seen_queries.add(query)
        slide.image_prompt = f"이 카드의 내용을 설명하는 이미지: {text}\n장면 검색어: {slide.image_query}"


def _cover_headline_error(title: str) -> str:
    """Reject obvious unfinished connectors, not valid headline nouns outside a vocabulary list."""
    ending = title.strip().rstrip(".!?。！？… ").split()
    if ending and ending[-1] in {"위한", "통한", "대한", "향한", "그리고", "하지만", "그러나", "때문에", "대해", "관해"}:
        return "표지 제목이 연결 표현에서 끝났습니다. 기사에 있는 사실로 제목을 완결하세요."
    return ""


def _normalize_cover_title(value: str, options: dict) -> str:
    wrap_options = {**options, "max_lines": 2, "preserve_manual_lines": True}
    wrapped = wrap_text(value, wrap_options).replace("\r", "\n")
    lines = [line.strip() for line in wrapped.splitlines() if line.strip()]
    if len(lines) == 1:
        words = lines[0].split()
        line_chars = int(options.get("line_chars") or 0)
        candidates = [
            (" ".join(words[:index]), " ".join(words[index:]))
            for index in range(1, len(words))
        ]
        if line_chars:
            candidates = [pair for pair in candidates if max(map(len, pair)) <= line_chars]
        if candidates:
            lines = list(min(candidates, key=lambda pair: abs(len(pair[0]) - len(pair[1]))))
    if len(lines) != 2:
        raise RuntimeError(f"표지 제목은 정확히 2줄이어야 합니다. 실제 {len(lines)}줄")
    return "\n".join(lines)


def _title_options(
    cfg: dict, role: str, default_chars: int, default_lines: int, field: str = "title",
) -> dict:
    template = cfg.get("template", {})
    variants = template.get("template_variants") or {}
    # Uploaded channel templates are authoritative.  The legacy base template
    # is only a fallback and must not impose another channel's capacities.
    specs = variants.get(role, []) or [template.get(role, {})]
    options = [
        spec.get("text_options", {}).get(field, {})
        for spec in specs
        if spec.get("text_options", {}).get(field)
    ]
    max_chars = min(
        (int(item["max_chars"]) for item in options if int(item.get("max_chars", 0)) > 0),
        default=default_chars,
    )
    max_lines = min(
        (int(item["max_lines"]) for item in options if int(item.get("max_lines", 0)) > 0),
        default=default_lines,
    )
    line_chars = min(
        (
            int(item.get("line_chars") or max(1, int(item.get("max_chars", max_chars)) // max_lines))
            for item in options
        ),
        default=max(1, max_chars // max_lines),
    )
    return {"max_chars": max_chars, "max_lines": max_lines, "line_chars": line_chars}


def create_cardnews(articles: list[Article], cfg: dict) -> CardNews:
    if not articles:
        raise RuntimeError("조건에 맞는 뉴스 후보가 없습니다. RSS/키워드/조회 시간을 확인하세요.")
    client = OpenAI()
    generation = cfg["generation"]
    brand = cfg["brand"]
    news = cfg["news"]
    cover_title_options = _title_options(cfg, "cover", 26, 2)
    template_cfg = cfg.get("template", {})
    cover_specs = (template_cfg.get("template_variants") or {}).get("cover") or [template_cfg.get("cover", {})]
    cover_fields = {field for spec in cover_specs for field in spec.get("layers", {})}
    cover_aux_options = {field: _title_options(cfg, "cover", chars, 1, field)
                         for field, chars in (("eyebrow", 6), ("body", 18)) if field in cover_fields}
    cover_aux_rule = (
        "표지에는 title 외에 PSD에 매핑된 보조 문구를 작성한다. "
        "eyebrow는 짧은 한국어 뉴스 분류 배지, body는 인물·작품 또는 핵심 보충정보 한 줄이다. "
        "기사 사실에 근거하고 제목을 그대로 반복하지 않는다. "
        f"반드시 채울 필드와 PSD 제한: {cover_aux_options}. 매핑되지 않은 보조 필드는 빈 문자열로 둔다."
    )
    ai_accent = any(spec.get("ai_accent") for spec in cover_specs)
    accent_rule = (
        "slides[0].accent_color에는 기사의 중심 팀·인물·브랜드를 대표하는 색(예: 팀 엠블럼·유니폼의 주 색)을 #RRGGBB로 쓴다. "
        "확실하지 않으면 빈 문자열로 둔다. 다른 슬라이드의 accent_color는 빈 문자열이다."
        if ai_accent else "모든 슬라이드의 accent_color는 빈 문자열로 둔다. 색상은 템플릿 디자인을 따른다."
    )
    body_specs = [template_cfg.get("body", {})]
    body_specs.extend((template_cfg.get("template_variants") or {}).get("body", []))
    body_text_options = (
        _title_options(cfg, "body", 96, 4, "body")
        if any(spec.get("text_options", {}).get("body") for spec in body_specs)
        else {"max_chars": 72, "line_chars": 24, "max_lines": 3}
    )
    body_text_options["max_lines"] = min(3, body_text_options["max_lines"])
    body_text_options["max_chars"] = min(body_text_options["max_chars"], body_text_options["line_chars"] * body_text_options["max_lines"])
    candidates = "\n".join(
        f"[{i}] {a.title}\n출처: {a.source}\nURL: {a.url}\n"
        f"1차 한국어 해석: {a.interpretation or a.summary}\nRSS 요약: {a.summary}"
        for i, a in enumerate(articles, 1)
    )
    configured_count = generation.get("slide_count", "auto")
    caption_prompt = str(generation.get("caption_prompt", "")).strip()
    configured_body_prompt = str(generation.get("body_prompt", "")).strip()
    configured_cover_prompt = str(generation.get("cover_prompt", "")).strip()
    # Old saved defaults must not reintroduce the former unlimited-draft rules.
    legacy_end = "- IF NO: FAIL. Shorten and rewrite the quotation passage before returning JSON."
    if configured_body_prompt.startswith("[ROLE & SYSTEM GOAL]") and legacy_end in configured_body_prompt:
        configured_body_prompt = configured_body_prompt.split(legacy_end, 1)[1].strip()
    if configured_cover_prompt.startswith(DEFAULT_COVER_PROMPT):
        configured_cover_prompt = configured_cover_prompt[len(DEFAULT_COVER_PROMPT):].strip()
    body_prompt = DEFAULT_BODY_PROMPT
    cover_prompt = DEFAULT_COVER_PROMPT
    if configured_body_prompt and configured_body_prompt != DEFAULT_BODY_PROMPT:
        body_prompt += f"\n\n[채널 추가 규칙]\n{configured_body_prompt}"
    if configured_cover_prompt and configured_cover_prompt != DEFAULT_COVER_PROMPT:
        cover_prompt += f"\n\n[채널 추가 규칙]\n{configured_cover_prompt}"
    if configured_count == "auto" or configured_count == 0:
        min_slides = int(generation.get("min_slides", 4))
        max_slides = int(generation.get("max_slides", 12))
        slide_instruction = (
            f"기사 정보량에 맞춰 총 {min_slides}~{max_slides}장 사이에서 필요한 장수를 스스로 선택하라. "
            "내용을 억지로 늘리거나 중요한 내용을 줄이지 마라."
        )
    else:
        expected = int(configured_count)
        min_slides = max_slides = expected
        slide_instruction = f"정확히 {expected}장의 카드 원고를 작성하라."
    prompt = f"""
당신은 인스타그램 매거진 편집장이다.
카테고리: {news['category']}
브랜드: {brand['name']} ({brand['handle']})
톤: {brand['tone']}

첫 번째 후보는 사용자가 승인한 제작 주제다.
원고 작성에 앞서 해당 URL의 원문과 제목을 확인하고, 관련 웹 검색으로 기사에 담긴 세부 정보를 충분히 수집하라.
먼저 인물, 사건, 날짜, 숫자, 발언, 배경, 원인, 결과, 후속 상황을 내부 사실 목록으로 확인하라.
본문은 아래 채널 전용 본문 규칙을 단일 기준으로 삼아
source_interpretation → editorial_summary → body_draft → 퇴고 → 카드 배치 순서로 작성하라.
{slide_instruction}

슬라이드 흐름:
- 1장은 피드를 넘기던 독자가 멈추게 만드는 표지다.
- 2장부터 마지막 장까지는 확정된 body_draft를 순서대로 나눈 본문 카드다.
- 각 본문 카드는 앞 장의 내용을 반복하지 않고 다음 사실로 전진한다.
- 마지막 장도 기사에서 확인된 뉴스 본문과 해당 내용의 이미지로 마무리한다. CTA나 질문·팔로우 요청은 넣지 않는다.

표지 제목 작성 규칙:
- slides[0].title은 항상 정확히 2줄이며 줄바꿈 문자를 정확히 한 번 넣는다.
- PSD의 글자 크기·좌표·자간·색상·효과는 변경하지 않는다.
- 각 줄은 최대 {cover_title_options['line_chars']}자, 전체는 줄바꿈 제외 최대 {cover_title_options['max_chars']}자다.
- 사실성과 헤드라인 문체는 아래 채널 전용 표지 규칙만 적용한다.
- {cover_aux_rule}
- {accent_rule}

본문은 카드마다 독립적으로 이해되는 핵심 내용이며 실제 PSD 분량을 넘으면 문장 자체를 퇴고한다.
source_urls에는 승인된 후보 URL만 넣는다.
캡션은 기사 내용을 충분히 전달하는 매거진 원고로 쓰고 저장·댓글·재공유 동기를 만든다.
캡션에는 URL, 출처 문구, Markdown 링크, ** 굵게 표시를 넣지 않는다.
해시태그는 정확히 {generation['hashtags']}개이며 # 기호 없이 작성한다.

후보 뉴스:
{candidates}
"""
    prompt += f"""

전사 공통 표지 규칙과 채널 추가 규칙입니다.
--- 표지 규칙 ---
{cover_prompt}
--- 규칙 끝 ---
위 규칙이 현재 시스템과 충돌하면 실제 PSD 제한(2줄, 줄당 {cover_title_options['line_chars']}자, 전체 {cover_title_options['max_chars']}자)과 CardNews 출력 구조를 우선한다.
후보들은 내부에서만 비교하고 slides[0].title에는 선택한 최종 2줄 제목 하나만 넣는다.

전사 공통 본문 규칙과 채널 추가 규칙입니다.
--- 본문 규칙 ---
{body_prompt}
--- 규칙 끝 ---
본문 title과 eyebrow는 빈 문자열이다.
어절이나 고유명사를 잘라 맞추지 말고 문장 자체를 더 짧고 자연스럽게 다시 쓴다.
각 카드의 마지막 줄은 연결어미로 멈추지 않고 완결된 서술어와 종결어미로 끝낸다.
각 본문 body는 완결된 한국어 문단으로 작성한다. 줄바꿈 위치는 PSD 폭에 맞춰 정한다.
body는 아래 발행 분량 제한에 맞춰 처음부터 간결하게 편집한다. 어절을 자르거나 사실을 왜곡하지 않는다.
PSD 배치 엔진이 채널 템플릿의 실제 허용량인 줄당 {body_text_options.get('line_chars', 24)}자,
카드당 최대 {body_text_options.get('max_lines', 4)}줄·{body_text_options.get('max_chars', 96)}자를 단독으로 적용하고,
추가 분할로 총 장수를 넘기지 않도록 반복 표현을 줄이고 부가 설명은 캡션에 보존한다.

이미지 지시:
- 마지막 장을 포함한 모든 슬라이드마다 image_query를 작성한다. 표지는 기사 대표 이미지를 우선한다.
- image_query는 Google/Baidu 이미지 검색에서 쓸 수 있도록 인물명, 작품명, 사건, 장소 등
  검증 가능한 고유명사를 포함한 짧고 구체적인 검색어로 작성한다.
- 슬라이드 문구는 해당 image_query의 장면과 직접 관련되어야 한다.
- image_prompt에는 해당 본문에 맞는 시각 자료를 설명한다. 실제 기사 사진을 사용할 때 사실관계와 인물 정체성을 바꾸지 않는다.
"""
    if caption_prompt:
        prompt += (
            "\n\n채널 전용 캡션 작성 규칙입니다. 일반 캡션 규칙보다 우선합니다. "
            "제목 표기와 문단 순서는 지키되 URL, 출처 문구, Markdown 링크, ** 표시는 사용하지 마세요.\n"
            "--- 채널 캡션 규칙 ---\n"
            f"{caption_prompt}\n"
            "--- 규칙 끝 ---"
        )
    system_prompt = (
        "당신은 기사 취재 내용을 정보 손실 없이 대중적인 매거진 원고로 재구성하는 한국어 에디터다. "
        "승인 기사와 원문을 먼저 충분히 파악하고 핵심 사실을 모두 보존한 다음, "
        "읽는 맛과 공유 동기가 생기는 순서와 문장으로 편집하라."
    )
    request = {
        "model": os.environ.get("OPENAI_WRITER_MODEL") or os.environ.get("OPENAI_MODEL", generation["model"]),
        "text_format": CardNews,
    }
    if generation.get("research_enabled", False):
        request["tools"] = [{"type": "web_search"}]
    body_budget = max(1, max_slides - 1)
    safe_chars = body_text_options["max_chars"]
    size_rule = (
        f"\n[전체 발행 분량 제한] 표지 1장과 본문을 포함해 총 {min_slides}~{max_slides}장. "
        f"본문은 최대 {body_budget}장이고 각 body는 공백 포함 {safe_chars}자 이하를 목표로 작성한다. "
        f"본문 PSD의 실제 제한: {body_text_options}. "
        "내용을 각 카드에 고르게 배분하고 핵심 사실·수치·인명·인과관계는 유지하되 반복 설명과 장황한 표현을 줄인다. "
        "한 카드에 긴 원고를 몰아넣고 시스템의 추가 분할에 맡기지 않는다."
    )
    editorial_rule = (
        "\n[최우선 발행 형식 — 위 채널 지침과 충돌하면 이 규칙을 우선한다]\n"
        f"{cover_aux_rule} "
        "2장부터 마지막 장까지 role은 body, title과 eyebrow는 빈 문자열이며 body만 표시한다. "
        f"본문은 카드당 2~{body_text_options['max_lines']}줄, 줄당 {body_text_options['line_chars']}자에 맞는 완결된 문장이다. "
        "핵심 사실별로 카드 구성을 먼저 정하고 각 카드에 들어갈 분량으로 요약·퇴고한다. "
        "긴 원고를 기계적으로 잘라 다음 카드에 이어 붙이지 않는다. 부가 설명은 캡션에 보존한다. "
        "모든 본문은 마침표로 끝나는 완결된 문장이며 서로 다른 내용을 전한다. "
        "카드별 본문을 확정한 뒤 그 내용에 맞는 image_query를 각각 새로 작성한다. "
        "기사 제목이나 앞 카드 검색어를 복제하지 말고 해당 카드의 인물·장소·행동·대상을 구체화한다. "
        "전체 카드의 검색어를 함께 비교하고 의미가 비슷하면 단순 동의어 교체가 아니라 기사에 근거한 다른 촬영 장소·대상·장면으로 나눈다. "
        "image_prompt에도 해당 본문의 시각적 설명을 쓴다. 마지막 장도 뉴스 본문이며 CTA는 생성하지 않는다.\n"
    )
    prompt += size_rule + editorial_rule

    def normalize_layout(card: CardNews) -> str:
        try:
            card.slides[0].title = _normalize_cover_title(card.slides[0].title, cover_title_options)
            _normalize_editorial_cards(card, body_text_options)
            cover = card.slides[0]
            for field in ("eyebrow", "body"):
                if field not in cover_aux_options:
                    setattr(cover, field, "")
                    continue
                value = " ".join(getattr(cover, field).split())
                if not value:
                    raise RuntimeError(f"표지 {field} 보조 문구를 PSD 제한 안에서 작성하세요.")
                setattr(cover, field, wrap_text(value, {**cover_aux_options[field], "strict_korean_wrap": True}).replace("\r", "\n"))
            if generation.get("cover_unique_labels", False):
                eyebrow = re.sub(r"[\W_]+", "", cover.eyebrow)
                title = re.sub(r"[\W_]+", "", cover.title)
                if eyebrow and eyebrow in title:
                    raise RuntimeError(
                        "표지 상단 설명이 메인 제목과 중복됩니다. 서로 다른 정보를 쓰세요."
                    )
            if generation.get("cover_headline_requires_action", False):
                headline_error = _cover_headline_error(cover.title)
                if headline_error:
                    raise RuntimeError(headline_error)
        except RuntimeError as exc:
            return str(exc)
        if not min_slides <= len(card.slides) <= max_slides:
            return f"카드가 {len(card.slides)}장입니다. {min_slides}~{max_slides}장 안에서 핵심 사실을 보존하며 문장을 간결하게 편집하세요."
        return ""

    generated_slide_count = 0
    for attempt in range(2):
        detail = f"{request['model']} · " + ("기사 확인 및 초안 작성" if attempt == 0 else "PSD 분량에 맞춰 원고 재편집 (2/2)")
        progress("원고 작성", detail=detail)
        request["input"] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        response = metered_call("원고 초안" if attempt == 0 else "원고 재편집", "openai", request["model"], client.responses.parse, **request)
        result = response.output_parsed
        if result is None:
            raise RuntimeError(f"AI가 구조화된 결과를 만들지 못했습니다: {json.dumps(response.model_dump(), ensure_ascii=False)[:500]}")
        generated_slide_count = len(result.slides)
        progress("원고 작성", detail=f"{generated_slide_count}장 초안 · PSD 줄 수와 문장 검사")
        layout_error = normalize_layout(result)
        if not layout_error:
            break
        print(f"[원고 검수 {attempt + 1}/2] {layout_error}", flush=True)
        request.pop("tools", None)
        prompt = (
            "웹 검색이나 새 사실 추가 없이 아래 CardNews JSON의 핵심 사실을 유지하되 표현은 간결하게 고치고 "
            f"배치 오류만 수정하라.\n오류: {layout_error}\n"
            f"표지 title은 2줄·전체 {cover_title_options['max_chars']}자다. "
            f"본문 body의 실제 PSD 제한은 {body_text_options}이다. 긴 문장은 사실을 보존하며 짧은 문장으로 나눠라. "
            "오류가 있는 카드의 문장을 짧게 퇴고하고 해당 내용에 맞춰 이미지 검색어도 수정하라.\n"
            + size_rule + editorial_rule + f"\n직전 CardNews JSON:\n{result.model_dump_json()}"
        )
    else:
        raise RuntimeError(layout_error)
    if not min_slides <= len(result.slides) <= max_slides:
        expected_text = str(min_slides) if min_slides == max_slides else f"{min_slides}~{max_slides}"
        raise RuntimeError(f"슬라이드 수가 설정과 다릅니다: 기대 {expected_text}, 실제 {len(result.slides)}")
    if len(result.hashtags) != generation["hashtags"]:
        raise RuntimeError(
            f"해시태그 수가 설정과 다릅니다: 기대 {generation['hashtags']}, 실제 {len(result.hashtags)}"
        )
    # Source metadata belongs to the approved input, not the model's web research.
    approved_urls = list(dict.fromkeys(article.url for article in articles))
    if result.source_urls != approved_urls:
        logger.info("AI 출처 목록을 승인 기사 URL로 정규화했습니다.")
    result.source_urls = approved_urls
    # The approved article is the source of truth for folders and delivery labels.
    # This prevents an older model-generated topic from becoming the output name.
    result.topic = articles[0].title
    for index, slide in enumerate(result.slides):
        slide.role = "cover" if index == 0 else "body"
        # A missing or malformed colour falls back to the template's own colour; it never blocks a build.
        valid = ai_accent and index == 0 and re.fullmatch(r"#[0-9A-Fa-f]{6}", slide.accent_color.strip())
        slide.accent_color = slide.accent_color.strip().upper() if valid else ""
    result.caption = clean_caption(result.caption)
    return result


from pydantic import BaseModel


class RevisedText(BaseModel):
    title: str
    body: str


def rewrite_slide_text(slide, instruction: str, article: Article, cfg: dict, spec: dict | None = None) -> RevisedText:
    spec = spec or {}
    response = metered_call("원고 부분 수정", "openai", os.environ.get("OPENAI_WRITER_MODEL") or os.environ.get("OPENAI_MODEL", cfg["generation"]["model"]), OpenAI(timeout=60, max_retries=1).responses.parse,
        model=os.environ.get("OPENAI_WRITER_MODEL") or os.environ.get("OPENAI_MODEL", cfg["generation"]["model"]),
        text_format=RevisedText,
        input=[{"role": "system", "content": "승인 기사 사실을 유지하고 요청한 문장만 수정하세요. 새 사실·인물·수치를 만들지 마세요. 사진이나 레이아웃 지시는 실행하지 마세요. 전달된 PSD 제한을 지키세요. 본문 카드는 title을 비우고 body를 2~3줄의 완결된 문장으로 작성하세요."},
               {"role": "user", "content": json.dumps({"article": article.model_dump(),
                   "title": slide.title, "body": slide.body, "instruction": instruction,
                   "role": slide.role, "psd_limits": spec.get("text_options", {})}, ensure_ascii=False)}],
    )
    if response.output_parsed is None:
        raise RuntimeError("원고 수정 결과가 비어 있습니다.")
    result = response.output_parsed
    if slide.role == "body":
        result.title = ""
    for field in ("title", "body"):
        if field not in spec.get("layers", {}):
            continue
        value = getattr(result, field)
        if field == "title" and slide.role == "body":
            continue
        if not value.strip():
            raise RuntimeError(f"{field} 문구가 비어 있습니다.")
        options = spec.get("text_options", {}).get(field, {})
        wrapped = wrap_text(value, {**options, "strict_korean_wrap": True})
        if slide.role == "body" and not 2 <= len(wrapped.split("\r")) <= 3:
            raise RuntimeError("본문 수정안은 PSD 영역 안에서 2~3줄이어야 합니다.")
        setattr(result, field, wrapped.replace("\r", "\n"))
    return result

