"""批量页「恢复默认顺序」必须同时重算开始状态。

清空全部勾选后开始按钮变灰并提示「请到「脚本」页至少勾选一个脚本」；右键
「恢复默认顺序」把预置勾选恢复后，这条提示与灰按钮必须一起消失。

曾经的缺陷：两个「恢复默认」都是先 ``_save_draft()`` 再 ``_refresh_*_list()``，
而开始状态是在 ``_save_draft → _refresh_start_feedback`` 里算的 —— 那一刻
``_script_order`` / ``_user_order`` 还是重建前的空表，于是提示残留、按钮保持
灰色，只能靠切一下配置组才恢复。修好后重建列表的函数自己负责刷新开始状态，
调用顺序不再影响结果。
"""
import inspect
from types import SimpleNamespace

from PyQt6.QtWidgets import QTreeWidget

from lvjiang.core.batch_run import BatchRunDraft, BatchSelection
from lvjiang.ui.batch.batch_tab import BatchTab, _ReorderTreeWidget


def _make_list(qtbot) -> QTreeWidget:
    """脚本/单元列表都是两列（名称、顺序）的 _ReorderTreeWidget。"""
    tree = _ReorderTreeWidget()
    qtbot.addWidget(tree)
    tree.setColumnCount(2)
    return tree


def _bind(target: SimpleNamespace, *names: str) -> None:
    """把 BatchTab 的真实方法绑到替身上；staticmethod 保持原样不绑 self。"""
    for name in names:
        attribute = inspect.getattr_static(BatchTab, name)
        method = getattr(BatchTab, name)
        setattr(target, name,
                method if isinstance(attribute, staticmethod)
                else method.__get__(target))


def test_restore_default_scripts_clears_stale_start_hint(qtbot, monkeypatch):
    """恢复默认勾选后，开始状态在顺序重建之后重算，提示与灰按钮不残留。"""
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts",
        lambda run_env=None: [
            {"id": script_id, "name": script_id.upper(), "batchable": True}
            for script_id in ("a", "b")
        ])

    batch = SimpleNamespace(
        _item=SimpleNamespace(
            task_ids=["a", "b"], default_task_ids=["a", "b"]),
        # 用户把两个脚本都取消勾选了
        _draft=BatchRunDraft(
            tasks=BatchSelection(order=["a", "b"], checked=[])),
        _script_list=_make_list(qtbot),
        _script_candidate_order=["a", "b"],
        _script_order=[],
        _missing_script_ids=[],
        _script_configs_by_id={},
        _updating_script_list=False,
        _group_id="",
        _host=SimpleNamespace(append_log=lambda *_a: None,
                              _selected_run_env=lambda: "desktop"),
    )
    _bind(batch, "_refresh_script_list", "_restore_script_order", "_save_draft",
          "_start_reason", "_refresh_script_order_column",
          "_warn_missing_scripts", "_script_id")

    # 记录每次算开始状态时看到的执行顺序：修复前最后一次是空表。
    seen: list[list[str]] = []
    batch._refresh_start_feedback = lambda: seen.append(list(batch._script_order))

    assert BatchTab._start_reason(batch) != ""  # 前提：此刻确实拦着

    batch._restore_script_order()

    assert batch._script_order == ["a", "b"]
    assert seen[-1] == ["a", "b"], "重算开始状态时必须已经重建执行顺序"
    assert BatchTab._start_reason(batch) == ""


def test_restore_default_entries_clears_stale_start_hint(qtbot):
    """单元页同理：恢复默认勾选后不再提示「至少勾选一个执行单元」。"""
    batch = SimpleNamespace(
        _item=SimpleNamespace(execution_unit_key="user", usernames=["u1", "u2"]),
        _draft=BatchRunDraft(
            users=BatchSelection(order=["u1", "u2"], checked=[])),
        _user_list=_make_list(qtbot),
        _user_candidate_order=["u1", "u2"],
        _user_order=[],
        _updating_user_list=False,
        _group_id="",
        _host=SimpleNamespace(append_log=lambda *_a: None),
    )
    _bind(batch, "_refresh_entry_list", "_restore_user_order", "_save_draft",
          "_start_reason", "_refresh_user_order_column", "_entry_selection",
          "_user_name")
    batch._unit_candidates = lambda _item: ["u1", "u2"]
    batch._entry_defaults = lambda _item: ["u1", "u2"]

    seen: list[list[str]] = []
    batch._refresh_start_feedback = lambda: seen.append(list(batch._user_order))

    assert BatchTab._start_reason(batch) != ""  # 前提：此刻确实拦着

    batch._restore_user_order()

    assert batch._user_order == ["u1", "u2"]
    assert seen[-1] == ["u1", "u2"], "重算开始状态时必须已经重建执行顺序"
    assert BatchTab._start_reason(batch) == ""
