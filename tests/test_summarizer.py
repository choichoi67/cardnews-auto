from types import SimpleNamespace

from cardnews_auto.models import Article
from cardnews_auto import summarizer
from cardnews_auto.summarizer import KoreanSummary, KoreanSummaryBatch


def test_summarize_preserves_original_title(monkeypatch):
    articles = [Article(title="New Album Announced", url="https://example.com")]
    parsed = KoreanSummaryBatch(items=[KoreanSummary(
            title_ko="새 앨범 발표",
            interpretation_ko="아티스트가 새 앨범의 발매 계획을 공개했습니다.",
            summary_ko="새 앨범이 발표됐다.",
    )])
    client = SimpleNamespace(responses=SimpleNamespace(
        parse=lambda **kwargs: SimpleNamespace(output_parsed=parsed),
    ))
    monkeypatch.setattr(summarizer, "OpenAI", lambda: client)

    result = summarizer.summarize_articles_in_korean(articles, "test-model")
    assert result[0].title == "새 앨범 발표"
    assert result[0].original_title == "New Album Announced"
    assert result[0].summary == "새 앨범이 발표됐다."
    assert result[0].interpretation == "아티스트가 새 앨범의 발매 계획을 공개했습니다."
