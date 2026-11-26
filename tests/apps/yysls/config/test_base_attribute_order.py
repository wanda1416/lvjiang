"""基础属性候选的展示顺序：预置 > 当前用户名前缀匹配 > 其他用户。

用户和方案一多，「当前属性」下拉框会很长，但用户实际关心的是预置与自己名下的条目。
顺序错乱不是崩溃而是"每次都要翻半天"，所以这里钉住可观察的列表顺序，并确认别人的
条目没有被过滤掉。
"""
from __future__ import annotations

from lvjiang.apps.yysls.config.base_attributes import (
    list_all_base_attributes,
    order_play_style_names,
)


def _play_styles(**kwargs):
    return kwargs


def test_current_user_entries_come_before_other_users() -> None:
    names = ["张三·会心", "宛元芷·奶", "李四·大外", "宛元芷2", "王大锤"]
    ordered = order_play_style_names(names, "宛元芷")

    mine = [name for name in ordered if name.startswith("宛元芷")]
    others = [name for name in ordered if not name.startswith("宛元芷")]
    # 自己的条目整段在前，别人的整段在后，且一个都没少
    assert ordered == mine + others
    assert set(ordered) == set(names)
    # 组内按名称排，顺序可重复
    assert mine == sorted(mine)
    assert others == sorted(others)


def test_order_only_sorts_never_filters() -> None:
    names = ["张三·会心", "宛元芷·奶"]
    ordered = order_play_style_names(names, "宛元芷")
    assert sorted(ordered) == sorted(names)


def test_no_user_keeps_alphabetical_order() -> None:
    """没有激活用户时退化为按名称排，顺序仍然确定。"""
    assert order_play_style_names(["b", "c", "a"], "") == ["a", "b", "c"]
    assert order_play_style_names(["b", "a"], None) == ["a", "b"]


def test_prefix_match_is_not_substring_match() -> None:
    """只认前缀：名字中间出现当前用户名的不算"自己的"。"""
    ordered = order_play_style_names(["别人的宛元芷", "宛元芷1"], "宛元芷")
    assert ordered[0] == "宛元芷1"


def test_list_all_base_attributes_puts_presets_first(monkeypatch) -> None:
    monkeypatch.setattr(
        "lvjiang.apps.yysls.config.play_styles.get_play_styles",
        lambda school: _play_styles(**{
            "张三·会心": {"min_outer": 1.0},
            "宛元芷·奶": {"min_outer": 2.0},
        }),
    )
    values = list_all_base_attributes("鸣金·虹", user_name="宛元芷")
    keys = list(values)

    assert keys[0].startswith("preset:"), keys[:1]
    personal = [key for key in keys if not key.startswith("preset:")]
    assert personal == ["宛元芷·奶", "张三·会心"]
    assert values["宛元芷·奶"] == {"min_outer": 2.0}
