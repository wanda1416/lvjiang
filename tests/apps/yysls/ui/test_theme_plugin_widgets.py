"""插件侧主题回归：品阶色与调律总览卡片跟随 ThemeTokens。

这两个用例依赖 yysls 插件的 theme / tuning 模块，按 tests/test_app_dependency_gate.py
的测试分层放在插件测试目录下；通用主题机制仍在 tests/ui/widgets/test_theme.py。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication


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
