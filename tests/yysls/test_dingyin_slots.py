"""两种定音并存时的写入契约。

一件装备可以同时定着普通定音和止戈定音，游戏里随时无成本切换，而一次扫描
只可能读到其中一种。装备在仓储里只有一条记录、被多个备战方案共用，所以任何
一次整条替换都会把另一种定音抹掉——切到止戈扫一次，切回原来的备战方案就会
发现普通定音没了。这些用例锁的就是「本次没带的不能丢」。
"""

from pathlib import Path

import pytest

from lvjiang.apps.yysls.core.equip_parser.dingyin_parser import (
    DINGYIN_NORMAL,
    DINGYIN_SLOT_NOTICE,
    DINGYIN_TYPE_KEY,
    DINGYIN_ZHIGE,
    ZHIGE_DINGYIN_NAME,
    can_switch_dingyin,
    dingyin_notice,
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


def test_plan_scan_treats_a_missing_stored_type_as_normal():
    """旧装备缺类型就是普通定音，不能被方案扫描带来的止戈状态污染。"""
    existing = _normal_scan()
    existing.pop(DINGYIN_TYPE_KEY)
    incoming = _zhige_scan()

    merged = merge_equipment_write(
        incoming, existing, source=WriteSource.PLAN_SCAN)

    assert merged[DINGYIN_TYPE_KEY] == DINGYIN_NORMAL


def test_first_plan_scan_uses_normal_as_the_equipment_display_default():
    merged = merge_equipment_write(
        _zhige_scan(), None, source=WriteSource.PLAN_SCAN)

    assert merged["dingyin_zhige"] == _ZHIGE
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

def test_plan_scan_keeps_the_other_dingyin_and_records_the_plan_choice(
    tmp_path: Path,
):
    """完整复现用户现场：方案 A 扫到普通，方案 B 扫到止戈。"""
    repo = LoadoutRepository("alice", tmp_path)
    plan_a = repo.load().active_plan_id
    plan_b = repo.create_plan("B", "主功法", "副功法").id

    repo.assign_equipment(plan_a, "ring", _normal_scan(), scanned=True)
    repo.assign_equipment(plan_b, "ring", _zhige_scan(), scanned=True)

    state = repo.load()
    stored = state.equipment_items["real-fp"]
    # 切回方案 A 时普通定音还在
    assert stored["dingyin"] == _NORMAL
    assert stored["dingyin_zhige"] == _ZHIGE
    # 各方案记住自己那一侧，装备自身的展示状态不被备战扫描改写
    assert state.plans[plan_a].dingyin["ring"] == DINGYIN_NORMAL
    assert state.plans[plan_b].dingyin["ring"] == DINGYIN_ZHIGE
    assert stored.get(DINGYIN_TYPE_KEY) == DINGYIN_NORMAL


def test_bag_scan_leaves_plan_choices_alone(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    plan_a = repo.load().active_plan_id
    repo.assign_equipment(plan_a, "ring", _normal_scan(), scanned=True)

    repo.upsert_item(_zhige_scan())

    state = repo.load()
    assert state.equipment_items["real-fp"][DINGYIN_TYPE_KEY] == DINGYIN_ZHIGE
    assert state.plans[plan_a].dingyin["ring"] == DINGYIN_NORMAL


def test_switching_writes_only_its_own_state(tmp_path: Path):
    """两个切换入口各写各的，绝不互相串扰。"""
    repo = LoadoutRepository("alice", tmp_path)
    plan_a = repo.load().active_plan_id
    repo.assign_equipment(plan_a, "ring", _normal_scan(), scanned=True)
    repo.upsert_item(_zhige_scan())

    repo.set_plan_dingyin(plan_a, "ring", DINGYIN_ZHIGE)
    state = repo.load()
    assert state.plans[plan_a].dingyin["ring"] == DINGYIN_ZHIGE
    assert state.equipment_items["real-fp"][DINGYIN_TYPE_KEY] == DINGYIN_ZHIGE

    repo.set_item_dingyin_type("real-fp", DINGYIN_NORMAL)
    state = repo.load()
    assert state.equipment_items["real-fp"][DINGYIN_TYPE_KEY] == DINGYIN_NORMAL
    assert state.plans[plan_a].dingyin["ring"] == DINGYIN_ZHIGE


def test_switching_is_refused_when_only_one_slot_has_data(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    plan_a = repo.load().active_plan_id
    repo.assign_equipment(plan_a, "ring", _normal_scan(), scanned=True)

    with pytest.raises(ValueError, match="无法切换"):
        repo.set_plan_dingyin(plan_a, "ring", DINGYIN_ZHIGE)
    with pytest.raises(ValueError, match="无法切换"):
        repo.set_item_dingyin_type("real-fp", DINGYIN_ZHIGE)


def test_replacing_or_clearing_a_slot_drops_its_dingyin_choice(tmp_path: Path):
    """换了一件装备，上一件的定音选择不能顺延给它。"""
    repo = LoadoutRepository("alice", tmp_path)
    plan_a = repo.load().active_plan_id
    repo.assign_equipment(plan_a, "ring", _zhige_scan(), scanned=True)
    assert repo.load().plans[plan_a].dingyin["ring"] == DINGYIN_ZHIGE

    other = _equip(_fp="other-fp", dingyin=dict(_NORMAL))
    repo.assign_equipment(plan_a, "ring", other)
    assert "ring" not in repo.load().plans[plan_a].dingyin

    repo.assign_equipment(plan_a, "ring", _zhige_scan(), scanned=True)
    repo.unassign(plan_a, "ring")
    assert "ring" not in repo.load().plans[plan_a].dingyin


def test_plan_dingyin_survives_a_reload(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    plan_a = repo.load().active_plan_id
    repo.assign_equipment(plan_a, "ring", _zhige_scan(), scanned=True)

    reloaded = LoadoutRepository("alice", tmp_path).load()
    assert reloaded.plans[plan_a].dingyin["ring"] == DINGYIN_ZHIGE


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

    repo.assign_equipment(plan_a, "ring", _zhige_scan(), scanned=True)

    stored = repo.load().equipment_items["real-fp"]
    assert stored["affix_1"]["target_transmute_name"] == "会心率"


# ─── 核对说明跟着槽走 ──────────────────────────────────────

def _zero_normal() -> dict:
    return _equip(
        dingyin={"name": "外功穿透", "value": 0.0,
                 DINGYIN_SLOT_NOTICE: "数值未能识别，已按 0 记录"},
        dingyin_type=DINGYIN_NORMAL)


def _misread_zhige() -> dict:
    return _equip(
        dingyin_zhige={"name": ZHIGE_DINGYIN_NAME,
                       DINGYIN_SLOT_NOTICE: "疑似误读，请核对"},
        dingyin_type=DINGYIN_ZHIGE)


def test_slot_notice_survives_a_scan_of_the_other_dingyin():
    """0 值定音的说明必须和它一起留下。

    说明就是「这个 0 是怎么来的」的唯一解释；跟着丢了，用户只看到一个孤零零
    的 0，分不清是没读出来还是装备真是 0。
    """
    merged = merge_equipment_write(
        _misread_zhige(), _zero_normal(), source=WriteSource.BAG_SCAN)

    assert merged["dingyin"][DINGYIN_SLOT_NOTICE]
    assert merged["dingyin_zhige"][DINGYIN_SLOT_NOTICE]


def test_a_clean_scan_only_clears_its_own_slot_notice():
    """重新扫到干净的普通定音，不该顺手抹掉止戈那条说明。"""
    merged = merge_equipment_write(
        _equip(dingyin=dict(_NORMAL), dingyin_type=DINGYIN_NORMAL),
        _misread_zhige(), source=WriteSource.BAG_SCAN)

    assert DINGYIN_SLOT_NOTICE not in merged["dingyin"]
    assert merged["dingyin_zhige"][DINGYIN_SLOT_NOTICE]


def test_notice_is_read_from_the_displayed_slot():
    """展示普通定音时不能读到止戈那条说明，反之亦然。"""
    equip = _equip(
        dingyin={"name": "外功穿透", "value": 0.0,
                 DINGYIN_SLOT_NOTICE: "普通槽说明"},
        dingyin_zhige={"name": ZHIGE_DINGYIN_NAME,
                       DINGYIN_SLOT_NOTICE: "止戈槽说明"})

    assert dingyin_notice(equip, DINGYIN_NORMAL) == "普通槽说明"
    assert dingyin_notice(equip, DINGYIN_ZHIGE) == "止戈槽说明"


# ─── 删除装备的槽位不变量 ──────────────────────────────────

@pytest.mark.parametrize("delete", [
    lambda repo: repo.delete_items({"mock_x"}),
    lambda repo: repo.delete_all_mock(),
])
def test_deleting_equipment_also_drops_the_plan_dingyin_choice(
    tmp_path: Path, delete,
):
    """空槽位不能留着定音选择——那是磁盘上自相矛盾的状态。"""
    repo = LoadoutRepository("alice", tmp_path)
    plan_a = repo.load().active_plan_id
    mock = _equip(_fp="mock_x", dingyin=dict(_NORMAL),
                  dingyin_zhige=dict(_ZHIGE), dingyin_type=DINGYIN_ZHIGE)
    mock["_extra"] = {"is_mock": True}
    repo.assign_equipment(plan_a, "ring", mock, scanned=True)
    assert repo.load().plans[plan_a].dingyin["ring"] == DINGYIN_ZHIGE

    delete(repo)

    plan = repo.load().plans[plan_a]
    assert plan.equipment["ring"] is None
    assert "ring" not in plan.dingyin
