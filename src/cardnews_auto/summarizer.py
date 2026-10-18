import json
import os

from openai import OpenAI
from pydantic import BaseModel

from .models import Article


class KoreanSummary(BaseModel):
    title_ko: str
    interpretation_ko: str = ""
    summary_ko: str


class KoreanSummaryBatch(BaseModel):
    items: list[KoreanSummary]


def summarize_articles_in_korean(articles: list[Article], configured_model: str) -> list[Article]:
    if not articles:
        return articles
    model = os.environ.get("OPENAI_MODEL", configured_model)
    payload = [
        {
            "title": article.title,
            "source": article.source,
            "published": article.published,
            "description": article.summary,
        }
        for article in articles
    ]
    response = OpenAI().responses.parse(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "당신은 Lead Content Editor이자 Senior Data Curator다. 제공된 사실만 사용한다. "
                    "독자는 원문을 전혀 모른다고 가정한다. 날짜와 시간순서, 인물·조직·회사명, 직접 인용과 "
                    "핵심 발언, 정확한 수치를 절대로 일반화하거나 생략하지 않는다. "
                    "각 사실을 원인·배경(Cause), 구체적인 핵심 사건(Event), 결과·영향(Impact)의 "
                    "인과 사슬로 먼저 해석한다. 사건만 고립해 나열하지 말고 원인이나 영향과 연결한다. "
                    "interpretation_ko에는 주체·시제·고유명사·수치·발언·인과관계를 빠뜨리지 않은 "
                    "자연스러운 한국어 해석을 적는다. summary_ko에는 군더더기만 제거하고 핵심 배경, "
                    "수치, 이유, 결과를 보존한 2~4문장 편집 요약을 적는다. 각 항목은 새로운 정보를 "
                    "전진시키며 앞 문장을 표현만 바꿔 반복하지 않는다. 주어와 서술어를 자연스럽게 "
                    "호응시키고 '~을 올랐다', '~을 남겼다', '~로 알려졌다', '~에 의해 보여진다'와 "
                    "'귀추가 주목된다', '신호탄을 쏘았다', '관심이 쏠립니다', '화제입니다', "
                    "'새로운 지평을 열었다'를 쓰지 않는다. '~하며', '~고', '~면서'로 문장을 억지로 "
                    "늘이지 않는다. 인명, 앨범명, 곡명은 통용 표기를 사용하며 추측하지 않는다."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        text_format=KoreanSummaryBatch,
    )
    result = response.output_parsed
    if result is None or len(result.items) != len(articles):
        raise RuntimeError("한국어 번역·요약 결과를 만들지 못했습니다.")
    return [
        article.model_copy(update={
            "original_title": article.original_title or article.title,
            "title": item.title_ko.strip(),
            "interpretation": item.interpretation_ko.strip(),
            "summary": item.summary_ko.strip(),
        })
        for article, item in zip(articles, result.items, strict=True)
    ]
