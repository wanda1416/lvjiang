"""场景选择下拉框 helper - 供编辑弹窗做跨场景迁移使用"""

from PyQt6.QtWidgets import QFormLayout, QComboBox

from ....core.scene_registry import get_registry


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
