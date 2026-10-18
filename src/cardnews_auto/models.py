from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr
from pydantic.json_schema import SkipJsonSchema


JOB_STATES = {
    "queued": dict(label="대기", active=True, retryable=False, message=(
        "📥 카드뉴스 제작 대기열 등록\n\n{topic}\n요청: {source}\n대기 순서: {position} · 전체 {total}건\n"
        "작업 ID: {job_id}\n\n제작이 시작되면 다시 알려드립니다. 브라우저에서도 같은 순번과 상태를 확인할 수 있습니다."
    )),
    "running": dict(label="진행 중", active=True, retryable=False, message=(
        "⏳ 카드뉴스 제작 중\n\n{topic}\n요청: {source}\n작업 ID: {job_id}\n\n"
        "브라우저에서도 같은 작업 상태를 확인할 수 있습니다."
    )),
    "completed": dict(label="완료", active=False, retryable=False),
    "failed": dict(label="실패", active=False, retryable=True, message=(
        "❌ 카드뉴스 제작 실패\n\n{topic}\n작업 ID: {job_id}\n\n원인: {error}"
    )),
    "interrupted": dict(label="중단", active=False, retryable=True),
}


class Article(BaseModel):
    title: str
    original_title: str = ""
    url: str
    image_url: str = ""
    summary: str = ""
    interpretation: str = ""
    source: str = ""
    published: str = ""


class Slide(BaseModel):
    _image_search_attempted: bool = PrivateAttr(default=False)
    _image_recovery_queries: list[str] = PrivateAttr(default_factory=list)
    role: Literal["cover", "body", "cta"] = "body"
    title: str
    body: str
    eyebrow: str = ""
    accent_color: str = ""  # used only when the cover template's "AI 배지 색" switch is on
    image_query: str = ""
    image_prompt: str = ""
    image_kind: SkipJsonSchema[Literal["photo", "evidence", "data"]] = "photo"
    image_url: str = ""
    image_page_url: str = ""
    image_provider: str = ""
    # Filled by the searcher, never requested from the writing model's strict schema.
    image_candidates: SkipJsonSchema[list[dict[str, str]]] = Field(default_factory=list)
    image_search_focus: SkipJsonSchema[str] = ""


class CardNews(BaseModel):
    topic: str
    rationale: str
    source_interpretation: str = ""
    editorial_summary: str = ""
    body_draft: str = ""
    source_urls: list[str]
    slides: list[Slide]
    caption: str
    hashtags: list[str]
