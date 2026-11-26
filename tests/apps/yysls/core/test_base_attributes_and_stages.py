"""阶段 1 回归测试：预置基础属性分层（base_attributes）与毕业率模型多阶段（stage）。"""

from __future__ import annotations

import json

import pytest

from lvjiang.apps.yysls.config import (
    BaseAttributePreset,
    delete_base_attribute_preset,
    get_base_attribute_presets,
    make_preset_ref,
    parse_preset_ref,
    resolve_base_attribute_data,
    resolve_default_preset,
    save_base_attribute_preset,
)
from lvjiang.apps.yysls.core.graduation.model_registry import (
    available_stages,
    invalidate_model_registry,
    model_filename,
    select_graduation_model,
)
from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected


def _isolate_resolver(tmp_path, monkeypatch, *, dev_mode: bool = True) -> ConfigResolver:
    system = tmp_path / "system"
    local = tmp_path / "local"
    system.mkdir(parents=True, exist_ok=True)
    local.mkdir(parents=True, exist_ok=True)
    res = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=dev_mode,
        config_dir=tmp_path, track_content=False,
    )
    monkeypatch.setattr(resolver_module, "_resolver", res)
    invalidate_model_registry()
    return res


def test_base_attribute_presets_layer_merge_and_default_selection(tmp_path, monkeypatch):
    """验证 system/local 分层覆盖、跨服等级过滤与玩法优先/流派兜底解析。"""
    _isolate_resolver(tmp_path, monkeypatch, dev_mode=True)

    # 写入 system 层：110级 1阶段、115级 1阶段、115级 2阶段（默认 + 玩法专属）
    save_base_attribute_preset("牵丝·霖", BaseAttributePreset(
        id="110_s1", school="牵丝·霖", level=110, stage=1, playstyle=None,
        label="110级 1阶段", stats={"min_outer": 1000.0},
    ))
    save_base_attribute_preset("牵丝·霖", BaseAttributePreset(
        id="115_s1", school="牵丝·霖", level=115, stage=1, playstyle=None,
        label="115级 1阶段", stats={"min_outer": 1500.0},
    ))
    save_base_attribute_preset("牵丝·霖", BaseAttributePreset(
        id="115_s2", school="牵丝·霖", level=115, stage=2, playstyle=None,
        label="115级 2阶段默认", stats={"min_outer": 1700.0},
    ))
    save_base_attribute_preset("牵丝·霖", BaseAttributePreset(
        id="115_s2_纯奶", school="牵丝·霖", level=115, stage=2, playstyle="纯奶",
        label="115级 2阶段（纯奶）", stats={"min_outer": 1650.0, "crit_rate": 0.65},
    ))

    # local 层同 id 覆盖 115_s2
    save_base_attribute_preset("牵丝·霖", BaseAttributePreset(
        id="115_s2", school="牵丝·霖", level=115, stage=2, playstyle=None,
        label="115级 2阶段本地微调", stats={"min_outer": 1720.0},
    ), layer="local")

    presets = get_base_attribute_presets("牵丝·霖")
    assert [p.id for p in presets] == ["115_s2", "115_s2_纯奶", "115_s1", "110_s1"]
    assert presets[0].source == "local"
    assert presets[0].stats["min_outer"] == 1720.0

    # 未指定等级：自动取最高 level(115) -> 最高 stage(2)
    # 玩法「纯奶」命中专属条目；玩法「火拳」回落流派默认（且读到 local 覆盖值）
    p_heal = resolve_default_preset("牵丝·霖", playstyle="纯奶")
    assert p_heal is not None and p_heal.id == "115_s2_纯奶"
    p_fire = resolve_default_preset("牵丝·霖", playstyle="火拳")
    assert p_fire is not None and p_fire.id == "115_s2" and p_fire.stats["min_outer"] == 1720.0

    # 国际版落后等级（如 110 级）：自动匹配 <=110 的最高等级
    p_global = resolve_default_preset("牵丝·霖", playstyle="纯奶", level=110)
    assert p_global is not None and p_global.id == "110_s1"


def test_dual_source_resolution_and_user_mode_delete_protection(tmp_path, monkeypatch):
    """验证 preset:<id> 与 session/play_styles 裸名双源解析互不冲突，且用户模式不可删系统预置。"""
    res = _isolate_resolver(tmp_path, monkeypatch, dev_mode=True)
    save_base_attribute_preset("鸣金·虹", BaseAttributePreset(
        id="115_s1", school="鸣金·虹", level=115, stage=1, playstyle=None,
        label="115级 1阶段", stats={"max_outer": 5200.0},
    ))

    # 切换到用户模式
    res_user = ConfigResolver(
        system_dir=res.system_dir, local_dir=res.local_dir, dev_mode=False,
        config_dir=tmp_path, track_content=False,
    )
    monkeypatch.setattr(resolver_module, "_resolver", res_user)

    # 模拟 session/play_styles.json 里存在个人条目
    monkeypatch.setattr(
        "lvjiang.apps.yysls.config.play_styles.get_play_styles",
        lambda school: {"个人实测": {"max_outer": 4800.0}} if school == "鸣金·虹" else {},
    )

    ref = make_preset_ref("115_s1")
    assert parse_preset_ref(ref) == "115_s1"
    assert parse_preset_ref("个人实测") is None
    assert resolve_base_attribute_data("鸣金·虹", ref) == {"max_outer": 5200.0}
    assert resolve_base_attribute_data("鸣金·虹", "个人实测") == {"max_outer": 4800.0}

    # 用户模式直接删 system 预置必须被拦下
    with pytest.raises(SystemContentProtected):
        delete_base_attribute_preset("鸣金·虹", "115_s1")

    # 用户模式写 local 覆盖后再删除，应回落到 system 原值
    save_base_attribute_preset("鸣金·虹", BaseAttributePreset(
        id="115_s1", school="鸣金·虹", level=115, stage=1, playstyle=None,
        label="本地覆盖", stats={"max_outer": 5300.0},
    ))
    assert resolve_base_attribute_data("鸣金·虹", ref) == {"max_outer": 5300.0}
    assert delete_base_attribute_preset("鸣金·虹", "115_s1") is True
    assert resolve_base_attribute_data("鸣金·虹", ref) == {"max_outer": 5200.0}


def test_graduation_model_registry_supports_multi_stage_selection(tmp_path, monkeypatch):
    """验证毕业率模型按 (level, stage, version) 选型且兼容未标 stage 的存量文件。"""
    res = _isolate_resolver(tmp_path, monkeypatch, dev_mode=True)
    grad_dir = res.system_dir / "yysls" / "graduation" / "115级"
    grad_dir.mkdir(parents=True, exist_ok=True)

    # 存量文件不带 stage（兼容为 stage=1），v4
    (grad_dir / model_filename("鸣金·虹", "基础方案", 4)).write_text(json.dumps({
        "schema_version": 3, "school": "鸣金·虹", "scheme": "基础方案",
        "model_level": 115, "model_version": 4,
    }, ensure_ascii=False), encoding="utf-8")
    # 阶段 2 新表 v1
    (grad_dir / model_filename("鸣金·虹", "基础方案", 1, stage=2)).write_text(json.dumps({
        "schema_version": 3, "school": "鸣金·虹", "scheme": "基础方案",
        "model_level": 115, "stage": 2, "model_version": 1,
    }, ensure_ascii=False), encoding="utf-8")
    invalidate_model_registry()

    assert available_stages("鸣金·虹", "基础方案", level=115) == (1, 2)
    # 默认选最高阶段（stage=2 v1），而非 stage=1 v4
    latest = select_graduation_model("鸣金·虹", "基础方案", 115)
    assert latest is not None and (latest.level, latest.stage, latest.version) == (115, 2, 1)
    # 指定 stage=1 时选到 stage=1 v4
    s1 = select_graduation_model("鸣金·虹", "基础方案", 115, stage=1)
    assert s1 is not None and (s1.level, s1.stage, s1.version) == (115, 1, 4)


def test_graduation_model_default_base_attribute_roundtrip(tmp_path, monkeypatch):
    """方案绑定默认预置基础属性：写入、读取与清除。"""
    from lvjiang.apps.yysls.core.graduation import (
        invalidate_graduation_cache,
        set_graduation_default_base_attribute,
    )
    from lvjiang.apps.yysls.core.graduation.model_registry import available_models

    resolver = _isolate_resolver(tmp_path, monkeypatch, dev_mode=False)
    rel = "yysls/graduation/115级/鸣金·虹_基础方案_v1.json"
    sys_file = resolver.system_dir / rel
    sys_file.parent.mkdir(parents=True, exist_ok=True)
    sys_file.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "school": "鸣金·虹",
                "scheme": "基础方案",
                "model_level": 115,
                "model_version": 1,
                "stage": 1,
                "reference": {"dps": 10000.0},
                "graduation_baseline_dps": 10000.0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    invalidate_graduation_cache()

    refs = available_models("鸣金·虹", "基础方案")
    assert len(refs) == 1
    assert refs[0].default_base_attribute == ""

    set_graduation_default_base_attribute(
        "鸣金·虹", "基础方案", "preset:115_s1", model_ref=refs[0],
    )
    refs_after = available_models("鸣金·虹", "基础方案")
    assert refs_after[0].default_base_attribute == "preset:115_s1"

    set_graduation_default_base_attribute(
        "鸣金·虹", "基础方案", "", model_ref=refs_after[0],
    )
    refs_cleared = available_models("鸣金·虹", "基础方案")
    assert refs_cleared[0].default_base_attribute == ""


def test_school_panel_preset_list_scheme_default_col_and_card_height(qtbot, tmp_path, monkeypatch):
    """SchoolPanel 预置/个人分离列表、方案管理第3列默认属性与增益效果6行对齐。"""
    from PyQt6.QtWidgets import QGridLayout, QGroupBox

    from lvjiang.apps.yysls.config import (
        BaseAttributePreset,
        get_base_attribute_preset,
        save_base_attribute_preset,
    )
    from lvjiang.apps.yysls.ui.game_settings.school_panel import SchoolPanel

    _isolate_resolver(tmp_path, monkeypatch, dev_mode=True)
    save_base_attribute_preset(
        "鸣金·虹",
        BaseAttributePreset(
            school="鸣金·虹",
            id="115_s1",
            level=115,
            stage=1,
            playstyle=None,
            label="115级 1阶段（默认）",
            stats={"min_outer": 1417.815, "max_outer": 3053.374, "mingjin_pen": 36.0},
        ),
        layer="system",
    )

    panel = SchoolPanel(
        data={
            "schools": {
                "鸣金·虹": {
                    "attr": "鸣金",
                    "main": {"weapon": "剑", "martial_art": "无名剑法"},
                    "sub": {"weapon": "枪", "martial_art": "无名枪法"},
                }
            }
        }
    )
    qtbot.addWidget(panel)

    # 0. 右侧拆分为「流派方案」（首页）与「流派属性」两个子 Tab
    assert panel._sub_tabs.count() == 2
    assert panel._sub_tabs.tabText(0) == "流派方案"
    assert panel._sub_tabs.tabText(1) == "流派属性"
    assert panel._sub_tabs.currentIndex() == 0

    # 1. 方案管理 4 列，第 3 列为「默认属性」
    assert panel._scheme_list.columnCount() == 4
    assert panel._scheme_list.horizontalHeaderItem(2).text() == "默认属性"

    # 2. 预置基础属性与个人基础属性分离，且支持通过 preset:<id> 定位
    panel.select_school_base_attr("鸣金·虹", "preset:115_s1")
    assert panel._preset_list.count() == 1
    assert panel._preset_list.currentRow() == 0
    assert panel._ps_list.currentRow() == -1
    assert "[系统]" in panel._preset_list.item(0).text()

    # 3. 复用数值展示与实时编辑逻辑：改字段自动写回预置
    panel._ps_edits["max_outer"].setText("3100.00")
    reloaded = get_base_attribute_preset("鸣金·虹", "115_s1")
    assert reloaded is not None
    assert abs(reloaded.stats["max_outer"] - 3100.0) < 1e-4

    # 4. 攻击属性与增益效果卡片均保持 6 行（12 格），确保判定属性与伤害加成对齐
    cards = {
        box.title(): box
        for box in panel._value_content_widget.findChildren(QGroupBox)
    }
    attack_grid = cards["攻击属性"].layout()
    gain_grid = cards["增益效果"].layout()
    assert isinstance(attack_grid, QGridLayout)
    assert isinstance(gain_grid, QGridLayout)
    assert attack_grid.rowCount() == 6
    assert gain_grid.rowCount() == 6


def test_system_layer_read_and_label_formatting(tmp_path, monkeypatch):
    """存在同 ID local 覆盖时 layer='system' 仍返回系统原值，且格式化标签隐藏 preset: 前缀。"""
    from lvjiang.apps.yysls.config import (
        format_base_attribute_label,
        save_base_attribute_preset,
    )

    _isolate_resolver(tmp_path, monkeypatch, dev_mode=True)
    save_base_attribute_preset(
        "鸣金·虹",
        BaseAttributePreset(
            id="115_s1", school="鸣金·虹", level=115, stage=1, playstyle=None,
            label="系统默认", stats={"min_outer": 1417.815},
        ),
        layer="system",
    )
    save_base_attribute_preset(
        "鸣金·虹",
        BaseAttributePreset(
            id="115_s1", school="鸣金·虹", level=115, stage=1, playstyle=None,
            label="本地微调", stats={"min_outer": 1500.0},
        ),
        layer="local",
    )

    sys_only = get_base_attribute_presets("鸣金·虹", layer="system")
    assert len(sys_only) == 1
    assert sys_only[0].source == "system"
    assert sys_only[0].stats["min_outer"] == pytest.approx(1417.815)

    merged = get_base_attribute_presets("鸣金·虹")
    assert len(merged) == 1
    assert merged[0].source == "local"

    assert format_base_attribute_label("鸣金·虹", "preset:115_s1") == "[本地预置] 本地微调"
    assert format_base_attribute_label("鸣金·虹", "preset:missing") == "[预置] missing"
    assert format_base_attribute_label("鸣金·虹", "个人实测") == "个人实测"


def test_school_panel_single_field_edit_preserves_unmodified_precision_and_user_guard(
    qtbot, tmp_path, monkeypatch,
):
    """单字段编辑不截断未改动字段的三位小数；用户模式下系统预置右键删除禁用。"""
    from PyQt6.QtWidgets import QMenu

    from lvjiang.apps.yysls.config import (
        get_base_attribute_preset,
        save_base_attribute_preset,
    )
    from lvjiang.apps.yysls.ui.game_settings.school_panel import SchoolPanel

    _isolate_resolver(tmp_path, monkeypatch, dev_mode=True)
    save_base_attribute_preset(
        "鸣金·虹",
        BaseAttributePreset(
            id="115_s1", school="鸣金·虹", level=115, stage=1, playstyle=None,
            label="115级 1阶段（默认）",
            stats={"min_outer": 1417.815, "max_outer": 3053.374, "mingjin_bonus": 0.15},
        ),
        layer="system",
    )

    panel = SchoolPanel(
        data={
            "schools": {
                "鸣金·虹": {
                    "attr": "鸣金",
                    "main": {"weapon": "剑", "martial_art": "无名剑法"},
                    "sub": {"weapon": "枪", "martial_art": "无名枪法"},
                }
            }
        }
    )
    qtbot.addWidget(panel)
    panel.select_school_base_attr("鸣金·虹", "preset:115_s1")

    # 只改 max_outer，未改动的 min_outer 必须保留 1417.815 三位小数
    panel._ps_edits["max_outer"].setText("3100.00")
    reloaded = get_base_attribute_preset("鸣金·虹", "115_s1")
    assert reloaded is not None
    assert reloaded.stats["min_outer"] == pytest.approx(1417.815, abs=1e-6)
    assert reloaded.stats["max_outer"] == pytest.approx(3100.0, abs=1e-6)
    assert reloaded.stats["mingjin_bonus"] == pytest.approx(0.15, abs=1e-6)

    # 切到用户模式，系统预置右键删除应禁用而不弹窗报错
    _isolate_resolver(tmp_path, monkeypatch, dev_mode=False)
    save_base_attribute_preset(
        "鸣金·虹",
        BaseAttributePreset(
            id="115_s1", school="鸣金·虹", level=115, stage=1, playstyle=None,
            label="115级 1阶段（默认）",
            stats={"min_outer": 1417.815},
        ),
        layer="local",
    )
    # 先测试纯 system 条目的右键守卫
    delete_base_attribute_preset("鸣金·虹", "115_s1")
    panel._refresh_presets()
    rect = panel._preset_list.visualItemRect(panel._preset_list.item(0))
    captured: list[tuple[str, bool]] = []

    def fake_exec(menu_self, _pos):
        for act in menu_self.actions():
            captured.append((act.text(), act.isEnabled()))
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    panel._on_preset_context_menu(rect.center())
    assert any("删除" in text and not enabled for text, enabled in captured)


def test_ondevice_loadout_view_exposes_readable_base_attribute_options(tmp_path, monkeypatch):
    """手机端备战视图返回可读的 base_attribute_label 与 base_attribute_options。"""
    from lvjiang import constants
    from lvjiang.apps.yysls.config import get_game_config
    from lvjiang.apps.yysls.core.loadout.models import LoadoutPlan, LoadoutState
    from lvjiang.apps.yysls.ondevice import loadout_view
    from lvjiang.core.ondevice import task_runner

    preset = BaseAttributePreset(
        id="115_s1", school="鸣金·虹", level=115, stage=1, playstyle=None,
        label="115级 1阶段（默认）", stats={"min_outer": 1400.0}, source="system",
    )
    monkeypatch.setattr(
        "lvjiang.apps.yysls.config.base_attributes.get_base_attribute_presets",
        lambda school, **_kwargs: [preset] if school == "鸣金·虹" else [],
    )
    monkeypatch.setattr(constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(task_runner, "_local_user_names", lambda: ["viewer"])
    monkeypatch.setattr("lvjiang.apps.yysls.config.play_styles.get_play_styles", lambda _s: {})

    gc = get_game_config()
    arts = gc.get_schools()["鸣金·虹"]
    plan = LoadoutPlan(
        id="p1", name="默认",
        main_martial_art=arts["main"]["martial_art"],
        sub_martial_art=arts["sub"]["martial_art"],
        base_attribute="preset:115_s1",
        graduation_scheme="基础方案",
    )
    state = LoadoutState(world_level=115, active_plan_id="p1", plans={"p1": plan}, equipment_items={})
    (tmp_path / "viewer.loadouts.json").write_text(
        json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8",
    )

    result = json.loads(loadout_view.plan_view("viewer", "p1"))
    assert result["ok"]
    assert result["base_attribute"] == "preset:115_s1"
    assert result["base_attribute_label"] == "[预置] 115级 1阶段（默认）"
    assert result["base_attribute_options"] == [
        {"value": "preset:115_s1", "label": "[预置] 115级 1阶段（默认）"},
    ]


def test_shipped_115_preset_is_stage_2():
    """随包 115 级系统预置基础属性应明确为 stage 2（115_s2），不留 s1。"""
    presets = get_base_attribute_presets("鸣金·虹", layer="system")
    assert [p.id for p in presets] == ["115_s2"]
    assert presets[0].level == 115
    assert presets[0].stage == 2


@pytest.mark.parametrize("preset_mode", [False, True])
def test_play_style_edit_dialog_layout_alignment_and_empty_defaults(qtbot, preset_mode):
    """创建基础属性与新建预置弹窗：空白无 0.0000、无滚动条包裹内容、第 2 列左端对齐。"""
    from PyQt6.QtWidgets import QDoubleSpinBox, QGridLayout, QLabel, QScrollArea

    from lvjiang.apps.yysls.ui.game_settings.school_panel import _PlayStyleEditDialog

    dlg = _PlayStyleEditDialog(school_attr="鸣金", preset_mode=preset_mode)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.layout() is not None
    dlg.layout().activate()

    # 1. 无 QScrollArea、无 QDoubleSpinBox，尺寸紧贴 sizeHint
    assert dlg.findChildren(QScrollArea) == []
    assert dlg.findChildren(QDoubleSpinBox) == []
    assert dlg.size() == dlg.sizeHint()

    # 2. 初始输入框全为空串，不填即按空 dict 处理
    assert dlg._edits
    assert all(edit.text() == "" for edit in dlg._edits.values())
    assert dlg.get_attrs() == {}

    # 3. 填入数值与百分比，回读与再打开均无多余尾零
    dlg._edits["min_outer"].setText("1417.815")
    dlg._edits["mingjin_bonus"].setText("15")
    attrs = dlg.get_attrs()
    assert attrs == {"min_outer": 1417.815, "mingjin_bonus": 0.15}

    reopened = _PlayStyleEditDialog(
        attrs=attrs, school_attr="鸣金", preset_mode=preset_mode,
    )
    qtbot.addWidget(reopened)
    assert reopened._edits["min_outer"].text() == "1417.815"
    assert reopened._edits["mingjin_bonus"].text() == "15"
    assert reopened._edits["max_outer"].text() == ""

    # 4. 第 2 列全部标签在同一列左端对齐
    grids = [g for g in dlg.findChildren(QGridLayout)]
    assert len(grids) == 1
    grid = grids[0]
    col4_labels = [
        grid.itemAtPosition(row, 4).widget()
        for row in range(grid.rowCount())
        if grid.itemAtPosition(row, 4) is not None
    ]
    assert len(col4_labels) >= 6
    assert all(isinstance(lbl, QLabel) for lbl in col4_labels)
    x_positions = {lbl.geometry().x() for lbl in col4_labels}
    assert len(x_positions) == 1
    col4_texts = [lbl.text() for lbl in col4_labels]
    for expected_prefix in ("最大", "直接会心率", "属攻穿透", "会意伤害加成"):
        assert any(expected_prefix in text for text in col4_texts)
