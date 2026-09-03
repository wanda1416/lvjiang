"""通用工作流启动前校验测试。"""

from pathlib import Path

from lvjiang.ui.main.run_control import RunControlMixin


class _Resolver:
    def __init__(self, paths: dict[str, Path]):
        self.paths = paths

    def resolve_read(self, rel_path: str):
        return self.paths.get(rel_path)


def test_stale_workflow_path_is_refreshed_by_script_id(tmp_path, monkeypatch):
    wf_path = tmp_path / "workflows" / "standalone" / "demo.wf"
    wf_path.parent.mkdir(parents=True)
    wf_path.write_text("log 'ok'\n", encoding="utf-8")
    resolver = _Resolver({"workflows/standalone/demo.wf": wf_path})
    flow_cfg = {"id": "demo", "wf_file": "demo.wf"}

    # 路径自愈已下沉到 workflows.discovery，日常页与批量页共用一份。
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.get_resolver", lambda: resolver)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.discover_scripts",
        lambda: [{"id": "demo", "wf_file": "standalone/demo.wf"}],
    )

    resolved = RunControlMixin._resolve_dsl_workflow_path(object(), flow_cfg)

    assert resolved == wf_path
    assert flow_cfg["wf_file"] == "standalone/demo.wf"


class _StartStub(RunControlMixin):
    def __init__(self):
        self._run_state = "idle"
        self.errors: list[str] = []
        self.begin_calls = 0
        self._env_combo = type(
            "EnvCombo", (), {"currentData": lambda _self: "desktop"}
        )()

    def _backend_ready(self):
        return True

    def _plan_allows_backend(self):
        return True

    def _get_selected_flow_config(self):
        return {
            "id": "missing",
            "name": "缺失脚本",
            "wf_file": "missing.wf",
            "class": "",
            "env": [],
        }

    def _resolve_dsl_workflow_path(self, _flow_cfg):
        return None

    def _show_workflow_start_error(self, message: str):
        self.errors.append(message)

    def _begin_automation(self, _name: str):
        self.begin_calls += 1
        return True


def test_missing_workflow_is_rejected_before_running_state():
    stub = _StartStub()

    stub._on_run_workflow()

    assert stub.errors == ["工作流文件不存在: missing.wf"]
    assert stub.begin_calls == 0
    assert stub._run_state == "idle"


def test_start_error_is_logged_without_modal_main_window(qtbot):
    from PyQt6.QtCore import Qt

    messages: list[str] = []
    stub = type("S", (), {
        "log_text": type("L", (), {
            "append": lambda self, text: messages.append(text),
        })(),
    })()

    RunControlMixin._show_workflow_start_error(stub, "工作流文件不存在: x.wf")

    assert messages == ["[错误] 工作流文件不存在: x.wf"]
    dialog = stub._workflow_start_error_dialog
    assert dialog.windowTitle() == "无法启动工作流"
    assert dialog.text() == "工作流文件不存在: x.wf"
    assert dialog.isVisible()
    assert not dialog.isModal()
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    dialog.close()
