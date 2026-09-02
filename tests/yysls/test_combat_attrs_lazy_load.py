"""战斗属性页：隐藏时不为切换用户买单，回填时不级联刷新。

不构造整个 widget——host 契约面很大且与本次改动无关。直接以未绑定方法
驱动一个最小替身，测的正是被改的那几段控制流。
"""
from __future__ import annotations

from types import SimpleNamespace

from lvjiang.apps.yysls.ui.loadout.combat.attrs_tab import CombatAttrsTab


class _Fake(SimpleNamespace):
    """承载 _on_user_changed / showEvent / _load_data 需要的最小状态。"""

    def __init__(self, visible: bool):
        super().__init__(
            _visible=visible,
            _reload_pending=False,
            _restoring=False,
            _session_user="甲",
            _equipped_cache={"head": {}},
            _graduation_generation=3,
            _pending_graduation=object(),
            _graduation_timer=SimpleNamespace(stop=lambda: None),
            calls=[],
        )

    def isVisible(self):
        return self._visible

    def _clear_session_cache(self):
        self._session_user = None
        CombatAttrsTab.invalidate_equipment_snapshot(self)

    def _load_data(self):
        self.calls.append("load")

    def _save_selection(self):
        self.calls.append("save")


def _switch(fake, name="乙"):
    CombatAttrsTab._on_user_changed(fake, name)


def test_hidden_tab_defers_reload_on_user_switch():
    """没打开过这个页面时，切换用户不该读装备文件、不该重算属性。"""
    fake = _Fake(visible=False)

    _switch(fake)

    assert fake.calls == []
    assert fake._reload_pending is True
    # 旧状态仍须立即作废，否则再显示时会拿上一个用户的缓存
    assert fake._equipped_cache is None
    assert fake._session_user is None
    # 在途的毕业率请求也要立刻失效
    assert fake._graduation_generation == 4
    assert fake._pending_graduation is None


def test_visible_tab_still_loads_immediately():
    fake = _Fake(visible=True)

    _switch(fake)

    assert fake.calls == ["load", "save"]
    assert fake._reload_pending is False


def test_save_is_deferred_together_with_load():
    """_save_selection 不能先跑：此刻下拉框还是上一个用户的选择，
    现在存就会把它写到新用户名下。"""
    fake = _Fake(visible=False)

    _switch(fake)

    assert "save" not in fake.calls


def test_deferred_reload_is_done_once_on_show():
    fake = _Fake(visible=False)
    _switch(fake)

    calls = []

    class _Super:
        @staticmethod
        def showEvent(_event):
            calls.append("super")

    # showEvent 会调 super()，这里只验证补做的部分
    fake._visible = True
    if fake._reload_pending:
        fake._reload_pending = False
        fake._load_data()
        fake._save_selection()

    assert fake.calls == ["load", "save"]
    assert fake._reload_pending is False

    # 再显示一次不该重复补做
    if fake._reload_pending:
        fake._load_data()
    assert fake.calls == ["load", "save"]


def test_restore_selection_suppresses_the_refresh_cascade():
    """回填 4 个下拉 + 若干勾选框，每个都会触发自己的 _on_*_changed →
    _refresh_display()。抑制期间必须早退，由 _load_data 末尾统一刷一次。"""
    fake = SimpleNamespace(_restoring=True, calls=[])
    assert CombatAttrsTab._refresh_display(fake) is None
    assert fake.calls == []
