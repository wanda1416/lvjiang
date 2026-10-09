"""设备端任务生命周期管理测试

task_runner 是悬浮服务的 Python 侧入口，跑在手机上，但它的状态机、JSON 协议、
并发互斥全是纯逻辑，可以在 PC 上完整验证——真正碰硬件的只有引擎创建那一步，
mock 掉即可。这样每次改状态机不必装包。

覆盖：
1. list_tasks 的 JSON 形状 + 发现层异常兜底
2. start_task 的前置校验（无障碍未就绪 / 任务不存在 / 已有任务在跑）
3. 完整成功路径：running → done，结果与日志落进状态
4. 失败路径：异常收进 failed，不让线程带栈消失
5. 停止路径：stop_check 置位后引擎退出，状态为 stopped
6. 引擎复用时上一轮的 variables / output / context 被清空
"""

import json
import threading
import time

import pytest

from lvjiang.core.ondevice import task_runner

# ─── 测试替身 ────────────────────────────────────────────────

class _FakeEngine:
    """只实现 execute 的假引擎

    execute 的行为由 behavior 决定：
      "ok"    立刻返回 result
      "raise" 抛异常
      "loop"  轮询 stop_check 直到被置位（模拟长任务）
    """

    def __init__(self, behavior="ok", result=None, stop_check=None):
        self.behavior = behavior
        self.result = result if result is not None else {"count": 1}
        self.variables = {}
        self.output = {}
        self.context = {}
        self.executed = []
        self._stop_check = stop_check or (lambda: False)

    def execute(self, source, *, initial_variables=None):
        self.executed.append(source)
        if initial_variables:
            self.variables.update(initial_variables)
        if self.behavior == "raise":
            raise RuntimeError("boom")
        if self.behavior == "loop":
            deadline = time.time() + 5.0
            while not self._stop_check() and time.time() < deadline:
                time.sleep(0.01)
        return self.result


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    """每个用例都从干净状态起跑

    _STATE 与 _ENGINE 都是模块级单例（设备端一个进程只有一个任务），
    用例之间不重置会互相污染。
    """
    monkeypatch.setattr(task_runner, "_STATE", task_runner._TaskState())
    monkeypatch.setattr(task_runner, "_ENGINE", None)
    monkeypatch.setattr(task_runner, "_PAUSE_GATE", task_runner._PauseGate())
    task_runner._PAUSE_GATE.set()
    from lvjiang import constants
    from lvjiang.core.config.session import get_session_store
    from lvjiang.core.user_config import User, save_user_metadata

    monkeypatch.setattr(constants, "USERS_DIR", tmp_path / "users")
    constants.SESSION_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (constants.SESSION_CONFIG_DIR / "offline.json").write_text(
        json.dumps({"username": "tester", "layout": "android"}))
    save_user_metadata(User("tester"), constants.USERS_DIR)
    get_session_store().set_active("user", "tester")
    # 默认放行无障碍检查，需要测「未就绪」的用例自己覆盖
    monkeypatch.setattr(
        "lvjiang.core.ondevice.a11y.is_ready", lambda: True, raising=False
    )
    yield
    if task_runner.is_running():
        task_runner.stop_task()
    if task_runner._STATE._thread is not None:
        task_runner._STATE._thread.join(2)


def _fake_tasks(*items):
    """构造 discover_scripts 的返回值"""
    return [
        {
            "id": i["id"],
            "name": i.get("name", i["id"]),
            "wf_file": i.get("wf_file", f"{i['id']}.wf"),
            "class": i.get("class", ""),
            "parameters": i.get("parameters", []),
            "env": i.get("env", []),
        }
        for i in items
    ]


def _patch_discovery(monkeypatch, tasks):
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.discover_scripts", lambda: tasks
    )
    # list_tasks 现在直接调 list_exposed_scripts，绕过 workflows.yaml 过滤
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", lambda *_args, **_kw: tasks
    )


def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(task_runner, "_get_engine", lambda: engine)


def _patch_source(monkeypatch, value="SOURCE"):
    """绕开 .wf 文件是否真实存在的检查——本模块要测的是状态机，不是文件系统"""
    monkeypatch.setattr(task_runner, "_build_source", lambda task, engine: value)


def _wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _status():
    return json.loads(task_runner.get_status())


# ─── list_tasks ─────────────────────────────────────────────

def test_log_cursor_preserves_levels_and_resets_between_runs():
    state = task_runner._TaskState()
    state.begin("first", "first")
    state.log("progress")
    state.log("failure", "ERROR")
    first = state.snapshot()
    records = first["log_records"]
    assert [record["level"] for record in records] == ["INFO", "ERROR"]
    assert [record["text"] for record in records] == first["logs"]
    assert records[1]["seq"] > records[0]["seq"]
    records[0]["level"] = "DEBUG"
    assert state.snapshot()["log_records"][0]["level"] == "INFO"
    state.begin("second", "second")
    assert state.snapshot()["log_records"] == []
    assert state.snapshot()["log_generation"] > first["log_generation"]
    for _ in range(task_runner._LOG_CAPACITY + 1):
        state.log("progress")
    second = state.snapshot()
    assert len(second["log_records"]) == task_runner._LOG_CAPACITY
    assert second["log_records"][0]["seq"] > records[-1]["seq"]


def test_user_selection_only_changes_phone_active_user(monkeypatch):
    from lvjiang import constants
    from lvjiang.core.config.session import get_session_store
    from lvjiang.core.user_config import User, save_user_metadata

    save_user_metadata(User("second", workflow_params={"task": {"count": 2}}), constants.USERS_DIR)
    store = get_session_store()
    store.set_node("users", ["tester", "second", "missing"])
    before = (constants.SESSION_CONFIG_DIR / "offline.json").read_bytes()
    listed = json.loads(task_runner.list_users())
    assert listed["users"] == ["tester", "second"]
    assert store.get_active("user") == "tester" # 展示候选不能改变执行用户。
    assert json.loads(task_runner.select_user("second"))["ok"]
    status = json.loads(task_runner.get_status())
    assert status["sync"]["execution_username"] == "second"
    assert status["sync"]["username"] == "tester"
    assert (constants.SESSION_CONFIG_DIR / "offline.json").read_bytes() == before
    assert not (constants.USERS_DIR / "missing.json").exists()
    assert not json.loads(task_runner.select_user("../outside"))["ok"]
    assert store.get_active("user") == "second"


def test_user_selection_is_rejected_while_task_is_active():
    from lvjiang.core.config.session import get_session_store

    task_runner._STATE.begin("task", "task")
    task_runner._STATE.request_pause()
    task_runner._STATE.acknowledge_pause()
    assert not json.loads(task_runner.select_user("tester"))["ok"]
    assert get_session_store().get_active("user") == "tester"

def test_list_tasks_shape(monkeypatch):
    """清单每项只暴露 id / name / source 三个字段"""
    _patch_discovery(monkeypatch, _fake_tasks(
        {"id": "b_task", "name": "任务B"},
        {"id": "a_task", "name": "任务A", "class": "a_task", "wf_file": ""},
    ))
    data = json.loads(task_runner.list_tasks())

    assert data["ok"] is True
    assert data["error"] == ""
    assert [t["id"] for t in data["tasks"]] == ["b_task", "a_task"]
    assert data["tasks"][0] == {"id": "b_task", "name": "任务B", "source": "wf"}
    assert data["tasks"][1]["source"] == "class"


def test_list_tasks_swallows_discovery_error(monkeypatch):
    """发现层炸了也要回一个合法 JSON，否则 Kotlin 侧解析直接抛"""
    def boom():
        raise OSError("配置目录不存在")

    monkeypatch.setattr("lvjiang.workflows.discovery.discover_scripts", boom)
    data = json.loads(task_runner.list_tasks())

    assert data["ok"] is False
    assert data["tasks"] == []
    assert "OSError" in data["error"]


def test_device_lists_tuning_without_exposing_other_dedicated_tasks(monkeypatch):
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )
    from lvjiang.workflows import discovery, implementations
    from lvjiang.workflows.preferences import DailyScriptPrefs

    monkeypatch.setattr(implementations, "list_workflows", lambda: ["auto_tuning"])
    monkeypatch.setattr(implementations, "get_workflow_class", lambda _name: AutoTuningWorkflow)
    monkeypatch.setattr(discovery, "_discover_wf_scripts", lambda *_: {
        "helper": {"id": "helper", "scope": "dedicated", "source_layer": "system"},
        "pc_only": {"id": "pc_only", "env": ["windows"], "source_layer": "system"},
    })
    monkeypatch.setattr(discovery, "load_preferences", lambda: DailyScriptPrefs([], {}, {}))
    monkeypatch.setattr("lvjiang.core.ondevice.plugins.ensure_loaded", lambda: None)

    assert "auto_tuning" not in [item["id"] for item in discovery.list_exposed_scripts("android")]
    tasks = json.loads(task_runner.list_tasks())["tasks"]
    assert tasks == [{"id": "auto_tuning", "name": "自动调律", "source": "class"}]
    monkeypatch.setattr(discovery, "load_preferences",
                        lambda: DailyScriptPrefs([], {"auto_tuning": False}, {}))
    assert json.loads(task_runner.list_tasks())["tasks"] == []


# ─── start_task 前置校验 ─────────────────────────────────────

def test_phone_without_ready_configuration_cannot_start_or_list_tasks():
    from lvjiang import constants
    (constants.SESSION_CONFIG_DIR / "offline.json").unlink()
    assert not json.loads(task_runner.list_tasks())["ok"]
    result = json.loads(task_runner.start_task("task"))
    assert not result["ok"] and "预置配置未就绪" in result["message"]

def test_start_rejects_when_a11y_not_ready(monkeypatch):
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}))
    monkeypatch.setattr("lvjiang.core.ondevice.a11y.is_ready", lambda: False)

    data = json.loads(task_runner.start_task("t1"))

    assert data["ok"] is False
    assert "无障碍" in data["message"]
    assert _status()["state"] == task_runner.STATE_IDLE


def test_remote_start_binds_selected_synced_user_before_execution(monkeypatch):
    from lvjiang import constants
    from lvjiang.core.config.session import get_session_store
    from lvjiang.core.user_config import User, save_user_metadata

    save_user_metadata(User("second"), constants.USERS_DIR)
    get_session_store().set_node("users", ["tester", "second"])
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}))
    engine = _FakeEngine("ok")
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)
    assert json.loads(task_runner.start_task("t1", username="second"))["ok"]
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_DONE)
    assert engine.run_username == "second"
    assert engine.session["current_user"] == "second"


def test_start_rejects_unknown_task(monkeypatch):
    """错误提示里要带上可选项，否则拼错 id 时只能盲猜"""
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}, {"id": "t2"}))

    data = json.loads(task_runner.start_task("nope"))

    assert data["ok"] is False
    assert "t1" in data["message"] and "t2" in data["message"]


def test_start_rejects_bad_initial_variables(monkeypatch):
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}))

    data = json.loads(task_runner.start_task("t1", "[1, 2]"))

    assert data["ok"] is False
    assert "初始变量" in data["message"]


def test_start_rejects_concurrent_task(monkeypatch):
    """同一时刻只允许一个任务：两个任务共用一个引擎，并发跑等于互相踩截图"""
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}, {"id": "t2"}))
    engine = _FakeEngine("loop", stop_check=task_runner._STATE.should_stop)
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    assert json.loads(task_runner.start_task("t1"))["ok"] is True
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_RUNNING)

    second = json.loads(task_runner.start_task("t2"))
    assert second["ok"] is False
    assert "已有任务" in second["message"]

    task_runner.stop_task()
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_STOPPED)


# ─── 执行路径 ───────────────────────────────────────────────

def test_successful_run_reaches_done(monkeypatch):
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1", "name": "冒烟"}))
    engine = _FakeEngine("ok", result={"a": 1, "b": 2})
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    assert json.loads(task_runner.start_task("t1"))["ok"] is True
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_DONE)

    status = _status()
    assert status["task_id"] == "t1"
    assert status["task_name"] == "冒烟"
    assert "冒烟" in status["message"]
    assert status["result"] == {"a": 1, "b": 2}
    assert any("收集 2 项" in line for line in status["logs"])
    assert status["elapsed"] >= 0


def test_initial_variables_reach_engine(monkeypatch):
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}))
    engine = _FakeEngine("ok")
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    task_runner.start_task("t1", json.dumps({"部位": "武器"}))
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_DONE)

    assert engine.variables == {"部位": "武器"}


def test_synced_user_and_parameter_override_are_bound_before_execution(monkeypatch):
    from lvjiang import constants
    from lvjiang.core.config.wf_configs import set_wf_config
    from lvjiang.core.user_config import set_user_workflow_params

    _patch_discovery(monkeypatch, _fake_tasks({
        "id": "task", "parameters": [{"name": "count", "type": "number", "default": 1}],
    }))
    set_wf_config("task", {"count": 3})
    set_user_workflow_params("tester", "task", {"count": 4}, constants.USERS_DIR)
    engine = _FakeEngine("ok")
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    assert json.loads(task_runner.start_task("task"))["ok"]
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_DONE)
    assert engine.run_username == "tester"
    assert engine.variables["count"] == 4
    assert engine.session["current_user"] == "tester"


def test_tuning_freezes_synced_user_config_instead_of_shared_config(monkeypatch):
    from lvjiang import constants
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )
    from lvjiang.core.config.wf_configs import set_wf_config
    from lvjiang.core.user_config import set_user_workflow_params

    _patch_discovery(monkeypatch, _fake_tasks({"id": "auto_tuning", "class": "auto_tuning"}))
    monkeypatch.setattr("lvjiang.workflows.implementations.get_workflow_class",
                        lambda _name: AutoTuningWorkflow)
    set_wf_config("auto_tuning", {"selected_slots": ["head"]})
    saved = {"selected_slots": ["ring"], "rules": {"huiyi_general": {"enabled": True}}}
    set_user_workflow_params("tester", "auto_tuning", saved, constants.USERS_DIR)
    engine = _FakeEngine("ok")
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    assert json.loads(task_runner.start_task("auto_tuning"))["ok"]
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_DONE)
    snapshot = engine.workflow_config_snapshot
    assert snapshot["selected_slots"] == ["ring"]
    assert snapshot["rules"] == saved["rules"]
    set_user_workflow_params("tester", "auto_tuning", {"selected_slots": ["chest"]}, constants.USERS_DIR)
    assert snapshot["selected_slots"] == ["ring"]


def test_wrapped_workflow_oom_keeps_reason_and_releases_engine(monkeypatch):
    from unittest.mock import Mock

    from lvjiang.workflows.errors import WorkflowExecutionError

    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}))
    error = WorkflowExecutionError("TestWorkflow", {})
    error.__cause__ = MemoryError("手机截图内存不足")
    engine = _FakeEngine()
    engine.execute = Mock(side_effect=error)
    release = Mock()
    monkeypatch.setattr(task_runner, "release_engine", release)
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    assert json.loads(task_runner.start_task("t1"))["ok"]
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_FAILED)
    assert "手机截图内存不足" in _status()["message"]
    release.assert_called_once()


def test_failure_is_captured_as_failed(monkeypatch):
    """异常不能让任务线程带着栈静默消失，必须落进状态与日志"""
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}))
    _patch_engine(monkeypatch, _FakeEngine("raise"))
    _patch_source(monkeypatch)

    task_runner.start_task("t1")
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_FAILED)

    status = _status()
    assert "RuntimeError" in status["message"]
    assert "boom" in status["message"]
    assert any("boom" in line for line in status["logs"])


def test_stop_marks_stopped(monkeypatch):
    """停止是协作式的：置位 → 引擎自己退出 → 状态收敛到 stopped"""
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1", "name": "长任务"}))
    engine = _FakeEngine("loop", stop_check=task_runner._STATE.should_stop)
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    task_runner.start_task("t1")
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_RUNNING)

    result = json.loads(task_runner.stop_task())
    assert result["ok"] is True
    # 工作线程可能已在读取状态前完成停止，不能要求瞬态至少持续一次轮询。
    snapshot = _status()
    assert snapshot["stopping"] or snapshot["state"] == task_runner.STATE_STOPPED

    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_STOPPED)
    assert "长任务" in _status()["message"]


def test_stop_without_running_task():
    data = json.loads(task_runner.stop_task())
    assert data["ok"] is False
    assert "没有运行中" in data["message"]


def test_engine_state_reset_between_runs(monkeypatch):
    """引擎跨任务复用，上一轮的中间变量必须清掉，否则条件判断会读到脏数据"""
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}))
    engine = _FakeEngine("ok")
    engine.variables = {"stale": 1}
    engine.output = {"stale": 1}
    engine.context = {"stale": 1}
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    task_runner.start_task("t1")
    assert _wait_until(lambda: _status()["state"] == task_runner.STATE_DONE)

    assert engine.variables == {}
    assert engine.output == {}
    assert engine.context == {}


def test_logs_are_capped():
    """日志是环形缓冲：长任务刷几万行也不能把内存吃穿"""
    state = task_runner._TaskState()
    for i in range(task_runner._LOG_CAPACITY + 50):
        state.log(str(i))

    logs = state.snapshot()["logs"]
    assert len(logs) == task_runner._LOG_CAPACITY
    assert logs[-1].endswith(str(task_runner._LOG_CAPACITY + 49))


def test_is_running_reflects_state(monkeypatch):
    """Kotlin 侧用的轻量判据要与 JSON 里的 state 一致"""
    _patch_discovery(monkeypatch, _fake_tasks({"id": "t1"}))
    engine = _FakeEngine("loop", stop_check=task_runner._STATE.should_stop)
    _patch_engine(monkeypatch, engine)
    _patch_source(monkeypatch)

    assert task_runner.is_running() is False
    task_runner.start_task("t1")
    assert _wait_until(task_runner.is_running)

    task_runner.stop_task()
    assert _wait_until(lambda: not task_runner.is_running())


# ─── _build_source ──────────────────────────────────────────

def test_build_source_missing_wf_file(monkeypatch, tmp_path):
    """.wf 不在盘上要报路径，别让引擎去解析一个不存在的文件"""
    import lvjiang.core.config.resolver as cr
    monkeypatch.setattr(cr, "SYSTEM_CONFIG_DIR", tmp_path / "system")
    monkeypatch.setattr(cr, "LOCAL_CONFIG_DIR", tmp_path / "local")
    with pytest.raises(FileNotFoundError, match="工作流文件不存在"):
        task_runner._build_source({"wf_file": "ghost.wf", "class": ""}, None)


def test_build_source_returns_existing_path(monkeypatch, tmp_path):
    import lvjiang.core.config.resolver as cr
    wf_dir = tmp_path / "system" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "real.wf"
    wf.write_text("log \"hi\"\n", encoding="utf-8")
    monkeypatch.setattr(cr, "SYSTEM_CONFIG_DIR", tmp_path / "system")
    monkeypatch.setattr(cr, "LOCAL_CONFIG_DIR", tmp_path / "local")

    got = task_runner._build_source({"wf_file": "real.wf", "class": ""}, None)
    assert got == wf


def test_state_snapshot_is_thread_safe():
    """轮询与任务线程同时读写状态，不能出现半更新的快照"""
    state = task_runner._TaskState()
    state.begin("t1", "任务")
    errors = []

    def writer():
        try:
            for i in range(500):
                state.log(f"line {i}")
                state.set_message(f"step {i}")
        except Exception as e:  # pragma: no cover - 只在真出问题时触发
            errors.append(e)

    def reader():
        try:
            for _ in range(500):
                snap = state.snapshot()
                assert snap["state"] == task_runner.STATE_RUNNING
                assert isinstance(snap["logs"], list)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
