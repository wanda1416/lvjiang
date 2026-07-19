"""装备 OCR 数据转换器

将 OCR 原始识别的脏数据 dict 转换为标准装备领域模型结构。

输入格式（由 equip_weapon_detail / equip_armor_detail 场景 OCR 产出）：
    {
        "equip_type": "踏雪含光 | 武器·剑",
        "equip_level": "承音 | 110阶",
        "base_attr": "外功攻击100~232",
        "affix_gong": "最大外功攻击荐114.1",
        ...
    }

输出格式（标准装备领域模型）：
    EquipmentData(slot, type, name, level, quality, is_chengyin,
                  base_attr_1, base_attr_2, affixes, warnings)
"""

from .models import EquipAttr, Affix, EquipmentData
from .parser import EquipmentParser, get_equipment_parser

__all__ = [
    "EquipAttr",
    "Affix",
    "EquipmentData",
    "EquipmentParser",
    "get_equipment_parser",
]
