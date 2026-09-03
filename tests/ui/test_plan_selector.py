"""主界面方案下拉：填充、锁定、还原与降级。"""

from lvjiang.core.config.plans import (
    PLAN_MODE_WINDOW,
    Plan,
    get_active_plan_id,
    save_plans,
    set_active_plan_id,
)
from lvjiang.ui.main.run_control import (
    LOCK_REASON_PLAN,
    PLAN_CUSTOM_LABEL,
    RunControlMixin,
)
from lvjiang.ui.main.window import _ContextComboBox


class _Log:
    def __init__(self):
        self.lines = []

    def append(self, text):
        self.lines.append(text)


class _PlanHost(RunControlMixin):
    """只带方案相关控件的宿主桩，其余主窗口依赖不参与。"""

    def __init__(self):
        self.plan_combo = _ContextComboBox()
        self.reference_space_combo = _ContextComboBox()
        self._env_combo = _ContextComboBox()
        self.layout_combo = _ContextComboBox()
        self.reference_space_combo.addItems(["手游", "端游"])
        self._env_combo.addItem("安卓", "android")
        self._env_combo.addItem("桌面", "desktop")
        self.layout_combo.addItems(["默认布局", "桌面布局"])
        self.log_text = _Log()
        self.run_button_refreshes = 0

    def _refresh_run_button(self):
        self.run_button_refreshes += 1


def _host(qtbot):
    host = _PlanHost()
    for combo in (host.plan_combo, host.reference_space_combo,
                  host._env_combo, host.layout_combo):
        qtbot.addWidget(combo)
    return host


def _desktop_plan() -> Plan:
    return Plan.create("端游", space="端游", env="desktop", layout="桌面布局",
                       modes=[PLAN_MODE_WINDOW])


def _refresh(host):
    host._refresh_plan_combo()


def _select(host, text):
    host.plan_combo.setCurrentText(text)
    host._on_plan_changed(host.plan_combo.currentIndex())


def test_combo_lists_custom_first_then_saved_plans(qtbot):
    host = _host(qtbot)
    save_plans([_desktop_plan(), Plan.create("模拟器")])

    _refresh(host)

    labels = [host.plan_combo.itemText(i)
              for i in range(host.plan_combo.count())]
    assert labels == [PLAN_CUSTOM_LABEL, "端游", "模拟器"]
    assert host.plan_combo.currentText() == PLAN_CUSTOM_LABEL


def test_selecting_a_plan_fills_and_locks_the_three_selectors(qtbot):
    host = _host(qtbot)
    plan = _desktop_plan()
    save_plans([plan])
    _refresh(host)

    _select(host, "端游")

    assert host.reference_space_combo.currentText() == "端游"
    assert host._env_combo.currentData() == "desktop"
    assert host.layout_combo.currentText() == "桌面布局"
    for combo in (host.reference_space_combo, host._env_combo,
                  host.layout_combo):
        assert combo.is_locked()
    assert not host.plan_combo.is_locked()
    assert get_active_plan_id() == plan.id


def test_switching_back_to_custom_unlocks_and_restores(qtbot):
    host = _host(qtbot)
    save_plans([_desktop_plan()])
    _refresh(host)
    host.reference_space_combo.setCurrentText("手游")
    host._env_combo.setCurrentIndex(host._env_combo.findData("android"))
    host.layout_combo.setCurrentText("默认布局")

    _select(host, "端游")
    _select(host, PLAN_CUSTOM_LABEL)

    assert host.reference_space_combo.currentText() == "手游"
    assert host._env_combo.currentData() == "android"
    assert host.layout_combo.currentText() == "默认布局"
    for combo in (host.reference_space_combo, host._env_combo,
                  host.layout_combo):
        assert not combo.is_locked()
    assert get_active_plan_id() == ""


def test_switching_from_one_plan_to_another_refills_all_three(qtbot):
    host = _host(qtbot)
    save_plans([
        _desktop_plan(),
        Plan.create("模拟器", space="手游", env="android", layout="默认布局"),
    ])
    _refresh(host)
    _select(host, "端游")

    _select(host, "模拟器")

    assert host.reference_space_combo.currentText() == "手游"
    assert host._env_combo.currentData() == "android"
    assert host.layout_combo.currentText() == "默认布局"


def test_plan_without_stored_context_leaves_the_selectors_alone(qtbot):
    """三项为空的方案不该把选择器清成第一项——空表示「没记录」，不是「选第一个」。"""
    host = _host(qtbot)
    save_plans([Plan.create("空方案")])
    _refresh(host)
    host.reference_space_combo.setCurrentText("端游")

    _select(host, "空方案")

    assert host.reference_space_combo.currentText() == "端游"


def test_switching_between_plans_keeps_the_original_custom_stash(qtbot):
    """连着换两个方案后回自定义，还原的仍是最初手上的那套。"""
    host = _host(qtbot)
    other = Plan.create("模拟器", space="手游", env="android",
                        layout="默认布局")
    save_plans([_desktop_plan(), other])
    _refresh(host)
    host.reference_space_combo.setCurrentText("端游")
    host._env_combo.setCurrentIndex(host._env_combo.findData("desktop"))
    host.layout_combo.setCurrentText("默认布局")

    _select(host, "端游")
    _select(host, "模拟器")
    _select(host, PLAN_CUSTOM_LABEL)

    assert host.reference_space_combo.currentText() == "端游"
    assert host._env_combo.currentData() == "desktop"
    assert host.layout_combo.currentText() == "默认布局"


def test_startup_restores_the_active_plan(qtbot):
    host = _host(qtbot)
    plan = _desktop_plan()
    save_plans([plan])
    set_active_plan_id(plan.id)

    _refresh(host)

    assert host.plan_combo.currentText() == "端游"
    assert host.layout_combo.currentText() == "桌面布局"
    assert host.reference_space_combo.is_locked()


def test_deleted_plan_degrades_to_custom_with_a_notice(qtbot):
    host = _host(qtbot)
    plan = _desktop_plan()
    save_plans([plan])
    set_active_plan_id(plan.id)
    save_plans([])

    _refresh(host)

    assert host.plan_combo.currentText() == PLAN_CUSTOM_LABEL
    assert not host.reference_space_combo.is_locked()
    assert any("已不存在" in line for line in host.log_text.lines)


def test_plan_referring_to_missing_layout_reports_what_failed(qtbot):
    host = _host(qtbot)
    save_plans([Plan.create("坏方案", space="端游", env="desktop",
                            layout="早就删掉的布局")])
    _refresh(host)

    _select(host, "坏方案")

    # 能套用的照样套用，套不上的说清楚是哪一项
    assert host.reference_space_combo.currentText() == "端游"
    assert host._env_combo.currentData() == "desktop"
    assert any("布局" in line for line in host.log_text.lines)
    assert host.reference_space_combo.is_locked()


def test_plan_lock_leaves_the_plan_selector_switchable(qtbot):
    """锁住方案下拉自己会让用户再也回不到自定义。"""
    host = _host(qtbot)
    save_plans([_desktop_plan()])
    _refresh(host)

    _select(host, "端游")

    assert host.plan_combo.isEnabled()
    assert LOCK_REASON_PLAN not in host.plan_combo._lock_reasons
