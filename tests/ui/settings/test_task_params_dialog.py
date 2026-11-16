"""工具 → 任务参数：双视图、全局参数项、跳过声明与列表样式。"""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QKeyEvent
from PyQt6.QtWidgets import QLineEdit, QSpinBox

from lvjiang.core.config.wf_configs import get_wf_config
from lvjiang.core.users.document import (
    User,
    get_user_skipped_tasks,
    get_user_workflow_params,
    save_user_metadata,
    set_user_skipped_task,
    set_user_workflow_params,
)
from lvjiang.ui.settings.task_params_dialog import TaskParamsDialog
from lvjiang.ui.widgets.theme import get_theme_manager

PARAMETERS = [
    {"name": "text", "type": "text", "default": ""},
    {"name": "count", "type": "number", "default": 3},
]

CONFIGS = [
    {"id": "a", "name": "任务甲", "parameters": PARAMETERS, "scope": "daily"},
    {"id": "b", "name": "任务乙", "parameters": PARAMETERS, "scope": "daily"},
    # 专用脚本由专属页面管理，不应出现在本工具里。
    {"id": "ded", "name": "专用任务", "parameters": PARAMETERS, "scope": "dedicated"},
]


class Owner:
    def __init__(self, users_dir):
        self._user_manager = SimpleNamespace(
            _users_dir=users_dir, list_users=lambda: ["alpha", "beta"])
        self._workflow_configs = CONFIGS

    def _selected_run_env(self):
        return "desktop"


@pytest.fixture
def dialog(qtbot, tmp_path):
    users_dir = tmp_path / "users"
    for name in ("alpha", "beta"):
        save_user_metadata(User(name), users_dir)
    widget = TaskParamsDialog(Owner(users_dir))
    qtbot.addWidget(widget)
    return widget


def _row_of(dialog, key: str) -> int:
    for index in range(dialog._list.count()):
        item = dialog._list.item(index)
        if str(item.data(Qt.ItemDataRole.UserRole) or "") == key:
            return index
    raise AssertionError(f"列表里没有 {key}")


def test_task_view_lists_global_params_first_and_lists_daily_tasks_only(dialog):
    assert dialog._list.item(0).text() == "全局参数"
    names = [dialog._list.item(i).text() for i in range(dialog._list.count())]
    assert names == ["全局参数", "alpha", "beta"]
    task_names = [dialog._task_combo.itemText(i)
                  for i in range(dialog._task_combo.count())]
    assert "专用任务" not in task_names


def test_global_entry_edits_global_parameters(dialog):
    dialog._list.setCurrentRow(0)
    assert dialog._current == ("global", "a")
    # 全局伪条目没有"该用户"语义，两个开关都不适用。
    assert dialog._skip_check.isHidden()
    assert dialog._override_check.isHidden()
    count = dialog._form.widget_for("count")
    assert isinstance(count, QSpinBox)
    count.setValue(7)
    assert get_wf_config("a") == {"count": "7"}


def test_skip_declaration_disables_parameters_and_hides_global_entry(dialog, tmp_path):
    users_dir = tmp_path / "users"
    dialog._list.setCurrentRow(_row_of(dialog, "alpha"))
    assert dialog._current == ("alpha", "a")

    dialog._skip_check.setChecked(True)
    assert "a" in get_user_skipped_tasks("alpha", users_dir)
    # 跳过时置灰但保留值，且不把展示值当成编辑结果写回。
    assert not dialog._form.is_editable()
    assert get_user_workflow_params("alpha", "a", users_dir) is None

    # 用户视图：左侧是本用户的全部日常任务，不再出现「全局参数」。
    dialog._user_view_radio.setChecked(True)
    labels = [dialog._list.item(i).text() for i in range(dialog._list.count())]
    assert all("全局参数" not in label for label in labels)
    assert len(labels) == 2


def test_list_marks_override_bold_and_skip_greyed(dialog, tmp_path):
    """独立参数加粗、声明跳过用灰色字；颜色之外还有显式的跳过标记。"""
    users_dir = tmp_path / "users"
    set_user_workflow_params("alpha", "a", {"count": "5"}, users_dir)
    set_user_skipped_task("beta", "a", True, users_dir)
    dialog._reload_list(keep=False)

    alpha = dialog._list.item(_row_of(dialog, "alpha"))
    beta = dialog._list.item(_row_of(dialog, "beta"))

    assert alpha.font().bold()
    assert not beta.font().bold()
    assert beta.foreground().color() == QColor(
        get_theme_manager().tokens.text_disabled)
    assert beta.text().startswith("⊘")


def test_step_buttons_and_arrow_keys_follow_active_view_combo(dialog):
    """上一个/下一个切的是当前视图顶部那个下拉，两端禁用；←/→ 同效。"""
    # 任务视图：从第一个任务往后翻。
    assert dialog._task_combo.currentData() == "a"
    assert not dialog._previous_button.isEnabled()
    dialog._next_button.click()
    assert dialog._task_combo.currentData() == "b"
    assert dialog._current == ("global", "b")
    assert not dialog._next_button.isEnabled()

    # 用户视图：同一个按钮切的是用户。
    dialog._user_view_radio.setChecked(True)
    assert dialog._user_combo.currentData() == "alpha"
    dialog._next_button.click()
    assert dialog._user_combo.currentData() == "beta"
    assert dialog._current[0] == "beta"

    # 方向键与按钮同动作；文本输入里仍然是光标移动（这里只验证对话框级）。
    dialog.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left,
                                   Qt.KeyboardModifier.NoModifier))
    assert dialog._user_combo.currentData() == "alpha"
    assert isinstance(dialog._form.widget_for("text"), QLineEdit)
