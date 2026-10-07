"""Profile key 的三级选择按钮（类型 → 分组 → 定义）。

key 一多，平铺的下拉框根本看不完也选不动。类型和分组都是定义侧**已有**的组织
方式，所以这里跟着用同一套，不另造一种归类。

用户总览的「替换当前列」和批量配置的「指定排序」复用此单选控件。
批量新增列使用多选窗口，但沿用相同的类型和定义分组。
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QMenu, QPushButton

from ...core.profile.models import (
    ALL_MODELS,
    DEFAULT_KEY_GROUP,
    MODEL_LABELS,
    group_key_definitions,
)
from ...i18n import tr
from ..button_styles import apply_button_style


def profile_key_label(config, key: str) -> str:
    """`标签 (key)`；定义已不存在时退回裸 key，让用户看出是哪条失效了。"""
    if not key:
        return ""
    definition = config.get_key(key)
    if definition:
        return f"{definition.label} ({definition.key})"
    return key


def create_profile_key_picker(
    config,
    all_keys: list,
    current_key: str,
    on_choose: Callable[[str], None],
    *,
    empty_label: str = "",
    placeholder: str = "",
    minimum_width: int = 200,
) -> QPushButton:
    """按 类型 → 分组 → 定义 建出级联菜单的选择按钮。

    ``empty_label`` 非空时，菜单顶部多一项"不限定"，选它回传空 key——批量的
    「指定排序」需要能取消，用户总览的替换列不需要。

    只有一个分组且就是默认分组时不再套那一层：它只会多一次点击，不提供信息。
    """
    def _text_for(key: str) -> str:
        if key:
            return profile_key_label(config, key)
        return empty_label or placeholder or tr("（请选择）")

    button = QPushButton(_text_for(current_key))
    button.setMinimumWidth(minimum_width)
    apply_button_style(button, variant="neutral")

    def _choose(key: str) -> None:
        button.setText(_text_for(key))
        button.setToolTip(button.text())
        on_choose(key)

    def _add_key_actions(target_menu: QMenu, definitions: list) -> None:
        for definition in definitions:
            action = target_menu.addAction(
                f"{definition.label} ({definition.key})")
            if action is not None:
                action.triggered.connect(
                    lambda _checked, key=definition.key: _choose(key))

    def build_menu() -> QMenu:
        """与 exec 分开：exec 会阻塞，菜单结构只有这样才能被断言。"""
        menu = QMenu(button)
        if empty_label:
            clear_action = menu.addAction(empty_label)
            if clear_action is not None:
                clear_action.triggered.connect(lambda _checked: _choose(""))
            menu.addSeparator()
        keys_by_model: dict[str, list] = {}
        for definition in all_keys:
            model_type = config.get_model_type(definition.key) or ""
            keys_by_model.setdefault(model_type, []).append(definition)
        for model_type in ALL_MODELS:
            definitions = keys_by_model.get(model_type, [])
            if not definitions:
                continue
            submenu = menu.addMenu(MODEL_LABELS.get(model_type, model_type))
            if submenu is None:
                continue
            grouped = group_key_definitions(definitions)
            if len(grouped) == 1 and DEFAULT_KEY_GROUP in grouped:
                _add_key_actions(submenu, grouped[DEFAULT_KEY_GROUP])
                continue
            for group_name, group_definitions in grouped.items():
                label = (tr("默认") if group_name == DEFAULT_KEY_GROUP
                         else group_name)
                group_menu = submenu.addMenu(label)
                if group_menu is not None:
                    _add_key_actions(group_menu, group_definitions)
        return menu

    button.clicked.connect(
        lambda: build_menu().exec(
            button.mapToGlobal(button.rect().bottomLeft())))
    button.setToolTip(button.text())
    button.build_key_menu = build_menu  # type: ignore[attr-defined]
    return button
