"""装备数据对外交换：兵鉴 ``bingjian-gear`` 与 leoq7 V2 两种导出格式。

这两个格式是第三方站点的输入契约，改坏了用户只会看到“导入失败”，因此这里
只钉住**格式本身**：字段名、部位/武器词表、数值类型、筛选范围。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from lvjiang.apps.yysls.ui.loadout.bingjian_export import export_bingjian
from lvjiang.apps.yysls.ui.loadout.equipment_exchange import SITES
from lvjiang.apps.yysls.ui.loadout.export_dialog import SITE_OPTION_LABEL_TEMPLATE
from lvjiang.apps.yysls.ui.loadout.leoq7_export import export_leoq7

REPO = Path(__file__).parents[4]


def test_site_options_are_data_driven() -> None:
    """站点差异只是数据：界面用带占位符的模板，新增站点不必加翻译键。

    反面例子是给每个站点加一条「导出为某某格式」——第三个站点就得再抄一遍，
    词表随站点数量膨胀，还会和品牌名漂移。
    """
    assert SITE_OPTION_LABEL_TEMPLATE == "导出为 {site} 格式"
    hosts = {spec.host for spec in SITES.values()}
    assert len(hosts) == len(SITES)
    for spec in SITES.values():
        assert spec.label and spec.url.startswith("https://")
        assert "{url}" in spec.hint_key
        assert spec.file_suffix.startswith("_")

    table = (REPO / "config/i18n/apps/yysls/zh_CN.yaml").read_text(encoding="utf-8")
    assert "导出为 leoq7 格式" not in table
    assert "导出为兵鉴格式" not in table


def _weapon(**overrides) -> dict:
    item = {
        "type": "剑",
        "name": "踏雪含光",
        "level": 115,
        "quality": "gold",
        "is_chengyin": True,
        "affix_1": {"name": "最小外功攻击", "value": 114.1},
        "affix_2": {"name": "会心率", "value": 15.2, "is_transferred": True},
        "affix_3": {"name": "劲", "value": 83.1},
        "affix_4": {"name": "最大外功攻击", "value": 131.2},
        "affix_5": {"name": "剑武学增伤", "value": 11.3},
        "dingyin": {"name": "外功穿透", "value": 16.8},
    }
    item.update(overrides)
    return item


def _session() -> dict:
    return {
        "equipped": {
            "main_weapon": _weapon(),
            "chest": {
                "type": "胸甲", "name": "寒江甲", "level": 115, "quality": "purple",
                "affix_1": {"name": "气血最大值", "value": 8225},
                "affix_2": {"name": "外功防御", "value": 29},
                "dingyin": {"name": "指定武学定音", "value": 9.4},
            },
        },
        "bag_items": {
            "ring": {
                "fp-1": {"type": "环", "name": "云珑·玄", "level": 115,
                         "affix_1": {"name": "最小外功攻击", "value": 152}},
                "fp-2": {"type": "环", "name": "旧环", "level": 100,
                         "affix_1": {"name": "最小外功攻击", "value": 100}},
                "fp-3": {"type": None, "name": "识别失败"},
            },
        },
    }


def test_bingjian_payload_matches_site_contract() -> None:
    """兵鉴只认 ``bingjian-gear`` + 部位/武器词表；字段缺失即导入失败。"""
    payload = json.loads(export_bingjian(_session(), "测试角色"))

    assert payload["format"] == "bingjian-gear"
    assert payload["version"] == 1
    assert payload["name"] == "测试角色"
    assert [item["slot"] for item in payload["items"]] == [
        "weapon", "chest", "ring", "ring",
    ]

    weapon = payload["items"][0]
    assert weapon["weapon"] == "剑"
    assert weapon["chengyin"] is True
    assert weapon["purple"] is False
    assert weapon["initial"] == {"name": "最小外功攻击", "value": 114.1}
    assert weapon["subs"][0] == {"name": "会心率", "value": 15.2, "transmuted": True}
    assert len(weapon["subs"]) == 4
    assert weapon["pitch"] == {"name": "外功穿透", "value": 16.8}

    chest = payload["items"][1]
    assert "weapon" not in chest
    assert chest["purple"] is True
    assert chest["pitch"] == {"name": "指定武学定音", "value": 9.4}


def test_bingjian_filters_apply_to_bag_only() -> None:
    """已穿戴装备始终导出；等级与词条筛选只作用于背包。"""
    payload = json.loads(export_bingjian(_session(), "测试角色", level_threshold=115))
    names = [item["name"] for item in payload["items"]]
    assert names == ["踏雪含光", "寒江甲", "云珑·玄"]

    payload = json.loads(export_bingjian(_session(), "测试角色", affix_filter="dingyin"))
    assert [item["name"] for item in payload["items"]] == ["踏雪含光", "寒江甲"]


def test_bingjian_skips_unplaceable_items() -> None:
    """认不出部位或武器的装备不能塞进 JSON：宁可少一件，也不要错位。"""
    session = {
        "equipped": {"main_weapon": {"name": "无名剑", "affix_1": {"name": "劲", "value": 1}}},
        "bag_items": {"ring": {"fp": {"type": "未知道具", "name": "杂物"}}},
    }
    payload = json.loads(export_bingjian(session, "测试角色"))
    assert payload["items"] == []


def test_bingjian_values_are_plain_numbers() -> None:
    """站点用 Number(value) 读取，字符串或布尔值会算成 NaN。"""
    payload = json.loads(export_bingjian(_session(), "测试角色"))
    for item in payload["items"]:
        for stat in [item.get("initial"), item.get("pitch"), *(item.get("subs") or [])]:
            if stat is None:
                continue
            assert isinstance(stat["value"], float), stat
            assert isinstance(stat["name"], str) and stat["name"]


def test_leoq7_text_keeps_v2_header_and_decodes() -> None:
    """leoq7 仍是 ``V2 + XOR + Base64`` 两行文本，装备条目字段不变。"""
    text = export_leoq7(_session(), "测试角色")
    header, body = text.split("\n", 1)
    assert header == "YYSLS_EQUIPMENT_EXPORT_V2"

    encrypted = base64.standard_b64decode(body)
    key = b"yysls-equipment-transfer"
    raw = bytes(
        byte ^ key[index % len(key)] ^ ((17 * index + 29) % 251)
        for index, byte in enumerate(encrypted)
    )
    payload = json.loads(raw.decode("utf-8"))

    assert payload["kind"] == "yysls-equipment-export"
    assert payload["schemaVersion"] == 2
    assert payload["roleName"] == "测试角色"
    assert [item["equipmentKey"] for item in payload["items"]] == [
        "EQUIPMENT_WEAPON_SWORD", "EQUIPMENT_CHEST", "EQUIPMENT_RING", "EQUIPMENT_RING",
    ]
    assert payload["items"][0]["pitch"] == [{"key": "EXTERNAL_PENETRATION", "value": 16.8}]
    assert payload["items"][1]["type"] == "cloth"


def test_bingjian_dedups_equipped_and_skips_mock_and_supports_not_full_tuning() -> None:
    """已穿戴真实件不与背包重复导出、已穿戴模拟件不泄漏，且支持未满调律筛选。"""
    equipped_weapon = _weapon(_fp="fp-equipped-weapon", name="已穿戴长剑")
    equipped_mock = _weapon(
        _fp="mock_1",
        name="已穿戴模拟件",
        _extra={"is_mock": True},
    )
    bag_not_full = {
        "_fp": "fp-not-full",
        "type": "环",
        "name": "未满四词条环",
        "level": 115,
        "affix_1": {"name": "劲", "value": 30},
    }
    bag_full = {
        "_fp": "fp-full",
        "type": "佩",
        "name": "满五词条佩",
        "level": 115,
        "affix_1": {"name": "劲", "value": 30},
        "affix_2": {"name": "敏", "value": 30},
        "affix_3": {"name": "势", "value": 30},
        "affix_4": {"name": "会心率", "value": 5},
        "affix_5": {"name": "会意率", "value": 5},
    }
    session = {
        "equipped": {
            "main_weapon": equipped_weapon,
            "sub_weapon": equipped_mock,
        },
        "bag_items": {
            "weapon": {"fp-equipped-weapon": dict(equipped_weapon)},
            "ring": {"fp-not-full": bag_not_full},
            "pendant": {"fp-full": bag_full},
        },
    }

    all_items = json.loads(export_bingjian(session, "测试角色"))["items"]
    assert [i["name"] for i in all_items] == ["已穿戴长剑", "未满四词条环", "满五词条佩"]

    not_full_items = json.loads(
        export_bingjian(session, "测试角色", affix_filter="not_full_tuning")
    )["items"]
    assert [i["name"] for i in not_full_items] == ["已穿戴长剑", "未满四词条环"]
