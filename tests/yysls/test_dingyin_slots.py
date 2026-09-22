"""两种定音并存时的写入契约。

一件装备可以同时定着普通定音和止戈定音，游戏里随时无成本切换，而一次扫描
只可能读到其中一种。装备在仓储里只有一条记录、被多个备战方案共用，所以任何
一次整条替换都会把另一种定音抹掉——切到止戈扫一次，切回原来的备战方案就会
发现普通定音没了。这些用例锁的就是「本次没带的不能丢」。
"""

from pathlib import Path

from lvjiang.apps.yysls.core.equip_parser.dingyin_parser import (
    DINGYIN_NORMAL,
    DINGYIN_TYPE_KEY,
    DINGYIN_ZHIGE,
    ZHIGE_DINGYIN_NAME,
    can_switch_dingyin,
    resolve_dingyin_type,
)
from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.core.loadout.equipment_write import (
    WriteSource,
    merge_equipment_write,
)

_NORMAL = {"name": "外功穿透", "value": 14.2}
_ZHIGE = {"name": ZHIGE_DINGYIN_NAME}


def _equip(**overrides) -> dict:
    value = {
        "_fp": "real-fp", "type": "环", "level": 110,
        "affix_1": {"name": "最大外功攻击", "value": 80.0},
    }
    value.update(overrides)
    return value


def _normal_scan() -> dict:
    return _equip(dingyin=dict(_NORMAL), dingyin_type=DINGYIN_NORMAL)


def _zhige_scan() -> dict:
    return _equip(dingyin_zhige=dict(_ZHIGE), dingyin_type=DINGYIN_ZHIGE)


# ─── 合并契约 ──────────────────────────────────────────────

def test_scanning_zhige_keeps_the_stored_normal_dingyin():
    """核心症状：切到止戈扫一次，普通定音必须原样留着。"""
    merged = merge_equipment_write(
        _zhige_scan(), _normal_scan(), source=WriteSource.BAG_SCAN)

    assert merged["dingyin"] == _NORMAL
    assert merged["dingyin_zhige"] == _ZHIGE


def test_scanning_normal_keeps_the_stored_zhige_dingyin():
    merged = merge_equipment_write(
        _normal_scan(), _zhige_scan(), source=WriteSource.BAG_SCAN)

    assert merged["dingyin"] == _NORMAL
    assert merged["dingyin_zhige"] == _ZHIGE


def test_bag_scan_adopts_the_scanned_dingyin_type():
    """背包里读到的展示状态是用户自己给这件装备定的，可以采用。"""
    merged = merge_equipment_write(
        _zhige_scan(), _normal_scan(), source=WriteSource.BAG_SCAN)

    assert merged[DINGYIN_TYPE_KEY] == DINGYIN_ZHIGE


def test_plan_scan_never_touches_the_stored_dingyin_type():
    """备战扫描读到的止戈是切方案时游戏自动切的，用户根本没碰这件装备。

    把它当成展示偏好写回去，背包里这件装备就会莫名其妙变成止戈。
    """
    merged = merge_equipment_write(
        _zhige_scan(), _normal_scan(), source=WriteSource.PLAN_SCAN)

    assert merged["dingyin_zhige"] == _ZHIGE, "数据槽仍然要如实记录"
    assert merged[DINGYIN_TYPE_KEY] == DINGYIN_NORMAL


def test_first_write_takes_everything_from_the_scan():
    merged = merge_equipment_write(_zhige_scan(), None)

    assert merged["dingyin_zhige"] == _ZHIGE
    assert merged[DINGYIN_TYPE_KEY] == DINGYIN_ZHIGE


def test_merge_does_not_mutate_the_caller_payload():
    incoming = _zhige_scan()
    merge_equipment_write(incoming, _normal_scan())

    assert "dingyin" not in incoming


# ─── 展示解析 ──────────────────────────────────────────────

def test_switch_needs_both_slots():
    assert can_switch_dingyin(_equip(dingyin=dict(_NORMAL),
                                     dingyin_zhige=dict(_ZHIGE)))
    assert not can_switch_dingyin(_normal_scan())
    assert not can_switch_dingyin(_zhige_scan())


def test_display_falls_back_to_the_slot_that_has_data():
    """存的那一侧没有数据时回落，定音整行不该凭空消失。"""
    assert resolve_dingyin_type(
        _equip(dingyin_zhige=dict(_ZHIGE), dingyin_type=DINGYIN_NORMAL)
    ) == DINGYIN_ZHIGE
    assert resolve_dingyin_type(
        _equip(dingyin=dict(_NORMAL), dingyin_type=DINGYIN_ZHIGE)
    ) == DINGYIN_NORMAL


def test_legacy_record_without_type_reads_as_normal():
    assert resolve_dingyin_type(_equip(dingyin=dict(_NORMAL))) == DINGYIN_NORMAL


# ─── 仓储写入路径 ──────────────────────────────────────────

def test_plan_scan_keeps_the_other_dingyin(tmp_path: Path):
    """完整复现用户现场：方案 A 扫到普通，方案 B 扫到止戈。"""
    repo = LoadoutRepository("alice", tmp_path)
    plan_a = repo.load().active_plan_id
    plan_b = repo.create_plan("B", "主功法", "副功法").id

    repo.assign_equipment(plan_a, "ring", _normal_scan())
    repo.assign_equipment(plan_b, "ring", _zhige_scan())

    stored = repo.load().equipment_items["real-fp"]
    # 切回方案 A 时普通定音还在
    assert stored["dingyin"] == _NORMAL
    assert stored["dingyin_zhige"] == _ZHIGE
    # 备战扫描不改装备自身的展示状态
    assert stored.get(DINGYIN_TYPE_KEY) == DINGYIN_NORMAL


def test_bag_scan_updates_the_display_state(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    repo.upsert_item(_normal_scan())
    repo.upsert_item(_zhige_scan())

    stored = repo.load().equipment_items["real-fp"]
    assert stored["dingyin"] == _NORMAL
    assert stored[DINGYIN_TYPE_KEY] == DINGYIN_ZHIGE


def test_transmute_target_survives_a_plan_scan(tmp_path: Path):
    """备战扫描原本连转律目标都不搬——同一个整条替换的毛病。"""
    repo = LoadoutRepository("alice", tmp_path)
    plan_a = repo.load().active_plan_id
    saved = _normal_scan()
    saved["affix_1"] = {
        "name": "最大外功攻击", "value": 80.0,
        "target_transmute_name": "会心率", "target_transmute_value": 6.0,
    }
    repo.assign_equipment(plan_a, "ring", saved)

    repo.assign_equipment(plan_a, "ring", _zhige_scan())

    stored = repo.load().equipment_items["real-fp"]
    assert stored["affix_1"]["target_transmute_name"] == "会心率"
