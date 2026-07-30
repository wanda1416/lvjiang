"""场景选择下拉框 helper - 供编辑弹窗做跨场景迁移、视图归属使用"""

from PyQt6.QtWidgets import QComboBox, QFormLayout

from ....core.scene_loader import BASE_VIEW_KEY
from ....core.scene_registry import get_registry, get_scene_views


def add_scene_combo_row(form: QFormLayout, current_scene_key: str) -> QComboBox:
    """向表单添加「场景」下拉框行，返回该下拉框

    条目显示 `场景名 (key)`，userData 存 scene_key，默认选中当前场景。
    """
    combo = QComboBox()
    registry = get_registry()
    for key, scene in registry.all_scenes().items():
        combo.addItem(f"{scene.name} ({key})", userData=key)
        if key == current_scene_key:
            combo.setCurrentIndex(combo.count() - 1)
    form.addRow("场景:", combo)
    return combo


def add_view_combo_row(
    form: QFormLayout, scene_key: str, selected_view: str
) -> QComboBox | None:
    """多视图场景才添加「视图」下拉框行，否则返回 None

    条目显示视图名，userData 存 view key（基底视图为 BASE_VIEW_KEY）。
    selected_view 空视为基底。
    """
    views = get_scene_views(scene_key)
    if not views:
        return None
    combo = QComboBox()
    for v in views:
        combo.addItem(v.name, userData=v.key)
        is_base = v.key == BASE_VIEW_KEY
        if v.key == selected_view or (not selected_view and is_base):
            combo.setCurrentIndex(combo.count() - 1)
    form.addRow("视图:", combo)
    return combo


def combo_view_value(combo: QComboBox | None, fallback: str) -> str:
    """从视图下拉框取归属视图 key（基底归一化为空串），无下拉框时用 fallback"""
    if combo is None:
        view = fallback
    else:
        view = combo.currentData() or ""
    return "" if view == BASE_VIEW_KEY else view
