"""装备领域模型数据结构

定义 EquipAttr、Affix、EquipmentData 三个核心数据类，
支持 JSON 序列化/反序列化。
"""

from dataclasses import dataclass, field


@dataclass
class EquipAttr:
    """装备基础属性（武器为 [min, max]，防具/首饰为 int）"""
    name: str
    value: int | list[int]

    def to_dict(self) -> dict:
        return {"name": self.name, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict) -> "EquipAttr":
        return cls(name=d["name"], value=d["value"])


@dataclass
class Affix:
    """词条"""
    name: str
    value: float
    unit: str | None = None  # "%" 或 None
    is_transferred: bool = False

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "value": self.value}
        if self.unit:
            d["unit"] = self.unit
        d["is_transferred"] = self.is_transferred
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Affix":
        return cls(
            name=d["name"],
            value=d["value"],
            unit=d.get("unit"),
            is_transferred=d.get("is_transferred", False),
        )


@dataclass
class EquipmentData:
    """标准装备领域模型

    JSON 输出格式（与 equipment.md 定义一致）：
    {
        "slot": "main_weapon",
        "type": "剑",
        "name": "踏雪含光",
        "level": 110,
        "quality": null,
        "is_chengyin": true,
        "base_attr_1": { "name": "外功攻击", "value": [100, 232] },
        "base_attr_2": null,
        "affix_1": { "name": "最大外功攻击", "value": 114.1, "is_transferred": false },
        "affix_2": { "name": "会意率", "value": 6.6, "unit": "%", "is_transferred": false },
        ...
        "_warnings": []
    }
    """
    slot: str
    type: str | None = None        # 武器类型（剑/枪/...）或防具类别（冠胄/...）
    name: str | None = None        # 装备名称
    level: int | None = None
    quality: str | None = None     # gold/purple/blue/green，OCR 暂无法识别
    is_chengyin: bool = False
    base_attr_1: EquipAttr | None = None
    base_attr_2: EquipAttr | None = None
    affixes: list[Affix] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为标准装备领域模型 JSON dict

        affixes 列表展开为 affix_1 ~ affix_5 键。
        """
        d: dict = {
            "slot": self.slot,
            "type": self.type,
            "name": self.name,
            "level": self.level,
            "quality": self.quality,
            "is_chengyin": self.is_chengyin,
            "base_attr_1": self.base_attr_1.to_dict() if self.base_attr_1 else None,
            "base_attr_2": self.base_attr_2.to_dict() if self.base_attr_2 else None,
        }
        for i, affix in enumerate(self.affixes, 1):
            d[f"affix_{i}"] = affix.to_dict()
        if self.warnings:
            d["_warnings"] = self.warnings
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "EquipmentData":
        """从 JSON dict 还原为 EquipmentData"""
        affixes = []
        for i in range(1, 6):
            key = f"affix_{i}"
            if key in d and d[key] is not None:
                affixes.append(Affix.from_dict(d[key]))

        return cls(
            slot=d["slot"],
            type=d.get("type"),
            name=d.get("name"),
            level=d.get("level"),
            quality=d.get("quality"),
            is_chengyin=d.get("is_chengyin", False),
            base_attr_1=EquipAttr.from_dict(d["base_attr_1"]) if d.get("base_attr_1") else None,
            base_attr_2=EquipAttr.from_dict(d["base_attr_2"]) if d.get("base_attr_2") else None,
            affixes=affixes,
            warnings=d.get("_warnings", []),
        )
