"""UI 主题系统与热切换回归测试（离屏）。"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


def test_apply_themes_updates_palette_and_tokens(qtbot) -> None:
    """三种主题切换后 QPalette 角色与 ThemeTokens 严格对齐。"""
    from lvjiang.ui.widgets.theme import (
        PAPER,
        THEMES,
        get_theme_manager,
        reset_theme_manager,
    )

    assert PAPER.warning != PAPER.accent

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    reset_theme_manager()
    try:
        mgr = get_theme_manager(app)
        for key, tokens in THEMES.items():
            assert mgr.apply(key) == key
            palette = app.palette()
            assert palette.color(QPalette.ColorRole.Window) == QColor(tokens.window)
            assert palette.color(QPalette.ColorRole.Highlight) == QColor(tokens.selection_bg)
            assert palette.color(QPalette.ColorRole.HighlightedText) == QColor(
                tokens.selection_text)
            assert palette.color(QPalette.ColorRole.Link) == QColor(tokens.accent)
            assert app.property("lvjiangTheme") == key
    finally:
        reset_theme_manager()


def test_theme_changed_emits_only_on_change_and_reset_clears_app(qtbot) -> None:
    """仅实际切换主题时发一次信号，reset 还原 app 样式与属性。"""
    from lvjiang.ui.widgets.theme import get_theme_manager, reset_theme_manager

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    reset_theme_manager()
    try:
        mgr = get_theme_manager(app)
        emitted: list[str] = []
        mgr.theme_changed.connect(emitted.append)

        mgr.apply("light")  # 默认即 light，不重复触发
        assert emitted == []

        mgr.apply("paper")
        mgr.apply("paper")
        mgr.apply("dark")
        assert emitted == ["paper", "dark"]
        assert app.styleSheet()
    finally:
        reset_theme_manager()

    assert app.styleSheet() == ""
    assert app.property("lvjiangTheme") is None


def test_equipment_quality_stylesheet_respects_is_dark() -> None:
    """宣纸/纯白走浅色品阶色，玄墨走深色品阶色（不依赖旧 hex 硬编码）。"""
    from lvjiang.apps.yysls.ui.theme import (
        equipment_quality_stylesheet,
        quality_color,
    )
    from lvjiang.ui.widgets.theme import DARK, LIGHT, PAPER

    light_qss = equipment_quality_stylesheet(LIGHT)
    paper_qss = equipment_quality_stylesheet(PAPER)
    dark_qss = equipment_quality_stylesheet(DARK)

    assert quality_color("gold", LIGHT) in light_qss
    assert quality_color("gold", PAPER) in paper_qss
    assert quality_color("gold", DARK) in dark_qss
    assert quality_color("gold", DARK) not in paper_qss


def test_tuning_results_dialog_refreshes_cards_in_place_on_theme_change(qtbot) -> None:
    """调律总览弹窗在切换主题后原地刷新缓存卡片语义色，不销毁重建实例。"""
    from lvjiang.apps.yysls.core.tuning_history.models import (
        RESET_COUNT_UNREADABLE,
        RESULT_RECYCLED,
        TuningEquipmentResult,
    )
    from lvjiang.apps.yysls.ui.tuning.progress_hub import TuningProgressHub
    from lvjiang.apps.yysls.ui.tuning.result_dialog import TuningResultsDialog
    from lvjiang.apps.yysls.ui.tuning.result_store import TuningResultStore
    from lvjiang.ui.widgets.theme import (
        DARK,
        LIGHT,
        get_theme_manager,
        reset_theme_manager,
    )

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    reset_theme_manager()
    try:
        mgr = get_theme_manager(app)
        mgr.apply("light")

        store = TuningResultStore(TuningProgressHub())
        store.replace_results([TuningEquipmentResult(
            equipment_id=1,
            slot_key="leg",
            name="测试胫甲",
            type="连山",
            level=115,
            quality="gold",
            final_affixes=(),
            final_rating="junk",
            rounds=0,
            result=RESULT_RECYCLED,
            reason="识别失败",
            reset_outcome=RESET_COUNT_UNREADABLE,
        )])
        dialog = TuningResultsDialog(store)
        qtbot.addWidget(dialog)

        assert len(dialog._cards) == 1
        old_card = dialog._cards[0]
        assert old_card.reset_label is not None
        assert LIGHT.danger.lower() in old_card.reset_label.styleSheet().lower()

        mgr.apply("dark")
        assert len(dialog._cards) == 1
        new_card = dialog._cards[0]
        assert new_card is old_card
        assert new_card.reset_label is not None
        assert DARK.danger.lower() in new_card.reset_label.styleSheet().lower()
    finally:
        reset_theme_manager()


def test_execution_button_style_adapts_to_dark_theme(qtbot) -> None:
    """主执行按钮在浅色与玄墨主题下分别选取对应色表。"""
    from PyQt6.QtWidgets import QPushButton

    from lvjiang.ui.widgets.button_styles import (
        _EXECUTION_BUTTON_COLORS,
        _EXECUTION_BUTTON_COLORS_DARK,
        apply_execution_button_style,
    )
    from lvjiang.ui.widgets.theme import get_theme_manager, reset_theme_manager

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    reset_theme_manager()
    try:
        mgr = get_theme_manager(app)
        btn = QPushButton("开始")
        qtbot.addWidget(btn)

        mgr.apply("light")
        apply_execution_button_style(btn, "run")
        assert _EXECUTION_BUTTON_COLORS["run"][0] in btn.styleSheet()

        mgr.apply("dark")
        apply_execution_button_style(btn, "run")
        assert _EXECUTION_BUTTON_COLORS_DARK["run"][0] in btn.styleSheet()
    finally:
        reset_theme_manager()


def test_status_tone_and_dialog_footer_helpers(qtbot) -> None:
    """set_status_tone 与 add_dialog_footer 统一语义属性与按钮样式。"""
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

    from lvjiang.ui.widgets.layout_helpers import add_dialog_footer, set_status_tone
    from lvjiang.ui.widgets.theme import DARK, get_theme_manager, reset_theme_manager

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    reset_theme_manager()
    try:
        mgr = get_theme_manager(app)
        mgr.apply("dark")

        dialog = QDialog()
        qtbot.addWidget(dialog)
        layout = QVBoxLayout(dialog)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        status = add_dialog_footer(layout, buttons)
        assert isinstance(status, QLabel)
        assert status.property("tone") == "danger"
        assert buttons.button(QDialogButtonBox.StandardButton.Ok).property("btnVariant") == "action"
        assert buttons.button(QDialogButtonBox.StandardButton.Cancel).property("btnVariant") == "neutral"

        set_status_tone(status, "已保存", tone="success")
        assert status.text() == "已保存"
        assert status.property("tone") == "success"
        assert f'QLabel[tone="danger"] {{ color: {DARK.danger}; }}' in app.styleSheet()
        assert f'QLabel[tone="success"] {{ color: {DARK.success}; }}' in app.styleSheet()
    finally:
        reset_theme_manager()
