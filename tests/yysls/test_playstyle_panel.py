"""玩法配置面板：属性锁定、增伤/定音候选按武学收敛。"""
from __future__ import annotations

import pytest

from lvjiang.apps.yysls.ui.game_settings.playstyle_panel import PlaystylePanel


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants as constants
    import lvjiang.core.config.resolver as cr

    monkeypatch.setattr(cr, "LOCAL_CONFIG_DIR", tmp_path / "local")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(cr, "_resolver", None)
    monkeypatch.setenv("LVJIANG_DEV_MODE", "0")
    p = PlaystylePanel()
    qtbot.addWidget(p)
    return p


def _select(panel, name):
    for i in range(panel._list.count()):
        if panel._list.item(i).text() == name:
            panel._list.setCurrentRow(i)
            return
    raise AssertionError(f"玩法不存在: {name}")


def _combo_items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_bound_school_fills_and_locks_identity_fields(panel):
    """绑定流派后，属性与主副武学均来自流派权威配置。"""
    _select(panel, "纯唐")

    assert panel._combo_school.currentData() == "裂石·钧"
    assert panel._combo_attr.currentText() == "裂石"
    assert panel._combo_art_a.currentText() == "斩雪刀法"
    assert panel._combo_art_b.currentText() == "十方破阵"
    assert not panel._combo_attr.isEnabled()
    assert not panel._combo_art_a.isEnabled()
    assert not panel._combo_art_b.isEnabled()
    assert "锁定" in panel._hint.text()


def test_custom_school_unlocks_identity_fields(panel):
    """自定义玩法不受流派约束，属性和两门武学均可编辑。"""
    _select(panel, "纯唐")
    panel._combo_school.setCurrentIndex(0)

    assert panel._combo_school.currentData() == ""
    assert panel._combo_attr.isEnabled()
    assert panel._combo_art_a.isEnabled()
    assert panel._combo_art_b.isEnabled()
    assert "自定义" in panel._hint.text()


def test_changing_school_updates_and_persists_derived_fields(panel):
    """切换流派立即填充，并把新绑定及派生武器写回玩法。"""
    _select(panel, "纯唐")
    index = panel._combo_school.findData("鸣金·影")
    panel._combo_school.setCurrentIndex(index)

    cfg = next(e for e in panel._entries() if e["name"] == "纯唐")
    assert cfg["school"] == "鸣金·影"
    assert cfg["attr"] == "鸣金"
    assert cfg["arts"] == ["积矩九剑", "九曲惊神枪"]
    assert cfg["main_weapon"] == "剑"
    assert cfg["sub_weapon"] == "枪"


def test_damage_candidates_follow_the_weapon(panel):
    """增伤要求跟武器走：武学 → 武器 → 该武器的武学增伤词条。"""
    _select(panel, "纯唐")
    panel._combo_art_a.setCurrentText("斩雪刀法")    # 横刀

    assert _combo_items(panel._combo_damage_a) == ["", "横刀武学增伤"]


def test_defense_dingyin_filtered_by_school_group(panel):
    """按流派分组过滤，不能依赖武学名前缀。"""
    _select(panel, "樽樽")

    items = [x for x in _combo_items(panel._combo_defense) if x]
    assert "悬身断水·浓醺技能增伤" in items
    assert "断水双诀·轻击增伤" in items
    assert "悬身拳法·特殊技增伤" in items
    assert not any(x.startswith("无名剑法") for x in items)


def test_custom_playstyle_shows_all_defense_dingyin(panel):
    """自定义没有流派分组约束，应允许选择任意指定技能增效。"""
    _select(panel, "樽樽")
    panel._combo_school.setCurrentIndex(0)

    items = [x for x in _combo_items(panel._combo_defense) if x]
    assert "悬身断水·浓醺技能增伤" in items
    assert "无名剑法蓄力技增伤" in items


def test_descriptive_requirements_are_loaded_and_persisted(panel):
    """三项要求是受限选项的玩法说明元数据。"""
    _select(panel, "飞天玉")

    assert _combo_items(panel._combo_all_skill) == ["需要", "不需要"]
    assert _combo_items(panel._combo_qishu) == ["不需要", "群体", "单体"]
    assert _combo_items(panel._combo_unit) == ["不需要", "首领", "玩家"]
    assert panel._combo_qishu.currentText() == "不需要"

    panel._combo_qishu.setCurrentText("单体")
    cfg = next(e for e in panel._entries() if e["name"] == "飞天玉")
    assert cfg["qishu_requirement"] == "单体"


def test_deleting_a_referenced_playstyle_is_blocked(panel, monkeypatch):
    """规则还在引用时不能删——删了引用就悬空，那正是拆分要消灭的东西。"""
    warned = []
    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.game_settings.playstyle_panel.QMessageBox"
        ".warning", lambda *a, **k: warned.append(a[-1]))
    _select(panel, "纯唐")
    before = panel._list.count()

    panel._on_delete()

    assert warned and "引用" in warned[0]
    assert panel._list.count() == before
