"""TuningTab（调律 Tab 插件页面）测试

假 host（QObject 带宿主信号/API 桩）+ 隔离的插件 session（tmp_path），
覆盖：会话加载/保存、部位全选反选、按钮三态、f9_run 启停分发、
未选部位/未选规则的报错路径、成功启动时的 configure 回调注入。
"""

import json

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

import src.apps.yysls.plugin_session as ps_module
from src.apps.yysls.plugin_session import PluginSession
from src.apps.yysls.ui.tuning_tab import TuningTab


class _FakeHost(QObject):
    """通用 MainWindow 宿主桩：信号 + 宿主 API 记录"""

    automation_state_changed = pyqtSignal(str)
    user_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._running = False
        self.stop_requests = 0
        self.logs: list[str] = []
        self.run_calls: list[tuple] = []

    @property
    def is_running(self) -> bool:
        return self._running

    def request_stop(self):
        self.stop_requests += 1

    def append_log(self, text: str):
        self.logs.append(text)

    def active_user_name(self) -> str:
        return "测试用户"

    def run_workflow_implementation(self, impl_name, flow_name, configure):
        self.run_calls.append((impl_name, flow_name, configure))


@pytest.fixture
def session_path(tmp_path, monkeypatch):
    """把插件 session 单例替换为 tmp_path 下的隔离实例"""
    path = tmp_path / "session.json"
    monkeypatch.setattr(ps_module, "_session", PluginSession(path))
    return path


@pytest.fixture
def host():
    return _FakeHost()


def _make_tab(qtbot, host):
    tab = TuningTab(host)
    qtbot.addWidget(tab)
    return tab


# ─── 会话加载 / 保存 ───────────────────────────────────────

class TestSessionRoundtrip:
    def test_load_selected_slots_from_session(self, qtbot, host, session_path):
        session_path.write_text(json.dumps({
            "tuning": {"selected_slots": ["ring", "head"]},
        }), encoding="utf-8")
        ps_module._session = PluginSession(session_path)  # 重新加载文件内容

        tab = _make_tab(qtbot, host)
        assert tab._get_tuning_selected_slots() == ["ring", "head"]

    def test_default_slots_when_no_session(self, qtbot, host, session_path):
        tab = _make_tab(qtbot, host)
        # 默认全选（除副武器禁用），与 default_slots 一致
        assert tab._get_tuning_selected_slots() == [
            "main_weapon", "ring", "pendant", "head", "chest", "leg", "wrist"]

    def test_checkbox_change_persists(self, qtbot, host, session_path):
        tab = _make_tab(qtbot, host)
        cb = next(c for c in tab._tuning_checkboxes if c.objectName() == "ring")
        cb.setChecked(False)  # stateChanged → _save_tuning_config 落盘

        saved = json.loads(session_path.read_text(encoding="utf-8"))
        assert "ring" not in saved["tuning"]["selected_slots"]

    def test_sub_weapon_stays_disabled(self, qtbot, host, session_path):
        session_path.write_text(json.dumps({
            "tuning": {"selected_slots": ["sub_weapon", "ring"]},
        }), encoding="utf-8")
        ps_module._session = PluginSession(session_path)

        tab = _make_tab(qtbot, host)
        sub = next(c for c in tab._tuning_checkboxes if c.objectName() == "sub_weapon")
        assert not sub.isEnabled()
        assert not sub.isChecked()  # 禁用项不随会话配置回选


# ─── 部位全选 / 取消全选 ───────────────────────────────────

class TestSelectAll:
    def test_select_and_deselect_all(self, qtbot, host, session_path):
        tab = _make_tab(qtbot, host)
        tab._set_all_tuning_checks(False)
        assert tab._get_tuning_selected_slots() == []
        tab._set_all_tuning_checks(True)
        # 全选不含禁用的副武器
        assert "sub_weapon" not in tab._get_tuning_selected_slots()
        assert len(tab._get_tuning_selected_slots()) == 7


# ─── 按钮三态（订阅宿主 automation_state_changed）──────────

class TestButtonState:
    @pytest.mark.parametrize("state,text", [
        ("running", "停止 (F10)"),
        ("not_ready", "未就绪"),
        ("ready", "开始调律 (F9)"),
    ])
    def test_button_follows_host_state(self, qtbot, host, session_path, state, text):
        tab = _make_tab(qtbot, host)
        host.automation_state_changed.emit(state)
        assert tab.btn_run_tuning.text() == text


# ─── f9_run 启停分发与报错路径 ─────────────────────────────

class TestF9Run:
    def test_running_requests_stop(self, qtbot, host, session_path):
        tab = _make_tab(qtbot, host)
        host._running = True
        tab.f9_run()
        assert host.stop_requests == 1
        assert host.run_calls == []

    def test_no_slots_selected_logs_error(self, qtbot, host, session_path):
        tab = _make_tab(qtbot, host)
        tab._set_all_tuning_checks(False)
        tab.f9_run()
        assert any("请至少选择一个调律部位" in m for m in host.logs)
        assert host.run_calls == []

    def test_no_rules_enabled_logs_error(self, qtbot, host, session_path):
        tab = _make_tab(qtbot, host)
        tab._get_tuning_rule_config = lambda: {"huiyi_general": {"enabled": False}}
        tab.f9_run()
        assert any("请至少选择一个调律规则" in m for m in host.logs)
        assert host.run_calls == []

    def test_starts_via_host_with_configure(self, qtbot, host, session_path):
        tab = _make_tab(qtbot, host)
        tab.f9_run()

        assert len(host.run_calls) == 1
        impl_name, flow_name, configure = host.run_calls[0]
        assert impl_name == "auto_tuning"
        assert flow_name == "自动调律"

        # configure 回调向工作流实例注入专属参数并输出开始日志
        class _Wf:
            pass
        wf = _Wf()
        configure(wf, engine=None)
        assert wf._selected_slots == tab._get_tuning_selected_slots()
        assert wf._rule_judges
        assert wf._judge_rule_keys
        assert wf._doc_username == "测试用户"
        assert any(m.startswith("[开始] 自动调律") for m in host.logs)
