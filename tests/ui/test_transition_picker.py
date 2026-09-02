"""转移目标选择器：三级级联与取值往返。"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QDialogButtonBox, QFormLayout, QWidget

from lvjiang.core.scene_definition_models import RegionDef, SceneDef, ViewDef
from lvjiang.ui.scene_editor.entity_edit_form import add_dialog_action_row
from lvjiang.ui.scene_editor.scene_select import (
    SceneAreaReferencePicker,
    TransitionPicker,
    add_transition_row,
)


class _FakeRegistry:
    def __init__(self):
        self._scenes = {
            "bag_detail": SceneDef(
                key="bag_detail", name="背包",
                views=[ViewDef("base", "基底")],
                regions=[RegionDef(key="food", name="狗粮"),
                         RegionDef(key="shared", name="重名区域")]),
            "equip_tune_detail": SceneDef(
                key="equip_tune_detail", name="调律",
                views=[ViewDef("base", "基底"), ViewDef("result", "结果")]),
            "general_control": SceneDef(
                key="general_control", name="通用控件",
                regions=[RegionDef(key="confirm", name="确认")]),
            "jianghu_card": SceneDef(key="jianghu_card", name="卡片",
                                     type="subscene"),
        }
        self._groups = [("bag", "背包"), ("common", "通用")]
        self._group_scenes = {
            "bag": ["bag_detail", "equip_tune_detail"],
            "common": ["general_control", "jianghu_card"],
        }

    def get_groups(self):
        return list(self._groups)

    def get_group_scenes(self, gk):
        return list(self._group_scenes.get(gk, []))

    def get_scene(self, key):
        return self._scenes.get(key)

    def all_scenes(self):
        return dict(self._scenes)

    def get_scene_views(self, key):
        scene = self._scenes.get(key)
        return list(scene.views) if scene else []


@pytest.fixture
def registry(monkeypatch):
    reg = _FakeRegistry()
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_select.get_registry", lambda: reg)
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_select.get_scene_views",
        reg.get_scene_views)
    return reg


def _picker(qtbot, current="equip_tune_detail", value=""):
    p = TransitionPicker(current, value)
    qtbot.addWidget(p)
    return p


@pytest.mark.parametrize("value", [
    "",                        # 不跳转
    "bag_detail",              # 跨场景，基底视图
    "/result",                 # 本场景换视图
    "/base",                   # 本场景回基底（close_btn 这类）
    "general_control",         # 无多视图的场景
])
def test_value_round_trips(qtbot, registry, value):
    """选择器不平铺场景视图，但取值必须和写进 YAML 的形式一致。"""
    picker = _picker(qtbot, value=value)
    assert picker.value() == value


def test_cross_scene_base_target_is_canonicalized(qtbot, registry):
    """跨场景指向基底时省掉 /base，与不带视图的写法归一。

    否则没动过转移的 area 一经编辑就会把 to: bag_detail 改写成
    to: bag_detail/base，制造纯噪声 diff。
    """
    assert _picker(qtbot, value="bag_detail/base").value() == "bag_detail"


def test_same_scene_base_is_not_confused_with_no_transition(qtbot, registry):
    """本场景回基底是一条真实的边，不能被规范化成"不跳转"。"""
    picker = _picker(qtbot, value="/base")
    assert picker.value() == "/base" != ""


def test_selecting_a_scene_defaults_to_base_view(qtbot, registry):
    picker = _picker(qtbot)
    picker._group.setCurrentIndex(picker._group.findData("bag"))
    picker._scene.setCurrentIndex(picker._scene.findData("equip_tune_detail"))

    assert picker._view.currentData() == "base"
    # 本场景内跳转写成 /view，不重复场景名
    assert picker.value() == "/base"


@pytest.mark.parametrize("view_key,expected", [
    ("base", "/base"),
    ("result", "/result"),
])
def test_local_view_shortcut_selects_target_in_one_action(
        qtbot, registry, view_key, expected):
    picker = _picker(qtbot)
    action = next(
        action for action in picker._local_views_button.menu().actions()
        if action.data() == view_key)

    action.trigger()

    assert picker.value() == expected
    assert picker._scene.currentData() == "equip_tune_detail"


def test_local_view_shortcut_is_disabled_for_single_view_scene(
        qtbot, registry):
    picker = _picker(qtbot, current="general_control")

    assert not picker._local_views_button.isEnabled()


def test_local_view_shortcut_sits_left_of_dialog_buttons(
        qtbot, registry):
    host = QWidget()
    qtbot.addWidget(host)
    form = QFormLayout(host)

    picker = add_transition_row(form, "equip_tune_detail")
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel
    )
    add_dialog_action_row(
        form, buttons, leading_button=picker._local_views_button)

    transition_row, _role = form.getWidgetPosition(picker)
    assert transition_row == 0
    action_layout = form.itemAt(
        1, QFormLayout.ItemRole.SpanningRole).widget().layout()
    shortcut_index = action_layout.indexOf(picker._local_views_button)
    buttons_index = action_layout.indexOf(buttons)
    assert shortcut_index >= 0
    assert shortcut_index < buttons_index
    assert picker._local_views_button.text() == "本场景视图"
    assert form.labelForField(picker).text() == "跳转:"


def test_no_transition_disables_the_lower_levels(qtbot, registry):
    picker = _picker(qtbot, value="bag_detail")
    picker._group.setCurrentIndex(0)          # 不跳转

    assert picker.value() == ""
    assert not picker._scene.isEnabled()


def test_subscenes_are_not_offered(qtbot, registry):
    """子场景坐标相对外框，不是页面切换的目标。"""
    picker = _picker(qtbot)
    picker._group.setCurrentIndex(picker._group.findData("common"))

    offered = {picker._scene.itemData(i) for i in range(picker._scene.count())}
    assert offered == {"general_control"}


def test_area_reference_uses_group_scene_area_cascade(qtbot, registry):
    host = QWidget()
    qtbot.addWidget(host)
    form = QFormLayout(host)

    picker = SceneAreaReferencePicker(
        current_scene="equip_tune_detail", taken_keys={"shared"})
    form.addRow("来源:", picker)

    assert form.rowCount() == 1
    assert form.labelForField(picker).text() == "来源:"
    assert picker.layout().indexOf(picker.group) == 0
    assert picker.layout().indexOf(picker.scene) == 1
    assert picker.layout().indexOf(picker.area) == 2
    assert picker.value() == ("bag_detail", "food")

    picker.group.setCurrentIndex(picker.group.findData("common"))
    assert picker.scene.currentData() == "general_control"
    assert picker.area.currentData() == "confirm"


def test_non_clickable_entity_disables_transition_controls(qtbot, registry):
    picker = _picker(qtbot, value="bag_detail")

    picker.set_transition_enabled(False)

    assert not picker.isEnabled()
    assert not picker._local_views_button.isEnabled()
