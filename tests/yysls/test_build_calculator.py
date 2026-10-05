"""总量分配、独立保存与编辑状态的业务契约。全部写入临时配置。"""
import copy
import random
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QWidget

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.builds import (
    BuildDefinition,
    BuildRepository,
    check_requirements,
)
from lvjiang.apps.yysls.core.equip_validator import validate_combination_dict
from lvjiang.apps.yysls.core.loadout.affix_distribution import (
    distribute_affixes,
    distribution_counts,
)
from lvjiang.apps.yysls.core.loadout.affix_swap import swap_affixes
from lvjiang.apps.yysls.ui.loadout.build_calculator import (
    BuildEditor,
    template_for_playstyle,
)
from lvjiang.core.config.resolver import ConfigResolver


@pytest.fixture
def repository(tmp_path):
    return BuildRepository(ConfigResolver(system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=False))


def counts():
    return {"最大外功攻击": 12, "势": 8, "劲": 10, "会意率": 5,
            "剑武学增伤": 1, "全武学增效": 2, "对首领单位增伤": 2}


def allocate(values, templates=None, chengyin=True):
    gc = get_game_config()
    return distribute_affixes(values, templates or template_for_playstyle("无名", gc),
                             attribute="鸣金", level=115, chengyin=chengyin, game_config=gc)


@pytest.mark.parametrize("outer,shi,weapon_first", [(12, 8, 0), (11, 9, 1), (10, 10, 2)])
def test_shi_consumes_weapon_first_positions(outer, shi, weapon_first):
    values = counts() | {"最大外功攻击": outer, "势": shi}
    original = copy.deepcopy(values)
    result = allocate(values)
    assert result.feasible, result.errors
    assert values == original
    assert distribution_counts(result.equipment, "鸣金", get_game_config()) == values
    assert sum(result.equipment[s]["affix_1"]["name"] == "势" for s in ("main_weapon", "sub_weapon")) == weapon_first
    assert all(not validate_combination_dict(e) for e in result.equipment.values())


@pytest.mark.parametrize("changes", [
    {"势": 9, "劲": 9},  # 40 条但首词条位置冲突
    {"全武学增效": 3, "劲": 9},
    {"对首领单位增伤": 3, "劲": 9},
    {"势": -1}, {"势": True}, {"势": 8.5},
])
def test_impossible_totals_never_return_partial_recommendations(changes):
    result = allocate(counts() | changes)
    assert not result.feasible
    assert result.errors
    assert result.equipment == {}


def test_legal_partial_distributions_round_trip():
    """删掉普通词条仍然合法；随机样本防止分配算法错误拒绝可行解。"""
    rng = random.Random(731)
    full = allocate(counts()).equipment
    gc = get_game_config()
    for _ in range(12):
        sample = copy.deepcopy(full)
        for equip in sample.values():
            for i in range(2, 6):
                if rng.random() < .4:
                    equip.pop(f"affix_{i}", None)
        values = distribution_counts(sample, "鸣金", gc)
        result = allocate(values, sample)
        assert result.feasible, result.errors
        assert distribution_counts(result.equipment, "鸣金", gc) == values


@pytest.mark.parametrize("playstyle", list(get_game_config().get_playstyles()))
def test_all_playstyles_reallocate_legal_random_equipment(playstyle):
    from lvjiang.apps.yysls.core.affix_cap import affix_cap_value
    from lvjiang.apps.yysls.core.combat.affix_rules import normal_affix_candidates

    gc = get_game_config()
    rng = random.Random(892)
    attribute = gc.get_playstyle(playstyle)["attr"]
    for _ in range(2):
        equipped = template_for_playstyle(playstyle, gc)
        for equip in equipped.values():
            equip.update(level=115, original_level=115, quality="gold")
            group = gc.get_type_to_group()[equip["type"]]
            equip["affix_1"] = {"name": rng.choice(gc.get_first_affixes(group, 115)), "value": 1}
            candidates = normal_affix_candidates(equip, gc)
            rng.shuffle(candidates)
            slot = 2
            for name in candidates:
                if affix_cap_value(115, name, chengyin=True, game_config=gc) is None:
                    continue
                equip[f"affix_{slot}"] = {"name": name, "value": 1}
                if validate_combination_dict(equip):
                    equip.pop(f"affix_{slot}")
                else:
                    slot += 1
                    if slot == 6:
                        break
            assert slot == 6
        values = distribution_counts(equipped, attribute, gc)
        result = distribute_affixes(values, equipped, attribute=attribute, level=115, chengyin=True, game_config=gc)
        assert result.feasible, result.errors
        assert distribution_counts(result.equipment, attribute, gc) == values


def test_cap_modes_use_existing_authority():
    gc = get_game_config()
    for mode in (True, False):
        result = allocate(counts(), chengyin=mode)
        assert result.feasible
        for equip in result.equipment.values():
            for i in range(1, 6):
                affix = equip[f"affix_{i}"]
                caps = gc.get_affix_caps(115, affix["name"])
                assert affix["value"] == caps["chengyin" if mode else "cap"]


def test_native_attack_resolves_by_equipment_position():
    values = counts() | {"劲": 2, "最大本属攻击": 8}
    result = allocate(values)
    assert result.feasible, result.errors
    assert distribution_counts(result.equipment, "鸣金", get_game_config()) == values
    for slot, equip in result.equipment.items():
        names = [equip[f"affix_{i}"]["name"] for i in range(1, 6)]
        expected = "最大无相攻击" if slot in ("main_weapon", "sub_weapon") else "最大鸣金攻击"
        assert expected in names


def test_system_override_can_clear_an_affix_without_deleting_preset(repository):
    import yaml

    from lvjiang.apps.yysls.config.builds import BUILDS_PATH
    from lvjiang.core.config.resolver import SystemContentProtected

    build = BuildDefinition.create("预设", "无名", 115)
    build.equipment = allocate(counts()).equipment
    path = repository.resolver.system_dir / BUILDS_PATH
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"content_version": 1, "builds": {build.id: build.to_dict()}}), encoding="utf-8")
    assert not repository.can_delete(build)
    with pytest.raises(SystemContentProtected):
        repository.delete(build)
    expected = build.to_dict()
    build.equipment["main_weapon"].pop("affix_5")
    repository.save(build, expected=expected)
    assert repository.all()[0].equipment["main_weapon"]["affix_5"] is None


def test_repository_merges_only_selected_build_and_rejects_stale_save(repository):
    a = BuildDefinition.create("测试搭配甲", "无名", 115)
    b = BuildDefinition.create("测试搭配乙", "无名", 115)
    repository.save(a)
    expected = a.to_dict()
    repository.save(b)
    a.name = "修改甲"
    repository.save(a, expected=expected)
    assert {x.name for x in repository.all()} == {"修改甲", "测试搭配乙"}
    with pytest.raises(ValueError, match="其他窗口"):
        repository.save(a, expected=expected)
    repository.delete(a)
    assert [x.id for x in repository.all()] == [b.id]


def test_saved_build_contains_no_private_equipment_metadata(repository):
    build = BuildDefinition.create("公开模板", "无名", 115)
    build.equipment = allocate(counts()).equipment
    build.equipment["main_weapon"].update(fingerprint="fake-private-fingerprint", owner="fake-user", scan_time="fake-time")
    repository.save(build)
    raw = repository.all()[0].to_dict()
    assert "fake-" not in str(raw)
    assert "value" not in str(raw)
    assert set(raw["equipment"]["main_weapon"]) == {"type", "equipment_set", *(f"affix_{i}" for i in range(1, 6))}


def test_requirements_do_not_mutate_targets():
    targets = [{"affix": "最大外功攻击", "priority": "optimal", "minimum": 12, "maximum": 12}]
    before = copy.deepcopy(targets)
    assert not check_requirements({"最大外功攻击": 11}, targets)[0]["satisfied"]
    assert check_requirements({"最大外功攻击": 12}, targets)[0]["satisfied"]
    assert targets == before


def test_swap_checks_reverse_destination_and_first_slot_duplicates():
    gc = get_game_config()
    equipment = {
        "main_weapon": {"type": "剑", "level": 115, "original_level": 115,
                        "affix_1": {"name": "最大外功攻击", "value": 1},
                        "affix_2": {"name": "剑武学增伤", "value": 1}},
        "sub_weapon": {"type": "枪", "level": 115, "original_level": 115,
                       "affix_1": {"name": "最大外功攻击", "value": 1},
                       "affix_2": {"name": "劲", "value": 1}},
    }
    before = copy.deepcopy(equipment)
    # 劲能进入剑，但剑武学增伤不能反向进入枪。
    changed, reason = swap_affixes(equipment, ("sub_weapon", 2), ("main_weapon", 2), gc)
    assert changed is None and reason
    equipment["main_weapon"]["affix_2"] = {"name": "最大外功攻击", "value": 1}
    equipment["main_weapon"]["affix_3"] = {"name": "势", "value": 1}
    changed, reason = swap_affixes(equipment, ("main_weapon", 1), ("main_weapon", 3), gc)
    assert changed is None and "重复" in reason
    assert before["sub_weapon"] == equipment["sub_weapon"]


def test_swapped_positions_survive_recalculate_and_save(qtbot, repository):
    from PyQt6.QtCore import QPointF

    from lvjiang.apps.yysls.ui.loadout.build_calculator import BUILD_DISPLAY_SLOTS

    build = BuildDefinition.create("交换位置", "无名", 115)
    build.equipment = allocate(counts() | {"劲": 9}).equipment
    repository.save(build)
    editor = BuildEditor("无名", repository=repository, initial=build)
    qtbot.addWidget(editor)
    changed, reason = swap_affixes(editor.result.equipment,
                                  ("main_weapon", 2), ("main_weapon", 5), editor.gc)
    assert changed is not None, reason
    table = editor.distribution_table
    row = next(i for i, spec in enumerate(BUILD_DISPLAY_SLOTS) if spec.key == "main_weapon")
    table._source = ("main_weapon", 2)
    accepted = []
    event = SimpleNamespace(source=lambda: table,
                            position=lambda: QPointF(table.visualItemRect(table.item(row, 5)).center()),
                            acceptProposedAction=lambda: accepted.append(True),
                            ignore=lambda: accepted.append(False))
    table.dropEvent(event)
    table._source = None
    assert accepted == [True]
    assert editor._dirty
    expected = distribution_counts(changed, editor.attribute, editor.gc)
    editor.recalculate()
    assert editor.result.equipment == changed

    # 跨装备交换也不能被保存前的自动分配回滚。
    cross = None
    positions = [(slot, index) for slot in changed for index in range(1, 6)]
    for source in positions:
        for target in positions:
            if source[0] == target[0]:
                continue
            if (changed[source[0]].get(f"affix_{source[1]}") or {}).get("name") == (changed[target[0]].get(f"affix_{target[1]}") or {}).get("name"):
                continue
            cross, _ = swap_affixes(changed, source, target, editor.gc)
            if cross is not None:
                break
        if cross is not None:
            break
    assert cross is not None
    editor._templates = copy.deepcopy(cross)
    editor.save()
    assert editor.result.equipment == cross
    assert editor.counts() == expected
    saved = repository.all()[0]
    for slot in cross:
        for index in range(1, 6):
            assert (saved.equipment[slot].get(f"affix_{index}") or {}).get("name") == (cross[slot].get(f"affix_{index}") or {}).get("name")


def test_drag_precomputes_targets_and_cancel_keeps_distribution(qtbot, repository, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QDrag

    from lvjiang.apps.yysls.ui.loadout.build_calculator import BUILD_DISPLAY_SLOTS

    build = BuildDefinition.create("拖拽候选", "无名", 115)
    build.equipment = allocate(counts()).equipment
    editor = BuildEditor("无名", repository=repository, initial=build)
    qtbot.addWidget(editor)
    table = editor.distribution_table
    before = copy.deepcopy(editor.result.equipment)
    table.setCurrentCell(0, 2)

    def check_targets(_drag, _actions):
        assert not _drag.pixmap().isNull()
        assert table.item(0, 2).text() == ""
        assert table.item(0, 2).background() != table.palette().highlight()
        assert editor.result.equipment == before
        for row, spec in enumerate(BUILD_DISPLAY_SLOTS):
            for index in range(1, 6):
                changed, reason = swap_affixes(before, (BUILD_DISPLAY_SLOTS[0].key, 2),
                                              (spec.key, index), editor.gc)
                expected = "正在拖动" if (row, index) == (0, 2) else "可交换" if changed is not None else reason
                assert table.item(row, index).toolTip() == expected
        return Qt.DropAction.IgnoreAction

    monkeypatch.setattr(QDrag, "exec", check_targets)
    table.startDrag(Qt.DropAction.MoveAction)
    assert table._source is None
    assert editor.result.equipment == before
    assert table.item(0, 2).text() == before[BUILD_DISPLAY_SLOTS[0].key]["affix_2"]["name"]


def test_first_show_has_same_row_heights_as_recalculation(qtbot, repository):
    """首次打开的窄列宽不能留下折行行高，用户无需拖动词条才能恢复。"""
    build = BuildDefinition.create("首次行高", "无名", 115)
    build.equipment = allocate(counts()).equipment
    editor = BuildEditor("无名", repository=repository, initial=build)
    qtbot.addWidget(editor)
    editor.resize(1320, 820)
    editor.show()
    qtbot.waitExposed(editor)
    table = editor.distribution_table
    initial = [table.rowHeight(row) for row in range(8)]
    editor.recalculate()
    assert [table.rowHeight(row) for row in range(8)] == initial


def test_editor_load_adjust_save_and_invalid_clear(qtbot, repository):
    build = BuildDefinition.create("测试实时分配", "无名", 115)
    build.equipment = allocate(counts()).equipment
    repository.save(build)
    before = repository.all()[0].to_dict()
    editor = BuildEditor("无名", repository=repository, initial=build)
    qtbot.addWidget(editor)
    assert editor.result.feasible
    assert not editor._dirty
    assert repository.all()[0].to_dict() == before
    editor._counts["最大外功攻击"].setValue(11)
    editor._counts["势"].setValue(9)
    editor.recalculate()
    assert editor.result.feasible
    assert repository.all()[0].to_dict() == before
    editor.save()
    assert repository.all()[0].to_dict() != before
    assert not editor._dirty
    editor._counts["势"].setValue(40)
    editor.recalculate()
    assert not editor.result.feasible
    assert not editor.save_button.isEnabled()
    assert editor._left_tabs.count() == 2
    assert editor.distribution_table.item(0, 1).text() == "—"


def test_cancel_switch_keeps_editor_draft(qtbot, monkeypatch, repository):
    first = BuildDefinition.create("甲", "无名", 115)
    second = BuildDefinition.create("乙", "无名", 115)
    repository.save(first)
    repository.save(second)
    editor = BuildEditor("无名", repository=repository, initial=first)
    qtbot.addWidget(editor)
    editor.name_edit.setText("尚未保存")
    monkeypatch.setattr(editor, "confirm_discard", lambda: False)
    editor.build_combo.setCurrentIndex(editor.build_combo.findData(second.id))
    assert editor.build_combo.currentData() == first.id
    assert editor.name_edit.text() == "尚未保存"
    assert [b.name for b in repository.all()] == ["甲", "乙"]


def test_counter_clicks_update_distribution_without_saving(qtbot, repository):
    from PyQt6.QtCore import Qt

    from lvjiang.apps.yysls.ui.loadout.build_calculator import AffixCounter

    build = BuildDefinition.create("步进操作", "无名", 115)
    build.equipment = allocate(counts()).equipment
    repository.save(build)
    before = repository.all()[0].to_dict()
    editor = BuildEditor("无名", repository=repository, initial=build)
    qtbot.addWidget(editor)
    counter = editor._counts["势"]
    assert isinstance(counter, AffixCounter)
    qtbot.mouseClick(editor._counts["最大外功攻击"].minus, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(counter.plus, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: editor.result.feasible and
                    distribution_counts(editor.result.equipment, "鸣金", editor.gc).get("势") == 9)
    assert counter.value() == 9
    assert repository.all()[0].to_dict() == before
    counter.setValue(0)
    assert not counter.minus.isEnabled()
    counter.setValue(40)
    assert not counter.plus.isEnabled()
    editor._timer.stop()


def test_full_total_disables_every_increase(qtbot, repository):
    """40 条上限由整套共享：满了以后任何一格都不能再加，减掉别处才解锁。

    单列自己没到 40 也能越过上限，正是这里要拦的。
    """
    build = BuildDefinition.create("总数上限", "无名", 115)
    build.equipment = allocate(counts()).equipment
    repository.save(build)
    editor = BuildEditor("无名", repository=repository, initial=build)
    qtbot.addWidget(editor)
    outer = editor._counts["最大外功攻击"]
    shi = editor._counts["势"]
    assert sum(c.value() for c in editor._counts.values()) == 40
    assert not outer.plus.isEnabled()
    assert not shi.plus.isEnabled()

    shi.setValue(shi.value() - 1)

    assert outer.plus.isEnabled()
    assert shi.plus.isEnabled()
    editor._timer.stop()


def test_distribution_cells_match_saved_slots_and_set_owners(qtbot, repository):
    build = BuildDefinition.create("逐格分配", "无名", 115)
    build.equipment = allocate(counts()).equipment
    repository.save(build)
    editor = BuildEditor("无名", repository=repository, initial=build)
    qtbot.addWidget(editor)
    table = editor.distribution_table
    assert [table.horizontalHeaderItem(i).text() for i in range(7)] == ["位置", "宫", "商", "角", "徵", "羽", "套装"]
    slots = ["main_weapon", "sub_weapon", "ring", "pendant", "head", "chest", "leg", "wrist"]
    for row, slot in enumerate(slots):
        assert table.cellWidget(row, 6) is editor._sets[slot]
        for index in range(1, 6):
            assert table.item(row, index).text() == editor.result.equipment[slot][f"affix_{index}"]["name"]
    editor._sets["ring"].setCurrentIndex(1)
    editor.recalculate()
    editor.save()
    assert repository.all()[0].equipment["ring"]["equipment_set"] == editor._sets["ring"].currentData()


def test_live_calculation_uses_shared_scorer_and_never_changes_role(qtbot, repository):
    from lvjiang.apps.yysls.core.combat.combat_attrs import (
        CombatAttributes,
        GraduationAttrContext,
        apply_hypothetical_caps,
    )
    from lvjiang.apps.yysls.core.graduation.context import (
        PlanScoringContext,
        gongjue_attrs,
    )
    from lvjiang.apps.yysls.core.graduation.scoring import LoadoutScorer

    class Calculator:
        def __init__(self):
            self.inputs = []

        def calculate(self, attrs):
            self.inputs.append(copy.deepcopy(attrs))
            return SimpleNamespace(graduation_rate=attrs.max_outer / 10000)

    calculator = Calculator()
    base = CombatAttributes(max_outer=100, min_outer=50)
    context = PlanScoringContext(
        "test-plan", "测试方案", "鸣金虹", "test-scheme", 115, 115, 1, calculator,
        GraduationAttrContext(0, 0, None), copy.deepcopy(base), copy.deepcopy(base), "", 115, "无名", "鸣金")
    original = allocate(counts()).equipment
    before = copy.deepcopy(original)
    class Host(QWidget):
        user_changed = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.user_combo = QComboBox(self)

        @staticmethod
        def active_user_name():
            return ""

        @staticmethod
        def navigate_user(_offset):
            return None

    host = Host()
    qtbot.addWidget(host)
    editor = BuildEditor(
        "无名", repository=repository, context=context, equipped=original,
        host=host,
    )
    qtbot.addWidget(editor)
    assert "毕业率" in editor.metrics.text()
    assert editor._left_tabs.count() == 2
    assert editor._attrs_preview is not None
    assert editor._result_page.isAncestorOf(editor._attrs_preview)
    assert editor._attrs_preview._select_group.isHidden()
    editor.gongjue.setCurrentIndex(editor.gongjue.findData("会意"))
    editor.recalculate()
    actual = calculator.inputs[-1]
    expected_base = base + gongjue_attrs("会意", editor.gc, world_level=115, gongjue_level=editor.gongjue_level.value())
    expected = LoadoutScorer(calculator, expected_base, context.school, editor.gc, attr_context=context.attr_context).attrs(
        apply_hypothetical_caps(editor.result.equipment, full_dingyin=True, playstyle="无名"))
    assert actual == expected
    expected_panel = expected_base + LoadoutScorer(
        calculator, expected_base, context.school, editor.gc,
        attr_context=context.attr_context,
    ).equipment_attrs(apply_hypothetical_caps(
        editor.result.equipment, full_dingyin=True, playstyle="无名"))
    assert editor._attrs_preview._current_combat_attrs == expected_panel
    editor.save()
    assert repository.all()[0].gongjue == "会意"
    assert original == before
    assert context.base_attrs_without_gongjue == base
    assert context.gongjue == ""
