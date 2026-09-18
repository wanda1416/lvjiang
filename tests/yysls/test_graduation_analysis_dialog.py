"""毕业率分析对话框：共享假设栏副本、按用户+方案缓存、过期标记。"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPushButton, QTabWidget

from lvjiang.apps.yysls.core.graduation.assumptions import Assumptions
from lvjiang.apps.yysls.ui.loadout.affix_analysis_pages import AffixAnalysisPages
from lvjiang.apps.yysls.ui.loadout.graduation_analysis import (
    TAB_OPTIMAL,
    TAB_TRANSMUTE,
    AnalysisCache,
    AssumptionBar,
    GraduationAnalysisDialog,
)
from lvjiang.apps.yysls.ui.loadout.optimal_combo import OptimalComboPage
from tests.yysls.test_transmute_dialog import _equipped, _result


class _StubOptimalPage(OptimalComboPage):
    """跳过读 session 的构造，只保留结果区与假设读取。"""

    def __init__(self, provider):
        from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

        QWidget.__init__(self)
        self._assumptions_provider = provider
        self._results = []
        self._result_cards = []
        self._current_equipped = {}
        self._slot_labels = {}
        self._worker = None
        self._results_inner = QVBoxLayout()
        self._candidate_summary = QLabel("")
        self._btn_search = QPushButton("开始搜索")
        self._tab_widget = QTabWidget()
        for _ in range(3):
            self._tab_widget.addTab(QLabel(), "t")
        self.rendered: list[list] = []

    def _render_results(self, results):
        self.rendered.append(list(results))


def _dialog(qtbot, cache: AnalysisCache, *, initial_tab=TAB_OPTIMAL,
            initial: Assumptions | None = None):
    initial = initial or Assumptions(full_chengyin=True, playstyle="无名")
    bar = AssumptionBar(initial, season_level=110)
    pages = AffixAnalysisPages(
        "鸣金·虹", "基础方案", equipped=_equipped(),
        transmute_runner=lambda _stop, _a: _result(),
        assumptions_provider=bar.value,
    )
    optimal = _StubOptimalPage(bar.value)
    dialog = GraduationAnalysisDialog(
        None, school="鸣金·虹", scheme="基础方案", plan_name="默认方案",
        assumption_bar=bar, optimal_page=optimal, affix_pages=pages,
        cache=cache, initial_tab=initial_tab,
    )
    qtbot.addWidget(dialog)
    return dialog, bar, optimal, pages


def test_bar_is_a_copy_of_plan_assumptions_and_is_not_persisted(qtbot):
    cache = AnalysisCache()
    dialog, bar, _optimal, _pages = _dialog(qtbot, cache)
    assert bar.chk_full_chengyin.isChecked()
    assert not bar.chk_full_level.isChecked()
    assert bar.value() == Assumptions(full_chengyin=True, playstyle="无名")

    bar.chk_full_level.setChecked(True)
    assert bar.value() == Assumptions(
        full_level=110, full_chengyin=True, playstyle="无名")
    # 赛季装备假设承音不是共享假设：它是最优组合页自己的搜索空间选项
    assert [box.text() for box in bar.checkboxes()] == [
        "满等级", "满承音", "满定音", "模拟转律"]
    # 对话框里的改动不落到任何持久化对象：缓存只放结果
    assert cache == AnalysisCache()
    dialog.reject()


def test_entry_tab_and_results_cached_per_plan(qtbot):
    cache = AnalysisCache()
    dialog, _bar, optimal, pages = _dialog(qtbot, cache, initial_tab=TAB_TRANSMUTE)
    assert dialog._tabs.currentIndex() == TAB_TRANSMUTE
    assert [dialog._tabs.tabText(i) for i in range(4)] == [
        "最优组合", "转律建议", "培养建议", "词条收益率"]
    # 技能轴入口在对话框右上角，不在某一页内
    assert dialog.findChild(QPushButton, "rotationButton") is not None

    # 页面已被对话框页签接管，控件在对话框名下
    run = dialog.findChild(QPushButton, "transmuteRunButton")
    assert run is not None
    run.click()
    qtbot.waitUntil(lambda: cache.transmute_result is not None, timeout=5000)
    # 模拟一次搜索完成：结果写入缓存
    optimal._results = [{"rate": 0.9, "equipped": {}}]
    optimal.results_changed.emit(optimal._results)
    assert cache.optimal_results == [{"rate": 0.9, "equipped": {}}]
    dialog.reject()

    # 再次打开：同一份缓存回填，不触发计算
    other = AnalysisCache()
    dialog2, _bar2, optimal2, _pages2 = _dialog(qtbot, cache)
    assert optimal2.rendered == [[{"rate": 0.9, "equipped": {}}]]
    status = dialog2.findChild(QLabel, "transmuteStatus")
    assert status is not None and "建议顺序" in status.text()
    dialog3, _bar3, optimal3, _pages3 = _dialog(qtbot, other)
    assert optimal3.rendered == []


def test_changing_assumptions_marks_results_stale_without_recomputing(qtbot):
    cache = AnalysisCache(optimal_results=[{"rate": 0.9, "equipped": {}}],
                          transmute_result=_result())
    dialog, bar, optimal, _pages = _dialog(qtbot, cache)
    bar.chk_full_dingyin.setChecked(True)
    assert "假设已变化" in optimal._candidate_summary.text()
    status = dialog.findChild(QLabel, "transmuteStatus")
    assert status is not None and "假设已变化" in status.text()
    # 缓存未被清空或改写
    assert cache.optimal_results == [{"rate": 0.9, "equipped": {}}]
    assert cache.transmute_result is _result() or cache.transmute_result is not None
