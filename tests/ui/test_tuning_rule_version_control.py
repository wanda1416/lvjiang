"""调律规则 key 行的显式版本提升控件。"""

from PyQt6.QtCore import Qt

from lvjiang.apps.yysls.ui.tune_settings.rule_settings_page import (
    RuleSettingsPage,
)
from lvjiang.core.config.resolver import EntityOrigin


def _rule_data() -> dict:
    return {
        "content_version": 2,
        "key": "demo",
        "name": "测试规则",
        "playstyles": {},
        "transmute_priority": [],
        "affix_pool": [],
        "patterns": {},
        "default_rating": "excellent",
    }


def test_bump_button_applies_immediately_and_refreshes_version(qtbot):
    calls = []

    def bump() -> int:
        calls.append(True)
        return 3

    page = RuleSettingsPage(
        _rule_data(), lambda: None,
        version_origin=EntityOrigin("remote", 2),
        on_bump_version=bump,
    )
    qtbot.addWidget(page)

    assert page._version_label.text() == "v2"
    assert "#D97706" in page._version_label.styleSheet()
    assert page._btn_bump_version.text() == "提升"

    qtbot.mouseClick(page._btn_bump_version, Qt.MouseButton.LeftButton)

    assert calls == [True]
    assert page._version_label.text() == "v3"
    assert "palette(mid)" in page._version_label.styleSheet()


def test_bump_button_is_disabled_outside_developer_mode(qtbot):
    page = RuleSettingsPage(
        _rule_data(), lambda: None,
        version_origin=EntityOrigin("local", 2),
    )
    qtbot.addWidget(page)

    assert not page._btn_bump_version.isEnabled()
    assert "palette(highlight)" in page._version_label.styleSheet()


# ─── key 行的排布 ────────────────────────────────────────

def _row_items(page):
    """key 行的条目序列：控件返回控件本身，弹簧返回其宽度"""
    row = page._key_row
    out = []
    for i in range(row.count()):
        item = row.itemAt(i)
        w = item.widget()
        out.append(w if w is not None else item.sizeHint().width())
    return out


def test_rename_sits_next_to_key_and_version_is_separated(qtbot):
    """「重命名」改的就是 key，得挨着它；版本号是配置代次，隔开别让人
    读成「key 的版本」"""
    page = RuleSettingsPage(
        _rule_data(), lambda: None,
        version_origin=EntityOrigin("system", 2),
        version_origins=(EntityOrigin("system", 2),),
    )
    qtbot.addWidget(page)
    items = _row_items(page)
    i_key = items.index(page._key_label)
    i_rename = items.index(page._btn_rename_key)
    i_ver = items.index(page._version_title)

    assert i_rename == i_key + 1, "重命名必须紧跟 key，中间不许插东西"
    assert i_ver > i_rename, "版本号排在 key/重命名之后"
    # 中间要有实打实的间隔（弹簧 + 竖线），不能贴着
    gap = sum(x for x in items[i_rename + 1:i_ver] if isinstance(x, int))
    assert gap >= 24, f"版本号离 key 只隔了 {gap}px，太近"
    assert page._version_label in items[i_ver:]
    assert page._btn_bump_version in items[i_ver:]


def test_version_tooltip_on_title_and_value(qtbot):
    """多数人把鼠标停在「规则版本：」几个字上，只给数值挂说明等于没做"""
    page = RuleSettingsPage(
        _rule_data(), lambda: None,
        version_origin=EntityOrigin("remote", 2),
        version_origins=(
            EntityOrigin("remote", 2), EntityOrigin("system", 1)),
    )
    qtbot.addWidget(page)
    tip = page._version_label.toolTip()
    assert "当前生效：远程 · v2" in tip
    assert "现有版本：2 份，来自 2 个来源" in tip
    assert "系统 · v1" in tip
    assert "/" not in tip
    assert page._version_title.toolTip() == tip
