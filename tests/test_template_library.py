from cardnews_auto.layout_ai import plan_layout
from cardnews_auto.models import CardNews, Slide


def _card() -> CardNews:
    return CardNews(
        topic="topic",
        rationale="reason",
        source_urls=["https://example.com"],
        slides=[
            Slide(role="body", title="첫 장", body="표지"),
            Slide(role="cover", title="두 번째", body="짧은 본문"),
            Slide(role="cta", title="마지막", body="CTA"),
        ],
        caption="caption",
        hashtags=["tag"],
    )


def test_layout_enforces_cover_body_cta_positions_and_preserves_cta(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    template = {
        "cover": {"renderer": "preview", "name": "fallback-cover"},
        "body": {"renderer": "preview", "name": "fallback-body"},
        "cta": {"renderer": "preview", "name": "fallback-cta"},
        "template_variants": {
            "cover": [{"renderer": "photoshop", "name": "cover"}],
            "body": [{"renderer": "photoshop", "name": "body"}],
            "cta": [{
                "renderer": "photoshop",
                "name": "cta",
                "layers": {"title": "must-not-change"},
                "image_layer": "must-not-change",
            }],
        },
    }

    specs, source = plan_layout(_card(), template)

    assert source == "deterministic"
    assert [item["name"] for item in specs] == ["cover", "body", "cta"]
    assert specs[-1]["preserve_original"] is True
    assert "layers" not in specs[-1]
    assert "image_layer" not in specs[-1]
