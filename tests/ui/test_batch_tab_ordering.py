from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QFormLayout, QPlainTextEdit

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem, BatchWorkflows
from lvjiang.core.profile.models import MODEL_QUOTA, QuotaKeyDef
from lvjiang.core.profile.schema import ProfileSchema
from lvjiang.ui.batch import batch_tab
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
    monkeypatch.setattr(batch_tab, "save_batch_config", lambda _cfg: None)
    if schema is not None:
        monkeypatch.setattr(batch_tab, "get_profile_config", lambda: schema)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", lambda _run_env: [],
    )


def test_main_batch_lists_preserve_and_update_actual_execution_order(
    monkeypatch, qtbot,
):
    config = BatchConfig(configs={
        "日常": BatchConfigItem(
            name="日常",
            task_ids=["a", "b", "c"],
            usernames=["用户A", "用户B", "用户C"],
            selected_task_ids=["b", "a"],
            selected_usernames=["用户B", "用户A"],
        ),
    }, active_config="日常")
    scripts = [
        {"id": task_id, "name": task_id, "batchable": True}
        for task_id in ("a", "b", "c")
    ]
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.save_batch_config", lambda _cfg: None)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts",
        lambda _run_env: scripts,
    )

    tab = BatchTab(_Host())
    qtbot.addWidget(tab)

    assert tab._checked_script_ids() == ["b", "a"]
    assert tab._get_enabled_usernames() == ["用户A", "用户B"]

    script_b = tab._script_list.takeTopLevelItem(0)
    tab._script_list.insertTopLevelItem(2, script_b)
    tab._on_script_rows_moved()
    user_a = tab._user_list.takeTopLevelItem(0)
    tab._user_list.insertTopLevelItem(2, user_a)
    tab._on_user_rows_moved()

    group = config.configs["日常"]
    assert group.task_ids == ["a", "b", "c"]
    assert group.usernames == ["用户A", "用户B", "用户C"]
    assert group.selected_task_ids == ["a", "b"]
    assert group.selected_usernames == ["用户B", "用户A"]
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
    assert group.selected_usernames == ["用户B", "用户A"]


def test_batch_multiline_parameter_uses_full_width_row(monkeypatch, qtbot):
    group = BatchConfigItem(
        name="日常",
        workflows=BatchWorkflows(prepare_item="batch/example.wf"),
    )
    config = BatchConfig({"日常": group}, "日常")
    _prepare_batch_tab(monkeypatch, config)
    monkeypatch.setattr(batch_tab, "lifecycle_parameter_definitions", lambda _wf: {
        "batch_setup": [],
        "prepare_item": [{
            "name": "activities",
            "type": "text",
            "label": "需要领取的活动名称（每行一个）",
            "multiline": True,
            "default": "朝夕共赏\n金秋共贺",
        }],
        "finish_item": [],
        "batch_teardown": [],
    })

    tab = BatchTab(_Host())
    qtbot.addWidget(tab)
    edit = tab._workflow_param_widgets[("prepare_item", "activities")]
    assert isinstance(edit, QPlainTextEdit)
    form = edit.parentWidget().layout()
    assert isinstance(form, QFormLayout)
    assert form.itemAt(0, QFormLayout.ItemRole.SpanningRole).widget().text() == (
        "需要领取的活动名称（每行一个）："
    )
    assert form.getWidgetPosition(edit) == (1, QFormLayout.ItemRole.SpanningRole)


def test_batch_profile_sort_uses_full_row_and_readable_popup(monkeypatch, qtbot):
    config = BatchConfig({"日常": BatchConfigItem(name="日常")}, "日常")
    long_label = "每周累计获得袅袅之音数量"
    schema = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="weekly_niaoniao_total", label=long_label)],
    })
    _prepare_batch_tab(monkeypatch, config, schema=schema)

    tab = BatchTab(_Host())
    qtbot.addWidget(tab)
    combo = tab._profile_sort_key
    combo.setCurrentIndex(1)
    expected = f"{long_label} (weekly_niaoniao_total)"

    assert combo.currentText() == expected
    assert combo.toolTip() == expected
    assert combo.view().minimumWidth() > combo.fontMetrics().horizontalAdvance(expected)
    summary_form = combo.parentWidget().parentWidget().layout()
    assert isinstance(summary_form, QFormLayout)
    assert summary_form.getWidgetPosition(combo.parentWidget()) == (
        2, QFormLayout.ItemRole.SpanningRole)
    assert summary_form.itemAt(1, QFormLayout.ItemRole.SpanningRole).widget().text() == (
        "指定排序："
    )


def test_user_profile_order_is_explicit_temporary_and_missing_quota_is_zero(
    monkeypatch, qtbot,
):
    group = BatchConfigItem(
        name="日常", usernames=["用户A", "用户B", "用户C"],
        selected_usernames=["用户A", "用户B", "用户C"],
    )
    config = BatchConfig({"日常": group}, "日常")
    schema = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="weekly_work", label="周进度")],
    })
    values = {"用户A": 4, "用户B": None, "用户C": 2}
    saves = []
    monkeypatch.setattr("lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.save_batch_config", saves.append)
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

    tab._profile_sort_key.setCurrentIndex(1)
    assert group.profile_sort_key == "weekly_work"
    assert tab._get_enabled_usernames() == ["用户A", "用户B", "用户C"]
    saves.clear()
    tab._user_list.profile_order_requested.emit()
    assert tab._get_enabled_usernames() == ["用户B", "用户C", "用户A"]
    assert group.selected_usernames == ["用户A", "用户B", "用户C"]
    assert group.usernames == ["用户A", "用户B", "用户C"]
    assert not saves

    tab._profile_sort_direction.setCurrentIndex(1)
    assert group.profile_sort_direction == "desc"
    saves.clear()
    tab._user_list.profile_order_requested.emit()
    assert tab._get_enabled_usernames() == ["用户A", "用户C", "用户B"]
    assert not saves
    tab._refresh_entry_list()
    assert tab._get_enabled_usernames() == ["用户A", "用户B", "用户C"]


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
        selected_usernames=["用户A", "用户B"],
    )
    config = BatchConfig({"日常": group}, "日常")
    schema = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="weekly_work", label="周进度")],
    })
    monkeypatch.setattr(batch_tab, "load_batch_config", lambda: config)
    monkeypatch.setattr(batch_tab, "save_batch_config", lambda _cfg: None)
    monkeypatch.setattr(batch_tab, "get_profile_config", lambda: schema)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", lambda _run_env: [],
    )
    tab = BatchTab(_Host())
    qtbot.addWidget(tab)

    # 1. 没有指定排序：这一项对当前配置组不适用，整条隐藏
    labels = [text for text, _enabled in _order_menu_actions(tab, monkeypatch)]
    assert not any("指定顺序排序" in text for text in labels), labels

    # 2. 选定定义后可用
    tab._profile_sort_key.setCurrentIndex(1)
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
