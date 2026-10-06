from __future__ import annotations

from PyQt6.QtWidgets import QLabel

from lvjiang.apps.yysls.config.models import LevelConfig
from lvjiang.apps.yysls.core.equip_parser.models import make_fingerprint
from lvjiang.apps.yysls.core.loadout import (
    LoadoutRepository,
    find_chengyin_merge_candidates,
)
from lvjiang.apps.yysls.ui.loadout.equip.chengyin_merge_dialog import (
    ChengyinMergeDialog,
    load_user_chengyin_candidates,
)


def _levels() -> list[LevelConfig]:
    return [
        LevelConfig(level=91, allow_chengyin=True),
        LevelConfig(level=100, allow_chengyin=True),
        LevelConfig(
            level=105, allow_chengyin=True, allow_retransfer=True),
        LevelConfig(
            level=110, allow_chengyin=True, allow_retransfer=True),
    ]


def _equip(
    *,
    level: int = 100,
    values: tuple[float, ...] = (10, 20, 30, 40, 50),
    names: tuple[str, ...] = ("词一", "词二", "词三", "词四", "词五"),
    transferred: int | None = None,
    dingyin: bool = True,
    name: str = "套装甲",
) -> dict:
    equip: dict = {
        "type": "剑",
        "name": name,
        "level": level,
        "quality": "gold",
        "is_chengyin": level > 100,
        "dingyin": {"name": "定音甲", "value": 1} if dingyin else None,
    }
    for index, (affix_name, value) in enumerate(
        zip(names, values, strict=True), 1
    ):
        equip[f"affix_{index}"] = {
            "name": affix_name,
            "value": value,
            "is_transferred": transferred == index,
        }
    equip["_fp"] = make_fingerprint(equip)
    return equip


def _find(*equips: dict):
    return find_chengyin_merge_candidates(
        {equip["_fp"]: equip for equip in equips}, _levels())


def test_first_transfer_does_not_require_old_level_to_allow_retransfer():
    old = _equip(level=100)
    names = ("词一", "词二", "新词三", "词四", "词五")
    new = _equip(level=105, values=(11, 21, 8, 41, 51), names=names,
                 transferred=3)

    candidates = _find(old, new)

    assert len(candidates) == 1
    assert candidates[0].old_fp == old["_fp"]
    assert candidates[0].new_fp == new["_fp"]


def test_retransfer_name_change_requires_both_levels_to_allow_it():
    old = _equip(level=100, transferred=3)
    new = _equip(
        level=105,
        names=("词一", "词二", "新词三", "词四", "词五"),
        transferred=3,
    )

    assert _find(old, new) == []


def test_retransfer_at_fixed_position_is_allowed_for_infinite_levels():
    old = _equip(level=105, transferred=3)
    new = _equip(
        level=110,
        values=(11, 21, 8, 41, 51),
        names=("词一", "词二", "新词三", "词四", "词五"),
        transferred=3,
    )

    assert len(_find(old, new)) == 1


def test_crossed_affix_values_are_not_same_item():
    left = _equip(values=(10, 21, 30, 40, 50))
    right = _equip(values=(11, 20, 30, 40, 50), name="另一套装")

    assert _find(left, right) == []


def test_name_and_dingyin_contents_are_ignored():
    old = _equip(name="套装甲")
    new = _equip(values=(11, 21, 31, 41, 51), name="套装乙")
    new["dingyin"] = {"name": "完全不同的定音", "value": 999}

    assert len(_find(old, new)) == 1


def test_requires_five_affixes_and_any_dingyin():
    complete = _equip()
    incomplete = _equip(values=(11, 21, 31, 41, 51))
    incomplete.pop("affix_5")
    no_dingyin = _equip(values=(12, 22, 32, 42, 52), dingyin=False)

    assert _find(complete, incomplete, no_dingyin) == []


def test_more_than_five_affixes_is_not_a_full_valid_item():
    old = _equip()
    old["affix_6"] = {"name": "异常词条", "value": 1}
    new = _equip(values=(11, 21, 31, 41, 51))

    assert _find(old, new) == []


def test_transfer_slot_cannot_move():
    old = _equip(level=105, transferred=2)
    new = _equip(level=110, values=(11, 21, 31, 41, 51), transferred=3)

    assert _find(old, new) == []


def test_first_affix_cannot_become_transferred():
    old = _equip(level=100)
    new = _equip(level=105, values=(11, 21, 31, 41, 51), transferred=1)

    assert _find(old, new) == []


def test_both_levels_must_allow_chengyin():
    old = _equip(level=91)
    new = _equip(level=90, values=(11, 21, 31, 41, 51))

    assert _find(old, new) == []


def test_multiple_transfer_markers_are_rejected():
    old = _equip(level=105)
    old["affix_2"]["is_transferred"] = True
    old["affix_3"]["is_transferred"] = True
    new = _equip(level=110, values=(11, 21, 31, 41, 51), transferred=2)

    assert _find(old, new) == []


def test_zhige_dingyin_is_eligible():
    old = _equip(dingyin=False)
    new = _equip(values=(11, 21, 31, 41, 51), dingyin=False)
    old["dingyin_zhige"] = {"name": "止戈定音"}
    new["dingyin_zhige"] = {"name": "止戈定音"}

    assert len(_find(old, new)) == 1


def test_unknown_quality_is_rejected():
    old = _equip()
    new = _equip(values=(11, 21, 31, 41, 51))
    old["quality"] = "unknown"
    new["quality"] = "unknown"

    assert _find(old, new) == []


def test_merge_repository_migrates_all_plan_references(tmp_path):
    repo = LoadoutRepository("tester", users_dir=tmp_path)
    old = _equip()
    new = _equip(level=105, values=(11, 21, 31, 41, 51))
    old_fp = repo.upsert_item(old)
    new_fp = repo.upsert_item(new)
    plan_id = repo.load().active_plan_id
    repo.assign_equipment(plan_id, "main_weapon", old)

    repo.merge_items({old_fp: new_fp})

    state = repo.load()
    assert old_fp not in state.equipment_items
    assert state.plans[plan_id].equipment["main_weapon"] == new_fp


def test_merge_inherits_earliest_creation_and_latest_update(tmp_path):
    repo = LoadoutRepository("tester", users_dir=tmp_path)
    old = _equip()
    new = _equip(level=105, values=(11, 21, 31, 41, 51))
    old_fp = repo.upsert_item(old)
    new_fp = repo.upsert_item(new)

    def set_times(state):
        state.equipment_items[old_fp].update({
            "created_at": "2026-08-01T01:00:00+00:00",
            "updated_at": "2026-08-03T01:00:00+00:00",
        })
        state.equipment_items[new_fp].update({
            "created_at": "2026-08-02T01:00:00+00:00",
            "updated_at": "2026-08-04T01:00:00+00:00",
        })
    repo.update(set_times)

    repo.merge_items({old_fp: new_fp})

    merged = repo.load().equipment_items[new_fp]
    assert merged["created_at"] == "2026-08-01T01:00:00+00:00"
    assert merged["updated_at"] == "2026-08-04T01:00:00+00:00"


def test_merge_with_no_time_data_keeps_empty_values(tmp_path):
    repo = LoadoutRepository("tester", users_dir=tmp_path)
    old = _equip()
    new = _equip(level=105, values=(11, 21, 31, 41, 51))
    old_fp = repo.upsert_item(old)
    new_fp = repo.upsert_item(new)

    def clear_times(state):
        for fp in (old_fp, new_fp):
            state.equipment_items[fp]["created_at"] = ""
            state.equipment_items[fp]["updated_at"] = ""
    repo.update(clear_times)

    repo.merge_items({old_fp: new_fp})

    merged = repo.load().equipment_items[new_fp]
    assert merged["created_at"] == ""
    assert merged["updated_at"] == ""


# ─── 数值口径差异（游戏显示 1 位小数 vs 内部承音上限 2 位小数）───────────

def _cy_pair() -> tuple[dict, dict]:
    """同一把 110 承音伞的两份快照。

    左边是「一键满承音」手填的，数值取 round(cap * 0.94, 2)；右边是照着
    游戏界面录的，只有 1 位小数。121.4*0.94=114.116 → 114.12 / 114.1，
    76.8*0.94=72.192 → 72.19 / 72.2：两条词条的舍入方向恰好相反。
    """
    names = ("最大外功攻击", "劲", "最大外功攻击", "最小外功攻击", "敏")
    filled = _equip(
        level=110, names=names, transferred=2,
        values=(114.12, 72.19, 114.12, 114.12, 72.19))
    scanned = _equip(
        level=110, names=names, transferred=2,
        values=(114.1, 72.2, 114.1, 114.1, 72.2))
    scanned["original_level"] = 105
    scanned["updated_at"] = "2026-09-04T15:37:14.328+00:00"
    return filled, scanned


def test_opposite_rounding_between_sources_is_not_a_value_drop():
    filled, scanned = _cy_pair()

    candidates = _find(filled, scanned)

    assert len(candidates) == 1
    # 双向兼容时保留元数据更全、更新时间更晚的实测快照。
    assert candidates[0].old_fp == filled["_fp"]
    assert candidates[0].new_fp == scanned["_fp"]


def test_freshness_beats_insertion_order():
    filled, scanned = _cy_pair()

    assert _find(scanned, filled) == _find(filled, scanned)


def test_visible_value_drop_is_still_rejected():
    """0.1 是游戏里可见的最小差异，容差不能把它抹平。

    数值降低的方向必须被拒，于是这一对只剩「低 → 高」一个合法方向，
    先录入的高值快照反而被判定为后继版本。
    """
    higher = _equip(level=110, values=(114.2, 72.2, 114.1, 114.1, 72.2))
    lower = _equip(level=110, values=(114.1, 72.2, 114.1, 114.1, 72.2))

    candidates = _find(higher, lower)

    assert len(candidates) == 1
    assert candidates[0].old_fp == lower["_fp"]
    assert candidates[0].new_fp == higher["_fp"]


def test_intermediate_feeding_state_precedes_full_chengyin_snapshot():
    """喂到一半的实测快照，应被认作满承音快照的前身。"""
    names = ("最大外功攻击", "劲", "最大外功攻击", "最小外功攻击", "敏")
    feeding = _equip(level=110, names=names, transferred=2,
                     values=(110.2, 72.2, 98.4, 114.1, 72.2))
    full = _equip(level=110, names=names, transferred=2,
                  values=(114.12, 72.19, 114.12, 114.12, 72.19))

    candidates = _find(feeding, full)

    assert len(candidates) == 1
    assert candidates[0].old_fp == feeding["_fp"]
    assert candidates[0].new_fp == full["_fp"]


def test_load_candidates_across_all_users(tmp_path):
    old = _equip(level=100)
    new = _equip(level=105, values=(11, 21, 31, 41, 51))
    for username in ("alice", "bob"):
        repo = LoadoutRepository(username, tmp_path)
        repo.upsert_item(old)
        repo.upsert_item(new)

    entries = load_user_chengyin_candidates(
        ["alice", "missing", "bob"], _levels(), tmp_path)

    assert [entry.username for entry in entries] == ["alice", "bob"]
    assert all(entry.candidate.old_fp == old["_fp"] for entry in entries)
    assert all(entry.candidate.new_fp == new["_fp"] for entry in entries)


def test_merge_dialog_displays_username_for_each_candidate(qtbot):
    from lvjiang.apps.yysls.ui.loadout.equip.chengyin_merge_dialog import (
        UserChengyinMergeCandidate,
    )

    old = _equip(level=100)
    new = _equip(level=105, values=(11, 21, 31, 41, 51))
    candidate = _find(old, new)[0]
    dialog = ChengyinMergeDialog(
        [UserChengyinMergeCandidate("alice", candidate)], {})
    qtbot.addWidget(dialog)

    assert dialog._pairs[0].entry.username == "alice"
    assert any(
        label.text().endswith("：alice")
        for label in dialog._pairs[0].findChildren(QLabel)
    )


def test_check_all_button_selects_every_candidate(qtbot):
    """「一键勾选」要能一次勾满全部候选。

    候选动辄几十组，逐个点勾选框不现实；这条路径是批量合并可用的前提，
    勾满之后合并按钮也应当随之可用。
    """
    from lvjiang.apps.yysls.ui.loadout.equip.chengyin_merge_dialog import (
        UserChengyinMergeCandidate,
    )

    entries = []
    for index in range(3):
        old = _equip(level=100, values=(10 + index, 20, 30, 40, 50))
        new = _equip(level=105, values=(11 + index, 21, 31, 41, 51))
        entries.append(
            UserChengyinMergeCandidate(f"user{index}", _find(old, new)[0]))

    dialog = ChengyinMergeDialog(entries, {})
    qtbot.addWidget(dialog)

    assert not dialog.merge_button.isEnabled()
    dialog.check_all_button.click()

    assert all(pair.checkbox.isChecked() for pair in dialog._pairs)
    assert dialog.merge_button.isEnabled()
    assert len(dialog.selected_candidates()) == 3


def _merge_dialog(qtbot, candidates):
    from lvjiang.apps.yysls.ui.loadout.equip.chengyin_merge_dialog import (
        UserChengyinMergeCandidate,
    )
    dialog = ChengyinMergeDialog(
        [UserChengyinMergeCandidate("tester", c) for c in candidates], {})
    qtbot.addWidget(dialog)
    return dialog


def test_three_snapshots_merge_as_one_group_and_migrate_references(qtbot, tmp_path):
    """两次培养后一起扫描：一键勾选只保留最终版本，旧方案引用不丢失。"""
    first = _equip()
    middle = _equip(level=105, values=(11, 21, 31, 41, 51))
    latest = _equip(level=110, values=(12, 22, 32, 42, 52))
    candidates = _find(first, middle, latest)
    assert {(c.old_fp, c.new_fp) for c in candidates} == {
        (first["_fp"], latest["_fp"]), (middle["_fp"], latest["_fp"])}
    dialog = _merge_dialog(qtbot, candidates)
    assert len(dialog._pairs) == 1
    assert len(dialog._pairs[0].entries) == 2
    dialog.check_all_button.click()
    selected = dialog.selected_candidates()
    assert len(selected) == 2

    repo = LoadoutRepository("tester", users_dir=tmp_path)
    for equip in (first, middle, latest):
        repo.upsert_item(equip)
    plan_id = repo.load().active_plan_id
    repo.assign_equipment(plan_id, "main_weapon", first)
    repo.assign_equipment(plan_id, "sub_weapon", middle)
    repo.merge_items({entry.candidate.old_fp: entry.candidate.new_fp for entry in selected})
    state = repo.load()
    assert set(state.equipment_items) == {latest["_fp"]}
    assert state.plans[plan_id].equipment["main_weapon"] == latest["_fp"]
    assert state.plans[plan_id].equipment["sub_weapon"] == latest["_fp"]


def test_ambiguous_targets_require_manual_mutually_exclusive_choice(qtbot):
    """同一旧记录指向两个互不兼容的新版本，全选跳过且选择立即互斥。"""
    old = _equip()
    left = _equip(level=105, values=(12, 21, 31, 41, 51))
    right = _equip(level=105, values=(11, 22, 31, 41, 51))
    candidates = _find(old, left, right)
    assert len(candidates) == 2
    assert all(c.requires_review for c in candidates)
    dialog = _merge_dialog(qtbot, candidates)
    dialog._check_all()
    assert dialog.selected_candidates() == []
    assert not dialog.merge_button.isEnabled()
    first, second = dialog._pairs
    first.checkbox.setChecked(True)
    second.checkbox.setChecked(True)
    assert not first.checkbox.isChecked()
    assert len(dialog.selected_candidates()) == 1
    assert dialog.selected_candidates()[0].candidate.new_fp == right["_fp"]


def test_nontransitive_chain_cannot_delete_a_selected_retained_version(qtbot):
    """转律换回原词但数值降低：不能凭 A→B→C 推导不合法的 A→C。"""
    first = _equip()
    middle = _equip(level=105, values=(11, 21, 8, 41, 51),
                    names=("词一", "词二", "新词三", "词四", "词五"), transferred=3)
    latest = _equip(level=110, values=(12, 22, 9, 42, 52), transferred=3)
    candidates = _find(first, middle, latest)
    assert {(c.old_fp, c.new_fp) for c in candidates} == {
        (first["_fp"], middle["_fp"]), (middle["_fp"], latest["_fp"])}
    dialog = _merge_dialog(qtbot, candidates)
    dialog._check_all()
    assert dialog.selected_candidates() == []
    first_group, second_group = dialog._pairs
    first_group.checkbox.setChecked(True)
    second_group.checkbox.setChecked(True)
    assert not first_group.checkbox.isChecked()
    assert len(dialog.selected_candidates()) == 1


def test_incompatible_old_snapshots_sharing_target_are_not_auto_selected(qtbot):
    """两个不同旧装备也可能各自匹配一个新记录，不能当成明确历史链。"""
    left = _equip(values=(10, 21, 30, 40, 50))
    right = _equip(values=(11, 20, 30, 40, 50))
    latest = _equip(level=105, values=(12, 22, 32, 42, 52))
    candidates = _find(left, right, latest)
    assert len(candidates) == 2
    assert all(c.requires_review for c in candidates)
    dialog = _merge_dialog(qtbot, candidates)
    assert len(dialog._pairs) == 1
    dialog._check_all()
    assert dialog.selected_candidates() == []
