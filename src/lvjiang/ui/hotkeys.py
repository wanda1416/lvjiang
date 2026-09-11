"""只读实例的快捷键展示策略。"""

from ..core.access import is_readonly


def hotkey_label(label: str, key: str) -> str:
    """主实例显示快捷键；只读实例只显示动作名称。"""
    return label if is_readonly() else f"{label} ({key})"


def hotkey_status(base: str, *items: tuple[str, str]) -> str:
    """构造状态栏热键提示；只读实例不暴露任何快捷键。"""
    if is_readonly():
        return base
    return " | ".join([base, *(f"{key} {label}" for key, label in items)])
