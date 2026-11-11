"""复制定义：Key / 标签及其余字段全部原样照抄原定义。

契约的核心是"漏一个字段就静默丢配置"，所以断言按 dataclasses.fields 逐个对齐，
而不是挑几个代表性字段——模型将来加字段时会立刻暴露。
"""

from __future__ import annotations

import dataclasses
from copy import deepcopy

from PyQt6.QtWidgets import QLineEdit, QPushButton, QTableWidgetItem

from lvjiang.core.profile.models import (
    MODEL_QUOTA,
    MODEL_REGEN,
    QuotaKeyDef,
    RegenKeyDef,
    StepDef,
    SyncTargetDef,
)
from lvjiang.i18n import tr
from lvjiang.ui.profile import settings_dialog
from lvjiang.ui.profile.settings_dialog import ProfileDefinitionDialog, _ModelTab

#: 由调用方单独决定或编辑器本就不写回的字段
_SKIP = {"key", "label", "group", "description", "raw"}

#: 同步目标的下拉只认当前 profile.yaml 里真实存在的 key，所以先摆一份最小定义
_CONFIG_YAML = """\
stock:
- key: energy
  label: 体力
  group: default
quota:
- key: src_key
  label: 原标签
  group: 甲组
  cap: 20
  period: week
"""


def _seed_config() -> None:
    from lvjiang.core.profile import schema

    path = schema._PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_CONFIG_YAML, encoding="utf-8")
    schema.reload_profile_config()


def _auto_accept(monkeypatch, key: str, label: str) -> None:
    """把模态 exec 换成「填好 Key / 标签后点保存」，并按 Accepted 返回。"""

    def fake_exec(dialog):
        dialog.findChild(QLineEdit, "key_input").setText(key)
        dialog.findChild(QLineEdit, "label_input").setText(label)
        for button in dialog.findChildren(QPushButton):
            if button.text() == tr("保存"):
                button.click()
                return 1
        raise AssertionError("找不到保存按钮")

    monkeypatch.setattr(settings_dialog.QDialog, "exec", fake_exec)
    # 保存会写 config/session/profile.yaml：测试只验证预填契约
    monkeypatch.setattr(
        ProfileDefinitionDialog, "_save_key_definition",
        staticmethod(lambda *args, **kwargs: None))


def _copy_of(qapp, monkeypatch, model_type, template, key="copied_key", label="副本"):
    _auto_accept(monkeypatch, key, label)
    return ProfileDefinitionDialog.open_key_editor(
        None, model_type, None, {template.key},
        initial_group=template.group, template=template)


def test_quota_copy_keeps_every_definition_field(qapp, monkeypatch) -> None:
    _seed_config()
    template = QuotaKeyDef(
        key="src_key", group="甲组", label="原标签", cap=20, soft=True,
        show_cap=True, decimal=True, sources=["日常"], uses=["消耗"],
        sync_targets=[SyncTargetDef(key="stock:energy", ratio=2.0,
                                    direction="both", source="日常",
                                    lock_on_cap=False)],
        change_script="scripts/on_change.py", change_script_sync=True,
        period="week", steps=[StepDef(value=5, source="日常")],
        reset_time="06:30", reset_day=5, increment_only=True)

    kd = _copy_of(qapp, monkeypatch, MODEL_QUOTA, template)

    assert kd is not None
    assert (kd.key, kd.label, kd.group) == ("copied_key", "副本", "甲组")
    for field in dataclasses.fields(QuotaKeyDef):
        if field.name in _SKIP or field.name == "steps":
            continue
        assert getattr(kd, field.name) == getattr(template, field.name), field.name
    assert [(step.value, step.source) for step in kd.steps] == [(5, "日常")]


def test_regen_copy_keeps_regen_and_alert_fields(qapp, monkeypatch) -> None:
    template = RegenKeyDef(
        key="src_regen", group="乙组", label="原再生", cap=20, sources=["太平武墓"],
        change_script="scripts/regen.py", regen_type="boundary",
        regen_amount=5.0, regen_period="week", regen_rate_value=1,
        regen_rate_unit="minute", reset_time="05:00", reset_day=5,
        alert_orange=15, alert_red=20)

    kd = _copy_of(qapp, monkeypatch, MODEL_REGEN, template, key="copied_regen")

    assert kd is not None
    assert (kd.key, kd.label, kd.group) == ("copied_regen", "副本", "乙组")
    for field in dataclasses.fields(RegenKeyDef):
        if field.name in _SKIP:
            continue
        assert getattr(kd, field.name) == getattr(template, field.name), field.name


def test_copy_prefills_key_and_label_verbatim(qapp, monkeypatch) -> None:
    """模板原样带出 Key 与标签，Key 仍可编辑，改名由用户自己决定。"""
    seen: dict[str, object] = {}

    def fake_exec(dialog):
        key_edit = dialog.findChild(QLineEdit, "key_input")
        label_edit = dialog.findChild(QLineEdit, "label_input")
        seen["key_text"] = key_edit.text()
        seen["key_editable"] = not key_edit.isReadOnly()
        seen["label_text"] = label_edit.text()
        return 0     # 取消：不落草稿

    monkeypatch.setattr(settings_dialog.QDialog, "exec", fake_exec)
    template = QuotaKeyDef(key="src_key", group="甲组", label="原标签", cap=7)

    result = ProfileDefinitionDialog.open_key_editor(
        None, MODEL_QUOTA, None, {"src_key"}, template=template)

    assert result is None
    assert seen == {"key_text": "src_key", "key_editable": True, "label_text": "原标签"}


def test_copy_keeps_original_key_but_cannot_save_it(qapp, monkeypatch) -> None:
    """沿用原 Key 保存会被拒绝，且不会写入 profile.yaml。"""

    def fake_exec(dialog):
        for button in dialog.findChildren(QPushButton):
            if button.text() == tr("保存"):
                button.click()
                return 0     # 保存被拒绝后关闭
        raise AssertionError("找不到保存按钮")

    monkeypatch.setattr(settings_dialog.QDialog, "exec", fake_exec)
    saved: list[object] = []
    monkeypatch.setattr(
        ProfileDefinitionDialog, "_save_key_definition",
        staticmethod(lambda *args, **kwargs: saved.append(args)))

    template = QuotaKeyDef(key="src_key", group="甲组", label="原标签", cap=7)
    result = ProfileDefinitionDialog.open_key_editor(
        None, MODEL_QUOTA, None, {"src_key"}, template=template)

    assert result is None
    assert saved == []


def test_context_menu_puts_duplicate_under_change_group(qapp, monkeypatch) -> None:
    tab = _ModelTab(MODEL_QUOTA)
    texts = [action.text() for action in tab._group_context_menu.actions()]
    assert texts[:2] == [tr("更改分组"), tr("复制定义")]

    # 多选时置灰：逐个弹编辑器没有可用的交互
    tab.table.setRowCount(2)
    tab.table.setItem(0, 0, QTableWidgetItem("a"))
    tab.table.setItem(1, 0, QTableWidgetItem("b"))
    monkeypatch.setattr(tab._group_context_menu, "exec", lambda *args: None)
    tab.table.selectAll()
    tab._show_context_menu(tab.table.visualItemRect(tab.table.item(0, 0)).center())
    assert not tab._duplicate_action.isEnabled()

    tab.table.clearSelection()
    tab.table.selectRow(0)
    tab._show_context_menu(tab.table.visualItemRect(tab.table.item(0, 0)).center())
    assert tab._duplicate_action.isEnabled()


def test_duplicate_key_appends_draft_and_marks_dirty(qapp, monkeypatch) -> None:
    """菜单命令的落点：新 key 作为草稿进列表、标记未保存、并定位到它。"""
    from lvjiang.core.profile.models import ALL_MODELS

    tab = _ModelTab(MODEL_QUOTA)
    tab.table.setRowCount(1)
    source_kd = QuotaKeyDef(key="src_key", group="甲组", label="原标签", cap=9)
    source_item = QTableWidgetItem("src_key")
    source_item.setData(settings_dialog._ROLE_KEYDEF, source_kd)
    tab.table.setItem(0, 0, source_item)

    dialog = ProfileDefinitionDialog.__new__(ProfileDefinitionDialog)
    dialog._tabs = {model_type: tab for model_type in ALL_MODELS}
    dialog._drafts = {model_type: [] for model_type in ALL_MODELS}
    dialog._baseline = {model_type: [] for model_type in ALL_MODELS}
    dialog._drafts[MODEL_QUOTA] = [source_kd]      # 已存在的定义仍在草稿里
    dialog._baseline[MODEL_QUOTA] = [deepcopy(source_kd)]
    dialog.has_saved_changes = False
    focused: list[tuple] = []
    dialog._refresh_model_tab = lambda model_type, group=None, key=None: focused.append(
        (model_type, group, key))

    copied = QuotaKeyDef(key="new_key", group="甲组", label="副标签", cap=9)
    seen: dict[str, object] = {}

    def fake_editor(_parent, model_type, existing, known_keys, *, initial_group, template):
        seen.update(model_type=model_type, existing=existing, known=known_keys,
                    group=initial_group, template=template)
        return copied

    monkeypatch.setattr(ProfileDefinitionDialog, "open_key_editor",
                        staticmethod(fake_editor))

    dialog._duplicate_key(MODEL_QUOTA, 0)

    assert seen["existing"] is None                       # 按「新增」流程走
    assert seen["known"] == {"src_key"}                   # 原 key 仍占用，不能重名
    assert seen["group"] == "甲组"                         # 落回原分组
    assert seen["template"].cap == 9                       # 预填来自原定义
    assert dialog._drafts[MODEL_QUOTA] == [source_kd, copied]
    assert [kd.key for kd in dialog._baseline[MODEL_QUOTA]] == ["src_key", "new_key"]
    assert dialog.has_saved_changes is True
    assert focused == [(MODEL_QUOTA, "甲组", "new_key")]
