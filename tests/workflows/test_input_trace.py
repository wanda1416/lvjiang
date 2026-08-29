from unittest.mock import MagicMock

import pytest

from lvjiang.core.input_trace import (
    InputTrace,
    InputTraceEvent,
    encode_input_trace,
)
from lvjiang.workflows.engine.signals import WorkflowUserError

from .conftest import make_engine


def test_validate_rejects_missing_input_trace(wf_root):
    wf = wf_root / "missing.wf"
    wf.write_text(
        'replay input_trace "lvtrace/missing.lvtrace"\n',
        encoding="utf-8",
    )

    with pytest.raises(WorkflowUserError, match="无法读取输入轨迹"):
        make_engine().validate_only(wf)


def test_validate_covers_input_traces_inside_imported_procs(wf_root):
    """import 引入的过程体里的 replay input_trace 也必须在校验期暴露。"""
    lib_dir = wf_root / "lib"
    lib_dir.mkdir()
    # 用 ../lvtrace/ 指到 workflows 根的 lvtrace（唯一合法落点），
    # 这样才会因「文件不存在」而失败，而不是先被越界检查挡下。
    (lib_dir / "lib.wf").write_text(
        'def scan_it()\n'
        '    replay input_trace "../lvtrace/missing.lvtrace"\n'
        'end\n',
        encoding="utf-8",
    )
    root = wf_root / "root.wf"
    root.write_text(
        'import "lib/lib.wf"\ncall scan_it()\n',
        encoding="utf-8",
    )

    with pytest.raises(WorkflowUserError, match="无法读取输入轨迹"):
        make_engine().validate_only(root)


def test_imported_proc_input_trace_resolves_relative_to_its_own_file(wf_root):
    """导入过程里的相对轨迹路径相对该过程自己的文件解析，不是根文件。

    轨迹必须落在 workflows 根的 lvtrace/（save_input_trace_bundle 的落盘
    约定）。所以子目录里的过程要写 ``../lvtrace/x.lvtrace``——正好构成
    区分：若按根文件解析，``..`` 会逃出根而被拒；按过程自己的文件解析
    才恰好落在根的 lvtrace/ 上。
    """
    lib_dir = wf_root / "lib"
    lib_dir.mkdir(parents=True)
    trace_dir = wf_root / "lvtrace"
    trace_dir.mkdir()
    trace = InputTrace(1000, 800, (InputTraceEvent(0, "move", (3, -1)),))
    (trace_dir / "x.lvtrace").write_bytes(encode_input_trace(trace))
    (lib_dir / "lib.wf").write_text(
        'def scan_it()\n'
        '    replay input_trace "../lvtrace/x.lvtrace"\n'
        'end\n',
        encoding="utf-8",
    )
    root = wf_root / "root.wf"
    root.write_text(
        'import "lib/lib.wf"\ncall scan_it()\n',
        encoding="utf-8",
    )

    input_ctrl = MagicMock()
    engine = make_engine(input_ctrl=input_ctrl)
    engine.execute(root)

    input_ctrl.replay_input_trace.assert_called_once()
    args, _kwargs = input_ctrl.replay_input_trace.call_args
    assert args == (trace,)


def test_validate_rejects_input_trace_path_traversal_outside_workflow_tree(
    wf_root, monkeypatch,
):
    """借多层 .. 指向根目录树之外某个恰好也叫 lvtrace 的目录必须被拒绝。"""
    from lvjiang.core.config import resolver as resolver_module

    workflows_root = wf_root / "workflows_root"
    (workflows_root / "workflows" / "standalone").mkdir(parents=True)
    outside_trace_dir = wf_root / "outside" / "lvtrace"
    outside_trace_dir.mkdir(parents=True)
    trace = InputTrace(1000, 800, (InputTraceEvent(0, "move", (1, 1)),))
    (outside_trace_dir / "x.lvtrace").write_bytes(encode_input_trace(trace))

    wf = workflows_root / "workflows" / "standalone" / "evil.wf"
    wf.write_text(
        'replay input_trace "../../../outside/lvtrace/x.lvtrace"\n',
        encoding="utf-8",
    )

    class _Resolver:
        system_dir = workflows_root
        local_dir = workflows_root

    monkeypatch.setattr(resolver_module, "get_resolver", lambda: _Resolver())
    import lvjiang.workflows.engine.core as engine_core
    monkeypatch.setattr(engine_core, "get_resolver", lambda: _Resolver())

    with pytest.raises(WorkflowUserError, match="越权引用"):
        make_engine().validate_only(wf)


def test_engine_replays_valid_input_trace_through_single_backend_call(wf_root):
    trace_dir = wf_root / "lvtrace"
    trace_dir.mkdir()
    trace_path = trace_dir / "valid.lvtrace"
    trace = InputTrace(
        1000,
        800,
        (InputTraceEvent(0, "move", (10, -4)),),
    )
    trace_path.write_bytes(encode_input_trace(trace))
    wf = wf_root / "valid.wf"
    wf.write_text(
        'replay input_trace "lvtrace/valid.lvtrace"\n',
        encoding="utf-8",
    )
    input_ctrl = MagicMock()
    engine = make_engine(input_ctrl=input_ctrl)

    engine.execute(wf)

    input_ctrl.replay_input_trace.assert_called_once()
    args, kwargs = input_ctrl.replay_input_trace.call_args
    assert args == (trace,)
    assert kwargs["canvas_width"] == 1920
    assert kwargs["canvas_height"] == 1080
