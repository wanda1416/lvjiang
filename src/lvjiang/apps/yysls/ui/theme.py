"""yysls 装备品阶的主题扩展。"""

from __future__ import annotations


def equipment_quality_stylesheet(tokens) -> str:
    dark = tokens.window.lower() == "#202327"
    colors = (
        {"gold": "#ffd166", "purple": "#c4a0ff", "blue": "#73a7ff", "green": "#62d58a"}
        if dark else
        {"gold": "#b8860b", "purple": "#8b5cf6", "blue": "#2563eb", "green": "#16863e"}
    )
    return "\n".join([
        f'QLabel[equipmentName="true"] {{ color: {tokens.text}; }}',
        *(
            f'QLabel[equipmentName="true"][quality="{quality}"] {{ color: {color}; }}'
            for quality, color in colors.items()
        ),
    ])
