"""批量配置对话框：定义层的唯一入口。

分层之后这个窗口写什么、不写什么是整套方案的关键：它只改 `batch.json` 里的
配置组定义，既不碰主页面的运行草稿（那在 session 里），也不再需要"读盘把别人
拥有的字段搬回来"那套反向合并。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFormLayout, QPlainTextEdit, QWidget

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem, BatchWorkflows
from lvjiang.core.batch_run import active_group_id, set_active_group_id
from lvjiang.core.user_config import User
from lvjiang.ui.batch.batch_config_dialog import BatchConfigDialog


class _Users:
    @staticmethod
    def list_users() -> list[str]:
        return ["用户A"]

    @staticmethod
    def get_user(name: str) -> User:
        return User(name, attributes={"account": "账号A"})


def _dialog(monkeypatch, qtbot, config, *, saved=None, parent=None):
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config",
        lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.save_batch_config",
        saved.append if saved is not None else (lambda _cfg: None))
    dialog = BatchConfigDialog(_Users(), parent)
    qtbot.addWidget(dialog)
    return dialog


def _group(**kwargs) -> BatchConfigItem:
    kwargs.setdefault("name", "组")
    return BatchConfigItem(**kwargs)


def test_two_columns_separate_visibility_from_default_selection(
    monkeypatch, qtbot,
):
    """可见与默认勾选是两件事，各占一列，分别落到两个字段。

    上一版一行只有一个勾选框，于是它同时表达"出现在主页面"和"默认要跑"，
    主页面的一次临时取消勾选就会写回配置组。
    """
    group = _group(
        task_ids=["a", "b"], usernames=["用户A"],
        default_task_ids=["a"], default_usernames=["用户A"])
    saved = []
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts",
        lambda run_env=None: [
            {"id": "a", "name": "任务 A", "batchable": True},
            {"id": "b", "name": "任务 B", "batchable": True},
        ])
    dialog = _dialog(
        monkeypatch, qtbot, BatchConfig({group.id: group}), saved=saved)

    rows = [dialog._task_list.topLevelItem(i) for i in range(2)]
    assert [row.checkState(0) for row in rows] == [Qt.CheckState.Checked] * 2
    assert [row.checkState(1) for row in rows] == [
        Qt.CheckState.Checked, Qt.CheckState.Unchecked]

    # 把第二个任务也设为默认执行
    rows[1].setCheckState(1, Qt.CheckState.Checked)
    dialog._on_save()

    stored = saved[0].by_name("组")
    assert stored.task_ids == ["a", "b"]
    assert stored.default_task_ids == ["a", "b"]


def test_hiding_an_item_also_drops_it_from_default_selection(
    monkeypatch, qtbot,
):
    """取消可见后，"默认执行"必须跟着清掉——不可见的条目谈不上默认跑。"""
    group = _group(usernames=["用户A"], default_usernames=["用户A"])
    saved = []
    dialog = _dialog(
        monkeypatch, qtbot, BatchConfig({group.id: group}), saved=saved)

    row = dialog._user_list.topLevelItem(0)
    assert row.checkState(1) == Qt.CheckState.Checked
    row.setCheckState(0, Qt.CheckState.Unchecked)

    assert row.checkState(1) == Qt.CheckState.Unchecked
    dialog._on_save()
    stored = saved[0].by_name("组")
    assert stored.usernames == []
    assert stored.default_usernames == []


def test_dispatch_unit_and_profile_sort_are_edited_here(monkeypatch, qtbot):
    """调度单元与排序键属于定义层，入口就在这个窗口。

    它们决定主页面候选列表的内容和生命周期契约，不是"本次启动"的选择。
    """
    group = _group(usernames=["用户A"])
    saved = []
    dialog = _dialog(
        monkeypatch, qtbot, BatchConfig({group.id: group}), saved=saved)

    index = dialog._unit_combo.findData("account")
    assert index >= 0, "候选键来自可见用户资料的非空属性"
    dialog._unit_combo.setCurrentIndex(index)
    dialog._on_save()

    assert saved[0].by_name("组").execution_unit_key == "account"


def test_single_user_passthrough_is_disabled_and_unchecked_for_attribute_units(
    monkeypatch, qtbot,
):
    """属性单元下"单用户直通"不适用：禁用**并且不勾选**。

    属性单元的生命周期始终执行，一个灰着却勾着的框字面意思正好相反——这是上一版
    那个"灰着、勾着、没有任何理由"的框真正的根治：理由就在相邻一行，状态也不再
    说反话。
    """
    group = _group(usernames=["用户A"], execution_unit_key="account",
                   skip_lifecycle_for_single_item=True)
    saved = []
    dialog = _dialog(
        monkeypatch, qtbot, BatchConfig({group.id: group}), saved=saved)

    assert dialog._skip_single_lifecycle.isEnabled() is False
    assert dialog._skip_single_lifecycle.isChecked() is False
    assert "属性" in dialog._skip_single_lifecycle.toolTip()

    # 展示成未勾选不等于用户取消了它：存储值必须原样留着
    dialog._on_save()
    assert saved[0].by_name("组").skip_lifecycle_for_single_item is True

    dialog._unit_combo.setCurrentIndex(dialog._unit_combo.findData("user"))

    assert dialog._skip_single_lifecycle.isEnabled() is True
    assert dialog._skip_single_lifecycle.isChecked() is True, (
        "切回用户名要恢复原来的选择，而不是停在被强制取消的状态")


def test_turning_off_passthrough_still_saves_for_user_units(
    monkeypatch, qtbot,
):
    """用户名单元下这一项照常可编辑可保存——把门不能把正常路径也挡掉。"""
    group = _group(usernames=["用户A"], skip_lifecycle_for_single_item=True)
    saved = []
    dialog = _dialog(
        monkeypatch, qtbot, BatchConfig({group.id: group}), saved=saved)

    assert dialog._skip_single_lifecycle.isEnabled() is True
    dialog._skip_single_lifecycle.setChecked(False)
    dialog._on_save()

    assert saved[0].by_name("组").skip_lifecycle_for_single_item is False


def test_lifecycle_parameters_are_edited_next_to_their_workflow(
    monkeypatch, qtbot,
):
    """生命周期参数跟着它所属的 wf：wf 在这里选，参数也在这里填。"""
    group = _group(
        usernames=["用户A"],
        workflows=BatchWorkflows(finish_item="batch/finish_item.wf"))
    saved = []
    dialog = _dialog(
        monkeypatch, qtbot, BatchConfig({group.id: group}), saved=saved)

    widget = dialog._workflow_param_widgets[("finish_item", "stop_app")]
    widget.setChecked(True)
    dialog._on_save()

    assert saved[0].by_name("组").workflow_params["finish_item"] == {
        "stop_app": True}


def test_rename_keeps_position_and_identity(monkeypatch, qtbot):
    """按稳定 ID 索引：重命名不动位置，草稿与历史的关联也不断。"""
    first = BatchConfigItem(name="第一组")
    second = BatchConfigItem(name="第二组")
    config = BatchConfig({first.id: first, second.id: second})
    dialog = _dialog(monkeypatch, qtbot, config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.QInputDialog.getText",
        staticmethod(lambda *a, **kw: ("新名称", True)))

    dialog._config_combo.setCurrentIndex(1)
    dialog._on_rename_config()

    assert dialog._cfg.names() == ["第一组", "新名称"]
    assert dialog._current_id == second.id
    assert dialog._cfg.by_name("新名称") is second


def test_visibility_lists_support_select_all_and_none(monkeypatch, qtbot):
    group = _group(usernames=["用户A"])
    dialog = _dialog(monkeypatch, qtbot, BatchConfig({group.id: group}))

    dialog._set_all_checked(dialog._user_list, False)
    rows = [dialog._user_list.topLevelItem(i)
            for i in range(dialog._user_list.topLevelItemCount())]
    assert all(row.checkState(0) == Qt.CheckState.Unchecked for row in rows)

    dialog._set_all_checked(dialog._user_list, True)
    assert all(row.checkState(0) == Qt.CheckState.Checked for row in rows)
    assert all(row.checkState(1) == Qt.CheckState.Checked for row in rows), (
        "全选同时打开默认执行，否则全选完主页面还是一个都不跑")


def test_batch_config_lists_other_environment_scripts(monkeypatch, qtbot):
    class DesktopParent(QWidget):
        @staticmethod
        def _selected_run_env():
            return "desktop"

    group = _group(usernames=["用户A"])
    requested_envs = []

    def exposed(run_env=None):
        requested_envs.append(run_env)
        return [{
            "id": "android_task", "name": "安卓任务", "wf_file": "task.wf",
            "batchable": True, "env": ["android"],
        }] if run_env is None else []

    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", exposed)
    parent = DesktopParent()
    qtbot.addWidget(parent)
    dialog = _dialog(
        monkeypatch, qtbot, BatchConfig({group.id: group}), parent=parent)

    assert requested_envs == [None]
    assert dialog._task_list.topLevelItemCount() == 1
    assert dialog._task_list.topLevelItem(0).text(0) == "安卓任务"

    # 这里的 parent 只为验证"对话框不看宿主的运行环境"而存在。它是局部的顶层
    # 控件，用例结束就被回收，顺带销毁作为子对象的对话框；而 qtbot 之后还要
    # 关一次。断开父子关系让对话框自己独立存活到 teardown。
    dialog.setParent(None)


def test_switching_editor_group_does_not_change_main_active_group(
    monkeypatch, qtbot,
):
    """切换编辑对象只换草稿，不动主页面的活动组。

    活动组现在是 session 里的页面状态，这个窗口根本没有写它的路径——
    从结构上就不可能越界，而不是靠保存时小心地搬回来。
    """
    main_group = BatchConfigItem(name="主页面组")
    edited = BatchConfigItem(name="编辑组")
    set_active_group_id(main_group.id)
    saved = []
    dialog = _dialog(
        monkeypatch, qtbot,
        BatchConfig({main_group.id: main_group, edited.id: edited}),
        saved=saved)

    dialog._config_combo.setCurrentIndex(1)
    assert dialog._current_id == edited.id

    dialog._on_save()

    assert active_group_id() == main_group.id
    assert "active_group" not in saved[0].to_dict()


# ─── 生命周期参数面板（随 wf 一起搬到这个窗口） ───────────

def _params_dialog(monkeypatch, qtbot, group, definitions):
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.lifecycle_parameter_definitions",
        lambda _wf: definitions)
    return _dialog(monkeypatch, qtbot, BatchConfig({group.id: group}))


def test_multiline_parameter_uses_full_width_row(monkeypatch, qtbot):
    group = _group(workflows=BatchWorkflows(prepare_item="batch/example.wf"))
    dialog = _params_dialog(monkeypatch, qtbot, group, {
        "prepare_item": [{
            "name": "activities",
            "type": "text",
            "label": "需要领取的活动名称（每行一个）",
            "multiline": True,
            "default": "朝夕共赏\n金秋共贺",
        }],
    })

    edit = dialog._workflow_param_widgets[("prepare_item", "activities")]
    assert isinstance(edit, QPlainTextEdit)
    form = edit.parentWidget().layout()
    assert isinstance(form, QFormLayout)
    assert form.itemAt(
        0, QFormLayout.ItemRole.SpanningRole).widget().text() == (
        "需要领取的活动名称（每行一个）：")
    assert form.getWidgetPosition(edit) == (
        1, QFormLayout.ItemRole.SpanningRole)


def test_dependent_parameter_row_follows_require(monkeypatch, qtbot):
    """require 不满足时整行隐藏；勾上上游开关立刻出现，值不丢。"""
    group = _group(workflows=BatchWorkflows(prepare_item="batch/example.wf"))
    dialog = _params_dialog(monkeypatch, qtbot, group, {
        "prepare_item": [
            {"name": "skip_online_role", "type": "bool",
             "label": "角色在线跳过", "default": True},
            {"name": "online_role_max_wait", "type": "number",
             "label": "最大等待时间（秒）", "default": 30, "min": 0, "max": 3600,
             "require": "not $skip_online_role"},
        ],
    })

    skip = dialog._workflow_param_widgets[("prepare_item", "skip_online_role")]
    wait = dialog._workflow_param_widgets[
        ("prepare_item", "online_role_max_wait")]
    form = wait.parentWidget().layout()
    assert isinstance(form, QFormLayout)
    wait_row = form.getWidgetPosition(wait)[0]

    assert not form.isRowVisible(wait_row)
    skip.setChecked(False)
    assert form.isRowVisible(wait_row)
    # 隐藏不丢值：保存时照旧收集该参数
    skip.setChecked(True)
    assert not form.isRowVisible(wait_row)
    assert wait.value() == 30
    assert dialog._collect_workflow_params()["prepare_item"][
        "online_role_max_wait"] == 30


def test_recovery_workflow_parameters_get_a_group_label(monkeypatch, qtbot):
    """异常恢复 wf 声明的参数也要能编辑。

    它不在主页面那几行只读摘要里，但照样是生命周期阶段——参数分组的标签表
    漏掉这个 key 就是一个 KeyError。这里用真实的 lifecycle_parameter_definitions，
    所以随包恢复 wf 真的声明了参数这件事也一并守住。
    """
    group = _group(
        workflows=BatchWorkflows(
            recover_unattended="batch/recover_to_login.wf"))
    dialog = _dialog(monkeypatch, qtbot, BatchConfig({group.id: group}))

    widget = dialog._workflow_param_widgets[
        ("recover_unattended", "max_roll_account")]
    assert widget.parentWidget().title() == "异常恢复"


def test_profile_sort_reuses_the_overview_three_level_picker(
    monkeypatch, qtbot,
):
    """指定排序的 key 用和「用户总览 → 新增列」同一套三级选择。

    Profile key 多起来之后平铺的下拉框根本看不完也选不动；同一个概念在两个
    地方给两种选法，等于逼用户学两次。
    """
    from lvjiang.core.profile.models import MODEL_QUOTA, MODEL_STOCK, QuotaKeyDef
    from lvjiang.core.profile.schema import ProfileSchema

    schema = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [
            QuotaKeyDef(key="weekly_a", label="每周甲", group="周常"),
            QuotaKeyDef(key="weekly_b", label="每周乙", group="周常"),
        ],
        MODEL_STOCK: [QuotaKeyDef(key="coin", label="铜钱")],
    })
    monkeypatch.setattr(
        "lvjiang.core.profile.schema.get_profile_config", lambda: schema)
    group = _group(usernames=["用户A"])
    saved = []
    dialog = _dialog(
        monkeypatch, qtbot, BatchConfig({group.id: group}), saved=saved)

    menu = dialog._profile_sort_key.build_key_menu()
    top = [action.text() for action in menu.actions() if action.text()]
    assert "不指定" in top, "要能取消排序，否则选错了没法退回"
    submenus = [action.menu() for action in menu.actions()
                if action.menu() is not None]
    assert submenus, top
    # 第一层是模型类型，第二层是定义自己的分组
    quota_menu = submenus[0]
    assert [a.text() for a in quota_menu.actions()] == ["周常"]
    leaf = quota_menu.actions()[0].menu()
    assert [a.text() for a in leaf.actions()] == [
        "每周甲 (weekly_a)", "每周乙 (weekly_b)"]

    leaf.actions()[1].trigger()
    dialog._on_save()

    assert saved[0].by_name("组").profile_sort_key == "weekly_b"
    assert "每周乙" in dialog._profile_sort_key.text()


def test_sort_row_has_no_extra_margin_and_direction_popup_fits(
    monkeypatch, qtbot,
):
    """指定排序这一行要和上下行左边缘对齐，升/降序弹出列表要能完整显示。

    它比别的行多包了一层 QWidget 才能放两个控件，默认边距会让整行右缩一截。
    """
    group = _group(usernames=["用户A"])
    dialog = _dialog(monkeypatch, qtbot, BatchConfig({group.id: group}))

    assert dialog._profile_sort_widget.layout().contentsMargins().left() == 0
    assert dialog._sort_key_slot.contentsMargins().left() == 0
    combo = dialog._profile_sort_direction
    view = combo.view()
    longest = max(combo.fontMetrics().horizontalAdvance(combo.itemText(i))
                  for i in range(combo.count()))
    assert view.minimumWidth() > longest, "弹出列表窄于最长项就会截断文字"


def test_lifecycle_rows_share_one_form_and_say_workflow(monkeypatch, qtbot):
    """调度单元到异常恢复共用一个表单，输入框左边缘才会对齐。

    分成两个 QFormLayout 时，各自按自己最长的标签算列宽，视觉上就会错开一截；
    这不是靠调间距能稳住的，所以直接钉「同一个 layout」这个机制。
    """
    from PyQt6.QtCore import Qt

    group = _group(usernames=["用户A"])
    dialog = _dialog(monkeypatch, qtbot, BatchConfig({group.id: group}))
    form = dialog._scope_form

    labels = []
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        widget = item.widget() if item is not None else None
        labels.append(widget.text() if widget is not None else "")

    assert labels == [
        "调度单元：", "指定排序：",
        "批次准备工作流：", "条目准备工作流：", "条目收尾工作流：",
        "批次收尾工作流：",
        "",  # 单个执行单元时跳过生命周期：勾选框占字段列，与输入框同一左边缘
        "异常恢复工作流：",
    ], labels
    assert all("wf" not in label for label in labels), labels
    assert form.labelAlignment() & Qt.AlignmentFlag.AlignLeft
    assert form.getWidgetPosition(dialog._skip_single_lifecycle) == (
        6, QFormLayout.ItemRole.FieldRole)
