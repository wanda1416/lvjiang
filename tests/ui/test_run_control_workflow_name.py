"""日常页脚本下拉：宽度随分栏伸缩，长脚本名不再被预先截断。

此前脚本名在入列前就被截成 8 字（`一二三四五六七八...`），下拉框又是
setFixedWidth，于是把左侧分栏拉多宽都看不到更多字。现在放完整名字、
宽度只设下限——窄的时候由 Qt 按可用宽度自己 elide，拉宽即可完整显示。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QSizePolicy

from lvjiang.ui.main.window import _set_combo_character_capacity

LONG = "一个非常非常长的脚本名称用于验证宽度"


def _combo(qtbot) -> QComboBox:
    combo = QComboBox()
    qtbot.addWidget(combo)
    return combo


class TestExpandingCapacity:
    def test_expanding_sets_floor_not_fixed_width(self, qtbot):
        combo = _combo(qtbot)
        width = _set_combo_character_capacity(combo, 10, expanding=True)

        assert combo.minimumWidth() == width
        assert combo.maximumWidth() > width, "不能是固定宽，否则拉不开"
        assert (combo.sizePolicy().horizontalPolicy()
                is QSizePolicy.Policy.Expanding)

    def test_expanding_combo_actually_grows(self, qtbot):
        combo = _combo(qtbot)
        floor = _set_combo_character_capacity(combo, 10, expanding=True)

        combo.resize(floor + 400, combo.height())

        assert combo.width() == floor + 400

    def test_default_stays_fixed_width(self, qtbot):
        """其余下拉（环境/布局）维持原有固定宽，本次不改它们的行为。"""
        combo = _combo(qtbot)
        width = _set_combo_character_capacity(combo, 10)

        assert combo.minimumWidth() == width
        assert combo.maximumWidth() == width


def test_full_name_is_kept_in_item_text(qtbot):
    """入列的是完整名字——预先截断会让「拉宽」永远看不到更多内容。"""
    from lvjiang.ui.main import run_control

    assert not hasattr(run_control, "_compact_workflow_name"), (
        "截断函数已移除，脚本名应完整入列")

    combo = _combo(qtbot)
    combo.addItem(LONG, "demo")
    assert combo.itemText(0) == LONG
