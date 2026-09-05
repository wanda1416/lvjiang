"""伤害建模：系数表的解析、同源校验与写回。

这份配置是毕业率编译程序的可读参考层——程序能算，但读不出「第一道
剑气的外功倍率是 1.3066」。所以这里验的是「读得对、写得回、过期看得
出来」，不验伤害数值：求值不在这个模块里。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lvjiang.apps.yysls.core.damage import (
    DamageModelError,
    DamageModelManager,
    parse_model,
)

_MINIMAL = {
    "school": "鸣金·虹",
    "scheme": "基础方案",
    "source": {"file": "x.xlsx", "version": "2.0", "sha256": "abc"},
    "skills": {
        "第一道剑气": {
            "kind": "剑", "charge": True, "qi_ratio": 1.1,
            "outer_ratio": 1.3066, "outer_fixed": 361,
            "attr_ratio": 1.9598, "attr_fixed": 197,
            "modifiers": {"generic": 0.2},
        },
        "尚未测量": {"kind": "剑"},
    },
    "buffs": {"远程笛": {"generic": 0.2}, "N/a占位": {}},
}


def test_parse_reads_the_four_coefficients() -> None:
    model = parse_model(_MINIMAL, filename="鸣金·虹.yaml")

    skill = model.skill("第一道剑气")
    assert skill is not None
    assert (skill.outer_ratio, skill.outer_fixed) == (1.3066, 361.0)
    assert (skill.attr_ratio, skill.attr_fixed) == (1.9598, 197.0)
    assert skill.charge is True and skill.kind == "剑"


def test_a_skill_with_no_coefficients_counts_as_unfilled() -> None:
    """四个系数全 0 就是没填。建模进度得能走到 100%，否则没人知道还差什么。"""
    model = parse_model(_MINIMAL, filename="鸣金·虹.yaml")

    assert model.progress() == (1, 2)


@pytest.mark.parametrize("payload", [
    {"skills": {}},                                          # 缺 school
    {"school": "x", "顶层拼错": 1},
    {"school": "x", "skills": {"甲": {"打错的字段": 1}}},
    {"school": "x", "skills": {"甲": {"modifiers": {"没这个字段": 1}}}},
    {"school": "x", "skills": {"甲": {"force": {"强制什么": True}}}},
    {"school": "x", "skills": {"甲": {"outer_ratio": "不是数值"}}},
    {"school": "x", "skills": {"甲": {"charge": 1}}},         # 必须是布尔
    {"school": "x", "buffs": {"甲": {"没这个字段": 1}}},
])
def test_parse_rejects_malformed_config(payload: dict) -> None:
    """写错了要当场知道：静默跳过一个拼错的字段，只会让人对着一个
    不动的数字找半天。"""
    with pytest.raises(DamageModelError):
        parse_model(payload, filename="x.yaml")


@pytest.fixture
def models_dir(tmp_path):
    (tmp_path / "鸣金·虹.yaml").write_text(
        yaml.dump(_MINIMAL, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return DamageModelManager(tmp_path), tmp_path


def test_saving_a_skill_persists_and_reloads(models_dir) -> None:
    manager, _ = models_dir

    manager.save_skill("鸣金·虹", "尚未测量", {"kind": "剑", "outer_ratio": 2.0})

    skill = manager.model("鸣金·虹").skill("尚未测量")
    assert skill.outer_ratio == 2.0
    assert manager.model("鸣金·虹").progress() == (2, 2)


def test_saving_an_invalid_skill_leaves_the_file_alone(models_dir) -> None:
    """整份文件先过一遍解析再落盘：单条看着合法、放进文件却打不开的话，
    界面会提示保存成功，然后整个流派的系数表全废。"""
    manager, path = models_dir
    before = (path / "鸣金·虹.yaml").read_text(encoding="utf-8")

    with pytest.raises(DamageModelError):
        manager.save_skill("鸣金·虹", "尚未测量", {"modifiers": {"没这个字段": 1}})

    assert (path / "鸣金·虹.yaml").read_text(encoding="utf-8") == before


def test_a_scheme_from_another_workbook_is_reported_as_out_of_sync(
    models_dir,
) -> None:
    """比 sha256 而不是文件名：改过表内容却没改文件名，正是两边悄悄分家
    的时刻。"""
    manager, path = models_dir
    (path / "鸣金·虹_基础方案.json").write_text(
        json.dumps({"source": {"sha256": "另一份表"}}), encoding="utf-8")
    manager.reload()

    assert "不同源" in manager.mismatched("鸣金·虹")


def test_a_missing_scheme_is_reported_too(models_dir) -> None:
    manager, _ = models_dir

    assert "找不到配套方案" in manager.mismatched("鸣金·虹")


# ── 与仓库里那份真实配置 ──────────────────────────────────

def test_the_shipped_model_matches_its_graduation_scheme() -> None:
    """仓库里的伤害模型必须与配套方案同源，否则页面上的系数会静静过期。"""
    from lvjiang.apps.yysls.core.damage import get_damage_model_manager

    manager = get_damage_model_manager()
    for school in manager.schools():
        assert manager.mismatched(school) == "", school


def test_the_three_sword_qi_coefficients_come_straight_from_the_workbook() -> None:
    """三道剑气是同一招的 1 : 1.2 : 1.4，`三剑气` 恰好是三者之和——
    整条管线对倍率是线性的，这条关系错了说明抽取抽歪了。"""
    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load(
        (root / "config/system/yysls/damage_model/鸣金·虹.yaml")
        .read_text(encoding="utf-8"))
    skills = data["skills"]

    first = skills["第一道剑气"]["outer_ratio"]
    assert skills["第二道剑气"]["outer_ratio"] == pytest.approx(first * 1.2, abs=5e-4)
    assert skills["第三道剑气"]["outer_ratio"] == pytest.approx(first * 1.4, abs=5e-4)
    assert skills["三剑气"]["outer_ratio"] == pytest.approx(
        sum(skills[f"第{n}道剑气"]["outer_ratio"] for n in "一二三"))
