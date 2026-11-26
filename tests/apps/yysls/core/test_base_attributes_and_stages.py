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
