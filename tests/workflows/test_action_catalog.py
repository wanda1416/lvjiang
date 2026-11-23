"""指令面板（action_catalog）与 DSF 文法的一致性

面板生成的不是给人看的文本，而是会原样插进 .wf 的源码：槽位值写错就会产出
一段「插进去就解析失败」的脚本。这里用两条契约兜住：

1. 槽位渲染对明显非法的值（内部双引号、非 #rrggbb 颜色、非数字坐标）必须抛
   RenderError，而不是拼出一段坏代码；
2. 全目录按合法样例渲染后，每一条都必须能通过 parse_text —— 目录与文法
   任意一侧漂移都会在这里炸掉。
"""

import pytest

from lvjiang.workflows.action_catalog import (
    ACTIONS,
    RenderError,
    Slot,
    render,
    render_slot,
)
from lvjiang.workflows.grammar import parse_text

#: 每种槽位一个合法样例；新增槽位类型时这里也必须补，否则全量渲染会因缺值失败。
#: 坐标是归一化比例（文法只接受 [0, 1] 内的值），不是像素。
SAMPLES = {
    "scene": "main",
    "region": "ok",
    "coord": "(0.5, 0.25)",
    "rect": "(0.1, 0.2, 0.3, 0.4)",
    "color": "#ff8800",
    "delay": "@short",
    "template": "ok",
    "text": "示例文本",
    "raw": "$v",
    "var": "$v",
    "choice": "",
}


def _fill(action) -> dict[str, str]:
    """按「最小可用」填槽：有默认值用默认值，可选槽留空，其余按类型取样。

    可选槽留空正是用户只填必填项时的产物，也是可选包装最容易写错的分支。
    """
    values = {}
    for slot in action.slots:
        if slot.default:
            values[slot.key] = slot.default
        elif slot.optional:
            values[slot.key] = ""
        else:
            values[slot.key] = SAMPLES[slot.kind]
    return values


def test_every_action_renders_into_parsable_dsl():
    for action in ACTIONS:
        code = render(action, _fill(action))
        parse_text(code + "\n")


def test_action_keys_are_unique():
    """重复 key 会静默覆盖：面板少一条、取到的是后一条。"""
    keys = [a.key for a in ACTIONS]
    assert len(set(keys)) == len(keys)


class TestSlotValidation:
    def test_text_rejects_inner_double_quote(self):
        slot = Slot("msg", "内容", "text")
        with pytest.raises(RenderError):
            render_slot(slot, 'boss "level 3" ready')

    def test_text_accepts_chinese_and_single_quote(self):
        slot = Slot("msg", "内容", "text")
        assert render_slot(slot, "他说：'好' 的了") == "\"他说：'好' 的了\""

    def test_color_requires_hex(self):
        slot = Slot("color", "颜色", "color")
        assert render_slot(slot, "#ff8800") == '"#ff8800"'
        with pytest.raises(RenderError):
            render_slot(slot, "#GGGGGG")

    def test_coord_requires_numbers(self):
        slot = Slot("coord", "坐标", "coord")
        assert render_slot(slot, "(1.5, -2)") == "(1.5, -2)"
        with pytest.raises(RenderError):
            render_slot(slot, "(a, b)")

    def test_delay_requires_name(self):
        slot = Slot("wait", "等待", "delay")
        assert render_slot(slot, "short") == "@short"
        with pytest.raises(RenderError):
            render_slot(slot, "short wait x")
