"""Card image rule: real scene photo first, AI reconstruction otherwise."""
import json

from PIL import Image

from cardnews_auto import render
from cardnews_auto.models import CardNews, Slide
from cardnews_auto.scene_check import reconstruction_prompt

OPTIONS = {"enabled": True, "recompose_for_publication": True, "scene_check": True, "max_parallel": 1}


def run(monkeypatch, tmp_path, verdicts):
    photos = {}
    for name, color in (("cover", "navy"), ("scene", "green"), ("portrait", "red")):
        photos[name] = tmp_path / f"{name}.png"
        Image.new("RGB", (1080, 1440), color).save(photos[name])
    monkeypatch.setattr(render, "_download_cover_image", lambda url, _cache: photos.get(url))
    monkeypatch.setattr(render, "_transform_image_with_fallback", lambda path, options, cache: path)
    monkeypatch.setattr(render, "judge_photo", lambda path, slide, title, model: verdicts[path.stem])
    made = {}
    def reconstruct(slide, title, reference, options, cache, label):
        made["reference"] = reference
        target = tmp_path / "reconstructed.png"
        Image.new("RGB", (1080, 1440), "yellow").save(target)
        return target
    monkeypatch.setattr(render, "_reconstruct_card_image", reconstruct)
    card = CardNews(topic="t", rationale="", source_urls=[], caption="캡션", hashtags=[], slides=[
        Slide(role="cover", title="표지", body="", image_url="cover", image_provider="rss_article"),
        Slide(role="body", title="", body="그는 우승 트로피를 들어 올렸다.",
              image_candidates=[{"url": "portrait"}, {"url": "scene"}])])
    template = {role: {"renderer": "auto"} for role in ("cover", "body", "cta")}
    render.render_hybrid(card, template, tmp_path / "out", image_transform=OPTIONS)
    audit = json.loads((tmp_path / "out" / "image_selection.json").read_text(encoding="utf-8"))
    return card, made, audit


def test_scene_photo_is_preferred_over_subject_photo(monkeypatch, tmp_path):
    card, made, audit = run(monkeypatch, tmp_path, {"portrait": "subject", "scene": "scene", "cover": "unrelated"})
    assert card.slides[1].image_url == "scene" and not made  # the cover hero is not re-judged
    assert {"slide": 2, "url": "portrait", "status": "not_scene:subject"} in audit


def test_no_scene_photo_reconstructs_with_subject_reference_and_labels_it(monkeypatch, tmp_path):
    card, made, audit = run(monkeypatch, tmp_path, {"portrait": "subject", "scene": "unrelated", "cover": "scene"})
    assert made["reference"].stem == "portrait"
    assert card.slides[1].image_provider == "ai_reconstruction"
    assert "AI 재현 이미지: 2번 카드" in card.caption


def test_sensitive_card_reconstruction_never_uses_a_real_face():
    slide = Slide(role="body", title="", body="그는 음주 운전 혐의로 체포됐다.")
    prompt, uses_reference = reconstruction_prompt(slide, "기사", with_reference=True)
    assert not uses_reference and "Do not show identifiable real people" in prompt
    prompt, uses_reference = reconstruction_prompt(Slide(role="body", title="", body="그는 트로피를 들었다."), "기사", True)
    assert uses_reference and "Keep their identity" in prompt
