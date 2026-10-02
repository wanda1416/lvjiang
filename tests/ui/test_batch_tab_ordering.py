import copy

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.core.profile.models import MODEL_QUOTA, QuotaKeyDef
from lvjiang.core.profile.schema import ProfileSchema
from lvjiang.ui.batch import batch_tab
from lvjiang.ui.batch.batch_runner import BatchScript
from lvjiang.ui.batch.batch_tab import BatchTab


class _Hotkeys:
    start = "F9"
    pause = "F10"


class _UserConfig:
    hotkeys = _Hotkeys()


class _Host(QObject):
    automation_state_changed = pyqtSignal(str)
    _user_config = _UserConfig()
    is_running = False

    @staticmethod
    def _selected_run_env():
        return None


def _prepare_batch_tab(monkeypatch, config, *, schema=None):
    monkeypatch.setattr(batch_tab, "load_batch_config", lambda: config)
    if schema is not None:
        monkeypatch.setattr(batch_tab, "get_profile_config", lambda: schema)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", lambda _run_env: [],
    )


def test_attribute_start_requires_prepare_workflow(monkeypatch, qtbot):
    config = BatchConfig({
        "组": BatchConfigItem(name="组", usernames=["u1"],
                               execution_unit_key="account"),
    })
    _prepare_batch_tab(monkeypatch, config)
    tab = BatchTab(_Host())
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_get_enabled_usernames", lambda: ["a"])
    monkeypatch.setattr(tab, "_checked_scripts", lambda: [BatchScript("A", "A")])
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args:
                        warnings.append(_args[2]))
    tab._start_batch()
    assert "条目准备" in warnings[0]
    assert tab._progress_table.rowCount() == 0


def test_main_batch_lists_preserve_and_update_actual_execution_order(
    monkeypatch, qtbot,
):
    config = BatchConfig(configs={
        "日常": BatchConfigItem(
            name="日常",
            task_ids=["a", "b", "c"],
            usernames=["用户A", "用户B", "用户C"],
            default_task_ids=["b", "a"],
            default_usernames=["用户B", "用户A"],
        ),
    })
    scripts = [
        {"id": task_id, "name": task_id, "batchable": True}
        for task_id in ("a", "b", "c")
    ]
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts",
        lambda _run_env: scripts,
    )

    tab = BatchTab(_Host())
    qtbot.addWidget(tab)

    assert tab._unit_label.text() == "<b>选择执行单元</b>"
    assert tab._user_list.headerItem().text(0) == "单元候选（用户名）"
    # 初始顺序来自定义层的 task_ids / usernames；default_* 只是"默认勾哪些"
    # 的集合，不承载顺序——顺序是另一个字段的职责。
    assert tab._checked_script_ids() == ["a", "b"]
    assert tab._get_enabled_usernames() == ["用户A", "用户B"]

    script_b = tab._script_list.takeTopLevelItem(0)
    tab._script_list.insertTopLevelItem(2, script_b)
    tab._on_script_rows_moved()
    user_a = tab._user_list.takeTopLevelItem(0)
    tab._user_list.insertTopLevelItem(2, user_a)
    tab._on_user_rows_moved()

    # 主页面的拖拽只写运行草稿，定义层一个字都不动——这是分层的核心契约
    group = config.by_name("日常")
    assert group.task_ids == ["a", "b", "c"]
    assert group.usernames == ["用户A", "用户B", "用户C"]
    assert group.default_task_ids == ["b", "a"]
    assert group.default_usernames == ["用户B", "用户A"]
    assert tab._script_list.topLevelItem(0).text(1) == "1"
    assert tab._user_list.topLevelItem(0).text(1) == "1"

    tab._script_list.restore_order_requested.emit()
    tab._user_list.restore_order_requested.emit()

    assert [
        tab._script_id(tab._script_list.topLevelItem(index))
        for index in range(tab._script_list.topLevelItemCount())
    ] == ["a", "b", "c"]
    assert [
        tab._user_name(tab._user_list.topLevelItem(index))
        for index in range(tab._user_list.topLevelItemCount())
    ] == ["用户A", "用户B", "用户C"]
    assert tab._checked_script_ids() == ["a", "b"]
    assert tab._get_enabled_usernames() == ["用户A", "用户B"]

    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.random.shuffle",
        lambda values: values.reverse(),
    )
    tab._script_list.shuffle_order_requested.emit()
    tab._user_list.shuffle_order_requested.emit()

    assert tab._checked_script_ids() == ["b", "a"]
    assert tab._get_enabled_usernames() == ["用户B", "用户A"]
    assert group.task_ids == ["a", "b", "c"]
    assert group.usernames == ["用户A", "用户B", "用户C"]
    assert group.default_usernames == ["用户B", "用户A"]


def test_user_profile_order_is_explicit_temporary_and_missing_quota_is_zero(
    monkeypatch, qtbot,
):
    group = BatchConfigItem(
        name="日常", usernames=["用户A", "用户B", "用户C"],
        default_usernames=["用户A", "用户B", "用户C"],
    )
    config = BatchConfig({"日常": group})
    schema = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="weekly_work", label="周进度")],
    })
    values = {"用户A": 4, "用户B": None, "用户C": 2}
    monkeypatch.setattr("lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config)
    monkeypatch.setattr("lvjiang.ui.batch.batch_tab.get_profile_config", lambda: schema)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.profile_read",
        lambda username, _key: values[username],
    )
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", lambda _run_env: [],
    )
    tab = BatchTab(_Host())
    qtbot.addWidget(tab)
    assert not tab._script_list._profile_order
    assert tab._user_list._profile_order

    # 排序键属于定义层（在批量配置里选）；这里只执行一次排序
    group.profile_sort_key = "weekly_work"
    tab._refresh_params()
    assert tab._get_enabled_usernames() == ["用户A", "用户B", "用户C"]
    definition_before = copy.deepcopy(group)

    tab._user_list.profile_order_requested.emit()

    assert tab._get_enabled_usernames() == ["用户B", "用户C", "用户A"]
    assert group == definition_before, (
        "排序结果属于本次运行，定义层一个字都不能动")

    group.profile_sort_direction = "desc"
    tab._refresh_params()
    definition_before = copy.deepcopy(group)
    tab._user_list.profile_order_requested.emit()
    assert tab._get_enabled_usernames() == ["用户A", "用户C", "用户B"]
    assert group == definition_before
    # 排完的顺序留在运行草稿里：重建列表不该把用户刚排好的本次顺序丢掉
    tab._refresh_entry_list()
    assert tab._get_enabled_usernames() == ["用户A", "用户C", "用户B"]


def _order_menu_actions(tab, monkeypatch) -> list[tuple[str, bool]]:
    """弹一次用户页右键菜单，返回 [(菜单项文字, 是否可用)]。

    断言真实菜单内容而不是内部标志：可见性与置灰是这里唯一的用户可感知契约。
    """
    from PyQt6.QtCore import QPoint

    captured: list = []
    monkeypatch.setattr(
        batch_tab.QMenu, "exec",
        lambda self, *_args: captured.append(self), raising=False)
    tab._user_list.customContextMenuRequested.emit(QPoint(0, 0))
    assert captured, "右键菜单没有弹出"
    return [(action.text(), action.isEnabled())
            for action in captured[-1].actions()]


def test_profile_order_entry_hides_when_unset_and_greys_out_when_undefined(
    monkeypatch, qtbot,
):
    """未配置指定排序 → 隐藏；配置了但定义已删 → 置灰并说明原因。

    定义是在 Profile 编辑器里删的，批量页收不到通知，所以判定必须在菜单
    弹出时现算；缓存下来会让用户点到一个什么都不做的菜单项。
    """
    group = BatchConfigItem(
        name="日常", usernames=["用户A", "用户B"],
        default_usernames=["用户A", "用户B"],
    )
    config = BatchConfig({"日常": group})
    schema = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="weekly_work", label="周进度")],
    })
    monkeypatch.setattr(batch_tab, "load_batch_config", lambda: config)
    monkeypatch.setattr(batch_tab, "get_profile_config", lambda: schema)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", lambda _run_env: [],
    )
    tab = BatchTab(_Host())
    qtbot.addWidget(tab)

    # 1. 没有指定排序：这一项对当前配置组不适用，整条隐藏
    labels = [text for text, _enabled in _order_menu_actions(tab, monkeypatch)]
    assert not any("指定顺序排序" in text for text in labels), labels

    # 2. 配置组选定排序键后可用
    group.profile_sort_key = "weekly_work"
    actions = _order_menu_actions(tab, monkeypatch)
    assert ("指定顺序排序", True) in actions, actions

    # 3. 定义在别处被删除：保留但置灰，并在菜单项上给出原因
    monkeypatch.setattr(
        batch_tab, "get_profile_config", lambda: ProfileSchema(keys_by_model={}))
    actions = _order_menu_actions(tab, monkeypatch)
    entry = [(text, enabled) for text, enabled in actions
             if text.startswith("指定顺序排序")]
    assert entry, actions
    text, enabled = entry[0]
    assert enabled is False
    assert "Profile 定义已不存在" in text

    # 4. 一行都没有时同样不能点
    tab._user_list.clear()
    actions = _order_menu_actions(tab, monkeypatch)
    assert all(not enabled for _text, enabled in actions), actions


@pytest.mark.parametrize("unit_key", ["role", "account"])
def test_profile_sort_is_hidden_for_attribute_units_and_restored_for_user(
    monkeypatch, qtbot, unit_key,
):
    group = BatchConfigItem(
        name="日常", execution_unit_key=unit_key,
        usernames=["用户A"],
        profile_sort_key="weekly_work",
    )
    config = BatchConfig({"日常": group})
    schema = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="weekly_work", label="周进度")],
    })
    _prepare_batch_tab(monkeypatch, config, schema=schema)
    tab = BatchTab(_Host())
    qtbot.addWidget(tab)

    form = tab._summary_form
    assert not form.isRowVisible(tab._profile_sort_label)
    assert not any(
        "指定顺序排序" in text
        for text, _enabled in _order_menu_actions(tab, monkeypatch)
    )
    assert group.profile_sort_key == "weekly_work"

    # 定义层改了调度单元，走和配置窗口保存后一样的刷新路径：
    # 它会重新载入定义并按新候选协调草稿。
    group.execution_unit_key = "user"
    tab._refresh_group_contents()

    assert form.isRowVisible(tab._profile_sort_label)
    assert ("指定顺序排序", True) in _order_menu_actions(tab, monkeypatch)




# ─── 本次运行参数：轮数与无人值守都只写草稿 ───────────────

def test_rounds_and_unattended_go_to_the_run_draft(monkeypatch, qtbot):
    """轮数和"今晚没人看"都是本次启动的选择，不该写回配置组。"""
    from lvjiang.core.batch_config import BatchWorkflows
    from lvjiang.core.batch_run import load_draft

    group = BatchConfigItem(
        name="日常", usernames=["用户A"], default_usernames=["用户A"],
        workflows=BatchWorkflows(
            recover_unattended="batch/recover_to_login.wf"))
    config = BatchConfig({group.id: group})
    _prepare_batch_tab(monkeypatch, config)
    definition_before = copy.deepcopy(group)

    tab = BatchTab(_Host())
    qtbot.addWidget(tab)
    tab._rounds_spin.setValue(3)
    tab._unattended_check.setChecked(True)

    draft = load_draft(group.id)
    assert draft.rounds == 3
    assert draft.unattended is True
    assert group == definition_before, "定义层一个字都不能动"


def test_unattended_needs_a_recovery_workflow_from_the_definition(
    monkeypatch, qtbot,
):
    """没配异常恢复 wf 就不允许勾选：禁用并说明原因，而不是让它勾着不生效。"""
    group = BatchConfigItem(name="日常", usernames=["用户A"])
    _prepare_batch_tab(monkeypatch, BatchConfig({group.id: group}))

    tab = BatchTab(_Host())
    qtbot.addWidget(tab)

    assert tab._unattended_check.isEnabled() is False
    assert "异常恢复" in tab._unattended_check.toolTip()


def test_params_page_labels_are_left_aligned_rows(monkeypatch, qtbot):
    """参数页每一项都是「标签：控件」，标签左对齐。

    无人值守原来把文字写在勾选框自己身上、标签列留空，于是它和上下行的标签
    对不上，读起来像两张表。
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QFormLayout

    group = BatchConfigItem(name="日常", usernames=["用户A"])
    _prepare_batch_tab(monkeypatch, BatchConfig({group.id: group}))

    tab = BatchTab(_Host())
    qtbot.addWidget(tab)
    form = tab._summary_form

    labels = []
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        widget = item.widget() if item is not None else None
        labels.append(widget.text() if widget is not None else "")

    assert labels[:4] == ["执行轮数：", "调度单元：", "指定排序：", "无人值守："]
    assert all(labels), "每一行都要有标签，不能把文字写在控件自己身上"
    assert not tab._unattended_check.text(), (
        "文字归标签列，勾选框自己不再带文字")
    assert form.labelAlignment() & Qt.AlignmentFlag.AlignLeft
