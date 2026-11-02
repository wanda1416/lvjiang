"""自动剧情对话框顶部字段行的对齐契约。

「配置文件 / 匹配阈值 / 保存到」都是四个字，但每行是独立的 QHBoxLayout：
间距或标签宽度有任何差异，后面的输入框就会错开一格。这个用例锁住"两者
左边界一致"，避免以后给某一行单独调间距或换字号时又错开。
"""
from __future__ import annotations


def test_field_rows_align_input_left_edges(qtbot):
    from lvjiang.ui.story_dialog import StoryChoicesDialog

    dialog = StoryChoicesDialog(None)
    qtbot.addWidget(dialog)
    dialog.resize(1100, 720)
    dialog.show()

    # 让自动宽度下拉框先量完自身尺寸，再比较左右边界
    for _ in range(3):
        qtbot.wait(10)

    group_left = dialog._group.geometry().left()
    threshold_left = dialog._threshold.geometry().left()
    assert abs(group_left - threshold_left) <= 1, (
        f"配置文件下拉与匹配阈值输入框没对齐: {group_left} vs {threshold_left}"
    )
    # 标签统一宽度后，三处标签（含同行的「保存到」）宽度必须一致
    widths = {label.width() for label in (
        dialog._group_label, dialog._threshold_label, dialog._save_label)}
    assert len(widths) == 1, f"字段标签宽度不一致: {widths}"
