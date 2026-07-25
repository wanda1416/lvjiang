"""装备模型 部位/武器/类别 维度测试

部位与武器为两个独立维度：部位 ∈ {武器, 环, 佩, 冠胄, 胸甲, 胫甲, 腕甲}，
部位为武器时不区分主/副，具体武器类型（剑/枪/扇/...）由 weapon 单独表达。
"""

import pytest

from src.apps.yysls.equip_parser.constants import infer_part
from src.apps.yysls.equip_parser.models import EquipmentData


class TestInferPart:
    @pytest.mark.parametrize("weapon_type", [
        "陌刀", "舞绫鼓", "双刀", "绳镖", "横刀", "手甲",
        "剑", "枪", "扇", "伞",
    ])
    def test_all_weapons_merge_into_one_part(self, weapon_type):
        assert infer_part(weapon_type) == "武器"

    @pytest.mark.parametrize("part", [
        "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲",
    ])
    def test_non_weapon_parts_keep_type(self, part):
        assert infer_part(part) == part

    def test_unknown(self):
        assert infer_part("鱼竿") == "unknown"
        assert infer_part(None) == "unknown"


class TestEquipmentDataDimensions:
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
