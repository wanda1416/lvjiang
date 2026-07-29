"""装备识别测试面板控件测试（pytest-qt）

覆盖 EquipAffixEditor 的候选池/去重/默认承音值/装备构造，
以及 TuningConfigWidget 的配置读写往返。
"""

import pytest

from src.apps.yysls.game_config import get_game_config
from src.apps.yysls.ui.equip_judge_dialog import (
    _NONE_ITEM, EQUIP_TYPES, EquipAffixEditor,
)
from src.apps.yysls.ui.tuning_config_widget import TuningConfigWidget


def _combo_items(combo) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


@pytest.fixture
def editor(qtbot):
    w = EquipAffixEditor()
    qtbot.addWidget(w)
    return w


# ─── EquipAffixEditor ──────────────────────────────────────

class TestEquipAffixEditor:
    def test_part_list_covers_16_types(self, editor):
        assert _combo_items(editor.part_combo) == EQUIP_TYPES
        assert len(EQUIP_TYPES) == 16

    def test_weapon_wuxue_only_on_matching_weapon(self, editor):
        # 神力仅在调律词条（2-5）候选中，且仅限对应武器
        editor.part_combo.setCurrentText("剑")
        items = _combo_items(editor._affix_combos[1])
        assert "剑武学增伤" in items
        assert "横刀武学增伤" not in items
        assert "全武学增效" not in items
        editor.part_combo.setCurrentText("横刀")
        items = _combo_items(editor._affix_combos[1])
        assert "横刀武学增伤" in items
        assert "剑武学增伤" not in items

    def test_part_specific_pool(self, editor):
        cases = {
            "环": "全武学增效",
            "冠胄": "单体类奇术增伤",
            "胫甲": "对首领单位增伤",
        }
        for part, affix in cases.items():
            editor.part_combo.setCurrentText(part)
            assert affix in _combo_items(editor._affix_combos[1])
            # 增伤类神力不会是首词条
            assert affix not in _combo_items(editor._affix_combos[0])

    def test_initial_pool_follows_doc(self, editor):
        # 词条1（初始词条）候选遵循 01-equipment-system.md 三.1 各部位初始池
        editor.part_combo.setCurrentText("剑")
        items = _combo_items(editor._affix_combos[0])
        assert "最大无相攻击" in items
        assert "会心率" not in items       # 武器初始池无三率
        assert "最大鸣金攻击" not in items  # 初始只出无相，无四属攻
        editor.part_combo.setCurrentText("环")
        items = _combo_items(editor._affix_combos[0])
        assert items == [_NONE_ITEM, "最大外功攻击", "最小外功攻击"]
        editor.part_combo.setCurrentText("胫甲")
        items = _combo_items(editor._affix_combos[0])
        assert "劲" in items and "气血最大值" in items
        assert "势" not in items            # 胫/腕初始池无势
        # 词条 2-5 仍为通用调律池
        assert "会心率" in _combo_items(editor._affix_combos[1])
        assert "最大鸣金攻击" in _combo_items(editor._affix_combos[1])

    def test_part_change_clears_selection(self, editor):
        editor._affix_combos[0].setCurrentText("最大外功攻击")
        assert editor._affix_spins[0].value() > 0
        editor.part_combo.setCurrentText("环")
        assert editor._affix_combos[0].currentText() == _NONE_ITEM
        assert editor._affix_spins[0].value() == 0

    def test_selected_value_defaults_to_chengyin(self, editor):
        editor._affix_combos[1].setCurrentText("会心率")
        caps = get_game_config().get_affix_caps(110, "会心率")
        assert editor._affix_spins[1].value() == caps["chengyin"]

    def test_rows_2_to_5_dedup(self, editor):
        editor._affix_combos[1].setCurrentText("会心率")
        # 其余 2-5 行候选排除已选，自身保留
        assert "会心率" not in _combo_items(editor._affix_combos[2])
        assert editor._affix_combos[1].currentText() == "会心率"

    def test_row_1_not_restricted(self, editor):
        # 词条1 允许与 2-5 重复（冠胄 会心率×2 场景）
        editor.part_combo.setCurrentText("冠胄")
        editor._affix_combos[1].setCurrentText("会心率")
        assert "会心率" in _combo_items(editor._affix_combos[0])
        editor._affix_combos[0].setCurrentText("会心率")
        equip = editor.get_equipment()
        assert [a.name for a in equip.affixes] == ["会心率", "会心率"]

    def test_get_equipment_none_without_affix(self, editor):
        assert editor.get_equipment() is None

    def test_get_equipment_structure(self, editor):
        editor.part_combo.setCurrentText("剑")
        editor.quality_combo.setCurrentText("紫色")
        editor._affix_combos[0].setCurrentText("最大外功攻击")
        editor._affix_combos[2].setCurrentText("会心率")  # 跳过词条2
        equip = editor.get_equipment()
        assert equip.type == "剑"
        assert equip.level == 110
        assert equip.quality == "purple"
        assert equip.name == "测试装备"
        assert [a.name for a in equip.affixes] == ["最大外功攻击", "会心率"]
        assert equip.affixes[1].unit == "%"
        assert equip.affixes[0].value > 0


# ─── TuningConfigWidget ────────────────────────────────────

class TestTuningConfigWidget:
    def test_set_get_roundtrip(self, qtbot):
        w = TuningConfigWidget()
        qtbot.addWidget(w)
        cfg = {
            "huiyi_general": {"enabled": True},
            "lieshi_big": {"enabled": True, "playstyles": ["双切"]},
        }
        w.set_config(cfg)
        result = w.get_config()
        assert result["huiyi_general"]["enabled"]
        # 缺 playstyles 键 = 全选
        assert result["huiyi_general"]["playstyles"] == ["会意"]
        assert result["lieshi_big"]["playstyles"] == ["双切"]
        assert not result["lieshi_small"]["enabled"]

    def test_default_all_playstyles_checked(self, qtbot):
        # 初始状态：玩法全部勾选（缺省 = 全部）
        w = TuningConfigWidget()
        qtbot.addWidget(w)
        result = w.get_config()
        assert result["lieshi_big"]["playstyles"] == ["纯唐", "双切", "威威"]
        assert result["heal_pure"]["playstyles"] == ["纯奶"]

    def test_set_config_does_not_emit(self, qtbot):
        w = TuningConfigWidget()
        qtbot.addWidget(w)
        fired = []
        w.config_changed.connect(lambda: fired.append(1))
        w.set_config({"huiyi_general": {"enabled": True}})
        w.set_switches({"keep_pvp": True})
        assert not fired

    def test_switches_roundtrip(self, qtbot):
        # 全局开关独立于规则配置读写（注册表驱动）
        w = TuningConfigWidget()
        qtbot.addWidget(w)
        assert w.get_switches() == {"keep_pvp": False}
        w.set_switches({"keep_pvp": True})
        assert w.get_switches() == {"keep_pvp": True}
        assert "keep_pvp" not in w.get_config()["huiyi_general"]

    def test_group_titles_and_checkboxes(self, qtbot):
        # 分组标题 = 规则名；玩法复选框文本含主副武器摘要
        w = TuningConfigWidget()
        qtbot.addWidget(w)
        for key, title in (("heal_pure", "治疗纯奶"),
                           ("heal_fire", "治疗火拳"),
                           ("lieshi_small", "裂石小外")):
            assert w._rule_widgets[key]["group"].title() == title
        cb = w._rule_widgets["heal_pure"]["playstyles"]["纯奶"]
        assert cb.text() == "纯奶（主 扇 / 副 伞）"

    def test_roundtrip_single_enabled(self, qtbot):
        w = TuningConfigWidget()
        qtbot.addWidget(w)
        w.set_config({"heal_fire": {"enabled": True}})
        result = w.get_config()
        assert result["heal_fire"]["enabled"]
        assert not result["heal_pure"]["enabled"]
        # 未提及的规则保持未启用
        assert not result["huiyi_general"]["enabled"]


# ─── EquipJudgeTestDialog 可转律开关 ──────────────────

class TestCanTransmuteCheckbox:
    def test_default_checked_and_injected(self, qtbot, monkeypatch):
        from src.apps.yysls.ui.equip_judge_dialog import EquipJudgeTestDialog
        dialog = EquipJudgeTestDialog()
        qtbot.addWidget(dialog)
        # 默认勾选
        assert dialog._chk_transmute.isChecked()

        # 取消勾选后判定：每个规则 config 注入 can_transmute=False
        captured: list[dict] = []

        def fake_worthiness(equip, configs, rule_keys=None):
            captured.append(configs)
            return False, ["stub"]

        monkeypatch.setattr(
            "src.apps.yysls.ui.equip_judge_dialog.judge_tuning_worthiness",
            fake_worthiness)
        dialog._tuning_config.set_config(
            {"huiyi_general": {"enabled": True}})
        dialog.editor._affix_combos[0].setCurrentText("最大外功攻击")
        dialog._chk_transmute.setChecked(False)
        dialog._on_judge()
        assert captured
        assert captured[0]["huiyi_general"]["can_transmute"] is False
