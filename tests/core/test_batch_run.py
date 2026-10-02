"""运行草稿层：本次执行的勾选与顺序，以及定义变化后的协调规则。

这些规则是分层之后最容易悄悄出错的地方：配置组改了可见范围，用户刚在主页面
调好的本次顺序不该被清掉；而定义层删掉的条目必须真的消失。
"""
from lvjiang.core.batch_run import (
    BatchRunDraft,
    BatchSelection,
    active_group_id,
    forget_drafts,
    load_draft,
    save_draft,
    set_active_group_id,
)


def test_first_use_takes_definition_order_and_defaults():
    """空草稿 = 配置组第一次使用：按定义顺序列出，按默认勾选打勾。"""
    selection = BatchSelection()
    selection.reconcile(["a", "b", "c"], ["a", "c"])

    assert selection.order == ["a", "b", "c"]
    assert selection.checked == ["a", "c"]
    assert selection.execution_order() == ["a", "c"]


def test_reconcile_keeps_this_run_order_and_appends_new_candidates():
    """定义层新增条目，不能把用户刚排好的本次顺序推翻。"""
    selection = BatchSelection(order=["c", "a", "b"], checked=["c"])

    selection.reconcile(["a", "b", "c", "d"], ["a", "d"])

    assert selection.order == ["c", "a", "b", "d"], "已有条目保持本次顺序"
    assert selection.checked == ["c", "d"], "新条目按定义层默认勾选加入"


def test_reconcile_drops_candidates_that_no_longer_exist():
    """配置组里删掉、或属性值不存在了的条目必须消失，不能留在本次执行里。"""
    selection = BatchSelection(order=["a", "b", "c"], checked=["a", "b"])

    selection.reconcile(["b", "c"], ["c"])

    assert selection.order == ["b", "c"]
    assert selection.checked == ["b"], "仍存在的条目保留原勾选，不被默认值覆盖"


def test_unchecking_keeps_the_position():
    """order 与 checked 分开存的理由：取消勾选不该丢掉位置。"""
    selection = BatchSelection(order=["a", "b", "c"], checked=["a", "b", "c"])
    selection.checked = ["a", "c"]

    selection.reconcile(["a", "b", "c"], ["a", "b", "c"])

    assert selection.order == ["a", "b", "c"]
    assert selection.checked == ["a", "c"], "b 既没被勾上，也没被挪走"


def test_from_defaults_rebuilds_both_order_and_checked():
    """「恢复默认」要同时恢复定义顺序和默认勾选。"""
    selection = BatchSelection.from_defaults(["a", "b", "c"], ["b"])

    assert selection.order == ["a", "b", "c"]
    assert selection.checked == ["b"]


def test_units_are_isolated_per_attribute_key():
    """调度单元换来换去，各自的勾选要分别记得。"""
    draft = BatchRunDraft()
    draft.entry_selection("account").checked = ["acc1"]
    draft.entry_selection("role").checked = ["role1"]

    assert draft.entry_selection("account").checked == ["acc1"]
    assert draft.entry_selection("role").checked == ["role1"]
    assert draft.entry_selection("user") is draft.users


def test_draft_rounds_are_clamped_on_reconcile():
    draft = BatchRunDraft(rounds=0)
    draft.reconcile(
        task_candidates=[], task_defaults=[],
        entry_candidates=[], entry_defaults=[], unit_key="user")
    assert draft.rounds == 1

    draft.rounds = 10_000
    draft.reconcile(
        task_candidates=[], task_defaults=[],
        entry_candidates=[], entry_defaults=[], unit_key="user")
    assert draft.rounds == 999


def test_drafts_are_isolated_per_group_and_survive_round_trip():
    """草稿按配置组的稳定 ID 隔离，不同组互不影响。"""
    save_draft("gid-1", BatchRunDraft(
        tasks=BatchSelection(order=["a"], checked=["a"]),
        rounds=3, unattended=True))
    save_draft("gid-2", BatchRunDraft(rounds=7))

    first = load_draft("gid-1")
    assert first.tasks.checked == ["a"]
    assert first.rounds == 3
    assert first.unattended is True
    assert load_draft("gid-2").rounds == 7
    assert load_draft("gid-unknown") == BatchRunDraft(), "没草稿就是空草稿"


def test_active_group_is_session_state():
    """主页面当前配置组属于页面状态，不进 batch.json。"""
    assert active_group_id() == ""

    set_active_group_id("gid-1")

    assert active_group_id() == "gid-1"


def test_forget_drafts_prunes_deleted_groups():
    save_draft("gid-1", BatchRunDraft(rounds=2))
    save_draft("gid-2", BatchRunDraft(rounds=3))

    forget_drafts(["gid-2"])

    assert load_draft("gid-1") == BatchRunDraft()
    assert load_draft("gid-2").rounds == 3
