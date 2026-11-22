"""yysls 业务语义色单一来源（基于通用 ThemeTokens 派生）。"""

from __future__ import annotations

from ....i18n import tr
from ....ui.widgets.theme import ThemeTokens, current_tokens

_QUALITY_ORDER = ("gold", "purple", "blue", "green")
_QUALITY_DARK = {
    "gold": "#e5a93b",
    "purple": "#c084fc",
    "blue": "#38bdf8",
    "green": "#34d399",
}
_QUALITY_LIGHT = {
    "gold": "#b45309",
    "purple": "#7e22ce",
    "blue": "#0369a1",
    "green": "#15803d",
}
_QUALITY_BG_DARK = {
    "gold": "rgba(229, 169, 59, 0.16)",
    "purple": "rgba(192, 132, 252, 0.16)",
}
_QUALITY_BG_LIGHT = {
    "gold": "rgba(210, 179, 102, 0.25)",
    "purple": "rgba(113, 102, 120, 0.25)",
}
_RESULT_BADGE_DARK = {
    "recycled": "#b91c3c",
    "skipped": "#524f49",
    "tuned": "#166534",
    "tuned_recycled": "#b45309",
    "reset": "#0e5a8a",
}
_RESULT_BADGE_LIGHT = {
    "recycled": "#be123c",
    "skipped": "#635f56",
    "tuned": "#15803d",
    "tuned_recycled": "#c2410c",
    "reset": "#0369a1",
}
_TAG_DARK = {
    "mock": "#7e22ce",
    "loadout": "#0f766e",
    "lock": "#b91c3c",
    "filtered": "#524f49",
}
_TAG_LIGHT = {
    "mock": "#7e57c2",
    "loadout": "#00897b",
    "lock": "#b45353",
    "filtered": "#607d8b",
}


def quality_color(
    quality: str,
    tokens: ThemeTokens | None = None,
    *,
    default: str | None = None,
) -> str:
    """Return equipment quality foreground/accent colour for the active theme."""
    t = tokens or current_tokens()
    palette = _QUALITY_DARK if t.is_dark else _QUALITY_LIGHT
    return palette.get(quality, t.text if default is None else default)


def quality_bg(quality: str, tokens: ThemeTokens | None = None) -> str | None:
    """Return subtle translucent card background for gold/purple equipment."""
    t = tokens or current_tokens()
    palette = _QUALITY_BG_DARK if t.is_dark else _QUALITY_BG_LIGHT
    return palette.get(quality)


def quality_border(
    has_quality_bg: bool,
    tokens: ThemeTokens | None = None,
) -> str:
    """Return normal border colour for equipment cards."""
    if not has_quality_bg:
        return "palette(midlight)"
    t = tokens or current_tokens()
    return "#6b5a34" if t.is_dark else "#b0a080"


def affix_cap_color(
    cap_pct: int | float | None,
    tokens: ThemeTokens | None = None,
) -> str:
    """Return affix value colour tiered by cap percentage (>=90 gold, >=70 purple)."""
    if cap_pct is None:
        return "palette(text)"
    t = tokens or current_tokens()
    if cap_pct >= 90:
        return quality_color("gold", t)
    if cap_pct >= 70:
        return quality_color("purple", t)
    return quality_color("blue", t)


def rating_style(
    rating: str,
    tokens: ThemeTokens | None = None,
    default_label: str | None = None,
) -> tuple[str, str]:
    """Return (display_label, inline_css_style) for a tuning rating key."""
    t = tokens or current_tokens()
    styles = {
        "top": (tr("顶级"), f"color:{t.accent}; font-weight: bold"),
        "excellent": (tr("优秀"), f"color:{t.success}"),
        "normal": (tr("能用"), f"color:{t.text_muted}"),
        "junk": (tr("垃圾"), f"color:{t.danger}"),
    }
    if rating in styles:
        return styles[rating]
    fallback = default_label if default_label is not None else rating
    return fallback, f"color:{t.text}"


def tuning_result_badge_color(
    result_key: str,
    tokens: ThemeTokens | None = None,
) -> str:
    """Return background colour for tuning result status pills."""
    t = tokens or current_tokens()
    palette = _RESULT_BADGE_DARK if t.is_dark else _RESULT_BADGE_LIGHT
    return palette.get(result_key, t.text_muted)


def transmute_target_color(tokens: ThemeTokens | None = None) -> str:
    """Return readable amber/yellow colour for transmute target affixes."""
    t = tokens or current_tokens()
    return "#ffd54f" if t.is_dark else "#b45309"


def yellow_stat_color(tokens: ThemeTokens | None = None) -> str:
    """Return accent/warning colour for yellow combat stats and cooldown notices."""
    return (tokens or current_tokens()).warning


def status_tag_color(kind: str, tokens: ThemeTokens | None = None) -> str:
    """Return background colour for compact status badges (mock/loadout/lock/illegal)."""
    t = tokens or current_tokens()
    if kind == "illegal":
        return t.danger
    palette = _TAG_DARK if t.is_dark else _TAG_LIGHT
    return palette.get(kind, t.text_muted)


def equipment_quality_stylesheet(tokens: ThemeTokens) -> str:
    """Build QSS rules for equipment name labels from the unified quality palette."""
    return "\n".join([
        f'QLabel[equipmentName="true"] {{ color: {tokens.text}; }}',
        *(
            f'QLabel[equipmentName="true"][quality="{quality}"] '
            f"{{ color: {quality_color(quality, tokens)}; }}"
            for quality in _QUALITY_ORDER
        ),
    ])
