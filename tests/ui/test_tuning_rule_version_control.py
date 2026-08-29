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
