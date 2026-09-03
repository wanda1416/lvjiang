"""_set_context_controls_locked 按原因决定锁哪几个选择器。"""

from types import SimpleNamespace

from lvjiang.ui.main.run_control import (
    LOCK_REASON_BATCH,
    LOCK_REASON_PLAN,
    RunControlMixin,
)


class _Combo:
    def __init__(self):
        self.calls = []

    def set_locked(self, reason, locked):
        self.calls.append((reason, locked))


def _host():
    return SimpleNamespace(
        plan_combo=_Combo(),
        reference_space_combo=_Combo(),
        _env_combo=_Combo(),
        layout_combo=_Combo(),
    )


def test_plan_lock_covers_the_three_selectors_it_defines():
    host = _host()

    RunControlMixin._set_context_controls_locked(
        host, LOCK_REASON_PLAN, True)

    expected = [(LOCK_REASON_PLAN, True)]
    assert host.reference_space_combo.calls == expected
    assert host._env_combo.calls == expected
    assert host.layout_combo.calls == expected


def test_plan_lock_never_locks_the_plan_selector_itself():
    """锁住方案下拉会让用户再也切不回自定义。"""
    host = _host()

    RunControlMixin._set_context_controls_locked(
        host, LOCK_REASON_PLAN, True)

    assert host.plan_combo.calls == []


def test_batch_lock_covers_the_plan_selector():
    """切方案会连带改环境和布局，等于绕过批量锁。"""
    host = _host()

    RunControlMixin._set_context_controls_locked(
        host, LOCK_REASON_BATCH, True)

    assert host.plan_combo.calls == [(LOCK_REASON_BATCH, True)]
    assert host._env_combo.calls == [(LOCK_REASON_BATCH, True)]
    assert host.layout_combo.calls == [(LOCK_REASON_BATCH, True)]


def test_batch_lock_leaves_the_gallery_selector_alone():
    """批量锁对图库的范围保持原样，本次不扩大。"""
    host = _host()

    RunControlMixin._set_context_controls_locked(
        host, LOCK_REASON_BATCH, True)

    assert host.reference_space_combo.calls == []


def test_missing_selector_is_skipped():
    """插件宿主/测试桩可能没有这些控件，不该炸。"""
    host = SimpleNamespace(_env_combo=_Combo())

    RunControlMixin._set_context_controls_locked(
        host, LOCK_REASON_PLAN, True)

    assert host._env_combo.calls == [(LOCK_REASON_PLAN, True)]
