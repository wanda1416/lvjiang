"""yysls 装备品阶的主题扩展。"""

from __future__ import annotations


def equipment_quality_stylesheet(tokens) -> str:
    dark = bool(getattr(tokens, "is_dark", False))
    colors = (
        {"gold": "#e5a93b", "purple": "#c084fc", "blue": "#38bdf8", "green": "#34d399"}
        if dark else
        {"gold": "#b45309", "purple": "#7e22ce", "blue": "#0369a1", "green": "#15803d"}
    )
    return "\n".join([
        f'QLabel[equipmentName="true"] {{ color: {tokens.text}; }}',
        *(
            f'QLabel[equipmentName="true"][quality="{quality}"] {{ color: {color}; }}'
            for quality, color in colors.items()
        ),
    ])
