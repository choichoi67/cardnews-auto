"""Pick each slide's PSD spec from the current channel's approved templates."""
from copy import deepcopy

from .models import CardNews


def plan_layout(card: CardNews, template_cfg: dict) -> tuple[list[dict], str]:
    """Cover first, body after, and an explicit final CTA kept as the fixed brand design."""
    # ponytail: approved_template() keeps one template per role, so the first variant always wins.
    # Bring back a scorer or AI picker only if a channel approves several templates for one role.
    variants = template_cfg.get("template_variants") or {}
    last = len(card.slides) - 1
    specs = []
    for index, slide in enumerate(card.slides):
        role = "cover" if index == 0 else "cta" if index == last and slide.role == "cta" else "body"
        spec = deepcopy((variants.get(role) or [template_cfg[role]])[0])
        if role == "cta":
            # Keep the fixed brand CTA; never repeat the cover photograph.
            spec["preserve_original"] = True
            for key in ("layers", "text_options", "static_values", "reuse_cover_image",
                        "image_layer", "image_layers", "image_fit", "image_focus_by_sha256"):
                spec.pop(key, None)
        specs.append(spec)
    return specs, "deterministic"
