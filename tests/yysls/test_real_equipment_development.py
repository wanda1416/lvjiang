from datetime import datetime, timedelta

from lvjiang.apps.yysls.core.equip_parser.models import make_fingerprint
from lvjiang.apps.yysls.ui.loadout.equip.mock_dialog import MockEquipDialog


def _real_equip() -> dict:
    equip = {
        "type": "环",
        "name": "踏雪含光",
        "level": 105,
        "original_level": 105,
        "quality": "gold",
        "is_chengyin": False,
        "base_attr": {"name": "气血最大值", "value": 1000},
        "base_attr_2": None,
        "affix_1": {"name": "最大外功攻击", "value": 80.0},
        "affix_2": {"name": "劲", "value": 60.0},
        "affix_3": {"name": "势", "value": 60.0},
        "affix_4": {"name": "敏", "value": 60.0},
        "affix_5": {"name": "会心率", "value": 5.0},
        "dingyin": {"name": "外功穿透", "value": 10.0},
        "cooldown_expires_at": "",
        "_extra": {"is_mock": False, "affix_count": 5},
    }
    equip["_fp"] = make_fingerprint(equip)
    return equip


def test_real_development_locks_identity_and_allows_value_growth(qtbot):
    dialog = MockEquipDialog(_real_equip())
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "养成扫描装备"
    assert not dialog._combo_part.isEnabled()
    assert not dialog._combo_weapon_type.isEnabled()
    assert not dialog._edit_name.isEnabled()
    assert not dialog._combo_level.isEnabled()
    assert not dialog._combo_quality.isEnabled()
    assert not dialog._btn_dingyin.isEnabled()
    assert dialog._spin_dingyin.isEnabled()
    assert dialog._spin_dingyin.minimum() == 10.0
    assert not dialog._affix_rows[0]._combo_name.isEnabled()
    assert dialog._affix_rows[1]._combo_name.isEnabled()
    assert dialog._affix_rows[1]._spin_value.minimum() == 60.0


def test_real_equipment_copy_marker_keeps_dialog_in_mock_edit_mode(qtbot):
    copied = _real_equip()
    copied.setdefault("_extra", {})["is_mock"] = True
    copied.pop("_fp", None)

    dialog = MockEquipDialog(copied)
    qtbot.addWidget(dialog)

    assert not dialog._is_real_development
    assert dialog.windowTitle() == "编辑模拟装备"
    assert dialog._edit_name.isEnabled()
    assert dialog._combo_quality.isEnabled()


def test_real_development_does_not_allow_filling_ocr_blank_affix(qtbot):
    equip = _real_equip()
    equip.pop("affix_5")
    equip["_fp"] = make_fingerprint(equip)
    dialog = MockEquipDialog(equip)
    qtbot.addWidget(dialog)

    assert not dialog._affix_rows[4]._combo_name.isEnabled()
    assert not dialog._affix_rows[4]._spin_value.isEnabled()


def test_dingyin_cultivation_keeps_fingerprint_and_cooldown(qtbot):
    equip = _real_equip()
    equip["cooldown_expires_at"] = "2026-09-08T10:00:00+00:00"
    dialog = MockEquipDialog(equip)
    qtbot.addWidget(dialog)
    dialog._spin_dingyin.setValue(11.0)

    result = dialog._build_real_development_data()

    assert result["dingyin"]["name"] == "外功穿透"
    assert result["dingyin"]["value"] == 11.0
    assert result["_fp"] == equip["_fp"]
    assert result["cooldown_expires_at"] == equip["cooldown_expires_at"]
    assert dialog._validate_real_development(result) is None


def test_transmute_marks_slot_and_resets_configured_five_day_cooldown(qtbot):
    equip = _real_equip()
    dialog = MockEquipDialog(equip)
    qtbot.addWidget(dialog)
    row = dialog._affix_rows[1]
    replacement = next(
        row._combo_name.itemData(index)
        for index in range(row._combo_name.count())
        if row._combo_name.itemData(index)
        and row._combo_name.itemData(index) != equip["affix_2"]["name"]
    )
    row._combo_name.setCurrentIndex(row._combo_name.findData(replacement))

    result = dialog._build_real_development_data()

    assert result["affix_2"]["name"] == replacement
    assert result["affix_2"]["is_transferred"] is True
    expires_at = datetime.fromisoformat(result["cooldown_expires_at"])
    remaining = expires_at - datetime.now(expires_at.tzinfo)
    assert timedelta(days=4, hours=23) < remaining <= timedelta(days=5)


def test_dialog_and_repository_share_one_rule_set(qtbot):
    """对话框放行的养成，写入边界必须同样放行。

    两侧各写一份规则时最容易出的问题是：对话框点了确定，仓储再抛
    ValueError。规则合并后用同一份数据两头验一次，钉住这个约定。
    """
    from lvjiang.apps.yysls.core.loadout.development_rules import (
        check_real_development,
    )

    equip = _real_equip()
    dialog = MockEquipDialog(equip)
    qtbot.addWidget(dialog)
    row = dialog._affix_rows[1]
    replacement = next(
        row._combo_name.itemData(index)
        for index in range(row._combo_name.count())
        if row._combo_name.itemData(index)
        and row._combo_name.itemData(index) != equip["affix_2"]["name"]
    )
    row._combo_name.setCurrentIndex(row._combo_name.findData(replacement))

    result = dialog._build_real_development_data()

    assert dialog._validate_real_development(result) is None
    assert check_real_development(equip, result) is None


def test_untouched_equipment_is_rejected_by_the_dialog_only(qtbot):
    """「没改动」是交互约束，不属于数据合法性，不该进共享规则。"""
    from lvjiang.apps.yysls.core.loadout.development_rules import (
        check_real_development,
    )

    equip = _real_equip()
    dialog = MockEquipDialog(equip)
    qtbot.addWidget(dialog)

    result = dialog._build_real_development_data()

    assert dialog._validate_real_development(result) == "未对装备进行任何养成修改"
    assert check_real_development(equip, result) is None


def test_transmute_allows_smaller_value_in_new_units_and_restores_original_floor(qtbot):
    dialog = MockEquipDialog(_real_equip())
    qtbot.addWidget(dialog)
    row = dialog._affix_rows[1]
    row._combo_name.setCurrentIndex(row._combo_name.findData("会意率"))
    row._spin_value.setValue(5.0)
    result = dialog._build_real_development_data()
    assert result["affix_2"]["value"] == 5.0
    assert result["affix_2"]["cap_pct"] < 100
    assert dialog._validate_real_development(result) is None
    assert dialog._validate_affix_rules(result) is None
    row._combo_name.setCurrentIndex(row._combo_name.findData("劲"))
    row._spin_value.setValue(5.0)
    assert row.get_data()["value"] == 60.0
