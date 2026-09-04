"""装备模型 部位/武器/类别 维度测试

部位与武器为两个独立维度：部位 ∈ {武器, 环, 佩, 冠胄, 胸甲, 胫甲, 腕甲}，
部位为武器时不区分主/副，具体武器类型（剑/枪/扇/...）由 weapon 单独表达。
"""


from lvjiang.apps.yysls.core.equip_parser.constants import infer_part
from lvjiang.apps.yysls.core.equip_parser.models import EquipmentData
from tests.case_matrix import case_matrix


class TestInferPart:
    @case_matrix("weapon_type", [
        "陌刀", "舞绫鼓", "双刀", "绳镖", "横刀", "手甲",
        "剑", "枪", "扇", "伞",
    ])
    def test_all_weapons_merge_into_one_part(self, weapon_type):
        assert infer_part(weapon_type) == "武器"

    @case_matrix("part", [
        "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲",
    ])
    def test_non_weapon_parts_keep_type(self, part):
        assert infer_part(part) == part

    def test_unknown(self):
        assert infer_part("鱼竿") == "unknown"
        assert infer_part(None) == "unknown"


class TestEquipmentDataDimensions:
    def test_empty_serialization_has_no_fingerprint(self):
        """空槽 OCR 的标准模型不得生成可被遍历器当成装备的指纹。"""
        assert EquipmentData().to_dict()["_fp"] == ""

    def test_garbage_name_without_type_has_no_fingerprint(self):
        """equip_type OCR 噪声保留为诊断名称，但不能确立装备身份。"""
        assert EquipmentData(name="王").to_dict()["_fp"] == ""

    def test_weapon_equipment(self):
        e = EquipmentData(type="剑")
        assert e.part == "武器"
        assert e.weapon == "剑"
        assert e.category == "weapon"

    def test_jewelry_equipment(self):
        e = EquipmentData(type="佩")
        assert e.part == "佩"
        assert e.weapon is None
        assert e.category == "jewelry"

    def test_armor_equipment(self):
        e = EquipmentData(type="腕甲")
        assert e.part == "腕甲"
        assert e.weapon is None
        assert e.category == "armor"

    def test_unknown_equipment(self):
        e = EquipmentData(type="鱼竿")
        assert e.part == "unknown"
        assert e.weapon is None
        assert e.category == "unknown"

    def test_cooldown_expiry_roundtrip_and_legacy_default(self):
        expires_at = "2026-09-07T04:00:00.000+00:00"
        equip = EquipmentData(type="剑", cooldown_expires_at=expires_at)
        data = equip.to_dict(include_fp=False)
        assert data["cooldown_expires_at"] == expires_at
        assert EquipmentData.from_dict(data).cooldown_expires_at == expires_at
        data.pop("cooldown_expires_at")
        assert EquipmentData.from_dict(data).cooldown_expires_at == ""

    def test_cooldown_expiry_never_changes_serialized_fingerprint(self):
        equip = EquipmentData(type="剑", level=110)
        initial_fp = equip.to_dict()["_fp"]
        equip.cooldown_expires_at = "2026-09-07T04:00:00.000+00:00"
        assert equip.to_dict()["_fp"] == initial_fp
        equip.cooldown_expires_at = "2026-09-08T20:00:00.000+00:00"
        assert equip.to_dict()["_fp"] == initial_fp


class TestDingyinSerialization:
    def test_roundtrip(self):
        e = EquipmentData(type="剑", dingyin={"name": "外功穿透", "value": 14.2})
        d = e.to_dict()
        assert d["dingyin"] == {"name": "外功穿透", "value": 14.2}
        restored = EquipmentData.from_dict(d)
        assert restored.dingyin == {"name": "外功穿透", "value": 14.2}

    def test_empty_dingyin_serialized_as_none(self):
        e = EquipmentData(type="剑")
        d = e.to_dict()
        assert d["dingyin"] is None
        assert EquipmentData.from_dict(d).dingyin == {}
