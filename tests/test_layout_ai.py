from cardnews_auto import layout_ai
from cardnews_auto.models import CardNews, Slide


def test_layout_uses_the_single_approved_template_per_role():
    card = CardNews(
        topic="topic",
        rationale="reason",
        source_urls=[],
        slides=[
            Slide(role="cover", title="표지 제목", body="예고"),
            Slide(role="body", title="본문 제목", body="본문입니다."),
            Slide(role="cta", title="", body=""),
        ],
        caption="caption",
        hashtags=[],
    )
    template = {
        role: {"renderer": "preview", "name": role}
        for role in ("cover", "body", "cta")
    }
    template["template_variants"] = {
        role: [{"renderer": "preview", "name": role}]
        for role in ("cover", "body", "cta")
    }

    specs, source = layout_ai.plan_layout(card, template)

    assert source == "deterministic"
    assert [spec["name"] for spec in specs] == ["cover", "body", "cta"]


def test_cta_keeps_brand_design_without_reusing_cover_image():
    card = CardNews(
        topic="topic",
        rationale="reason",
        source_urls=[],
        slides=[
            Slide(role="cover", title="cover", body=""),
            Slide(role="cta", title="", body=""),
        ],
        caption="",
        hashtags=[],
    )
    template = {
        "cover": {"renderer": "preview"},
        "cta": {
            "renderer": "photoshop",
            "preserve_original": True,
            "reuse_cover_image": True,
            "image_layer": "CTA 사진",
            "layers": {"title": "수정 금지"},
        },
    }

    specs, _ = layout_ai.plan_layout(card, template)

    assert "image_layer" not in specs[-1]
    assert "reuse_cover_image" not in specs[-1]
    assert "layers" not in specs[-1]
