"""日常页脚本名显示测试。"""

from lvjiang.ui.run_control import _compact_workflow_name


def test_compact_workflow_name_keeps_short_name():
    assert _compact_workflow_name("八字以内脚本") == "八字以内脚本"


def test_compact_workflow_name_elides_after_eight_characters():
    assert _compact_workflow_name("一二三四五六七八九十") == "一二三四五六七八..."
