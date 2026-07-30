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
    cap_pct: float | None = None  # 数值百分比（0-100），表示当前值占该等级上限的比例

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "value": self.value}
        if self.unit:
            d["unit"] = self.unit
        if self.is_transferred:
            d["is_transferred"] = self.is_transferred
        if self.cap_pct is not None:
            d["cap_pct"] = self.cap_pct
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Affix":
        return cls(
            name=d["name"],
            value=d["value"],
            unit=d.get("unit"),
            is_transferred=d.get("is_transferred", False),
            cap_pct=d.get("cap_pct"),
        )


@dataclass
class EquipmentData:
    """标准装备领域模型

    JSON 输出格式：
    {
        "type": "剑",
        "name": "踏雪含光",
        "level": 110,
        "quality": null,
        "is_chengyin": true,
        "base_attr": { "name": "外功攻击", "value": [100, 232] },
        "base_attr_2": null,
        "affix_1": { "name": "最大外功攻击", "value": 114.1, "is_transferred": false },
        "affix_2": { "name": "会意率", "value": 6.6, "unit": "%", "is_transferred": false },
        ...
        "dingyin": { "name": "外功穿透", "value": 14.2 },
        "_warnings": []
    }
    """
    type: str | None = None        # 武器类型（剑/枪/...）或防具类别（冠胄/...）或首饰（环/佩）
    name: str | None = None        # 装备名称
    level: int | None = None
    quality: str | None = None     # gold/purple/blue/green，OCR 暂无法识别
    is_chengyin: bool = False
    base_attr: EquipAttr | None = None
    base_attr_2: EquipAttr | None = None
    affixes: list[Affix] = field(default_factory=list)
    # 定音词条 {"name": 原始词条名, "value": 数值}；左四（武器/环/佩）为
    # 外功增益/属攻增益的原始词条名，右四（防具）为指定技能增效的原始词条名
    dingyin: dict = field(default_factory=dict)
    extra_data: dict = field(default_factory=dict)  # 辅助信息，如 {"affix_count": 5}
    warnings: list[str] = field(default_factory=list)

    @property
    def category(self) -> str:
        """从 type 推断装备类别：weapon / jewelry / armor / unknown"""
        from .constants import infer_category
        return infer_category(self.type)

    @property
    def part(self) -> str:
        """从 type 推断部位：武器（不分主/副）/ 环 / 佩 / 冠胄 / 胸甲 / 胫甲 / 腕甲 / unknown"""
        from .constants import infer_part
        return infer_part(self.type)

    @property
    def weapon(self) -> str | None:
        """武器类型（剑/枪/扇/...）；部位非武器时为 None"""
        from .constants import WEAPON_TYPES_SET
        return self.type if self.type in WEAPON_TYPES_SET else None

    def to_dict(self) -> dict:
        """转换为标准装备领域模型 JSON dict

        affixes 列表展开为 affix_1 ~ affix_5 键。
        """
        d: dict = {
            "type": self.type,
            "name": self.name,
            "level": self.level,
            "quality": self.quality,
            "is_chengyin": self.is_chengyin,
            "base_attr": self.base_attr.to_dict() if self.base_attr else None,
            "base_attr_2": self.base_attr_2.to_dict() if self.base_attr_2 else None,
        }
        for i, affix in enumerate(self.affixes, 1):
            d[f"affix_{i}"] = affix.to_dict()
            # 记录转律词条位置供 DSL 快速定位
            if affix.is_transferred:
                self.extra_data["transferred_affix"] = f"affix_{i}"
        d["dingyin"] = self.dingyin if self.dingyin else None
        if self.warnings:
            d["_warnings"] = self.warnings
        if self.extra_data:
            d["_extra"] = self.extra_data
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
            type=d.get("type"),
            name=d.get("name"),
            level=d.get("level"),
            quality=d.get("quality"),
            is_chengyin=d.get("is_chengyin", False),
            base_attr=EquipAttr.from_dict(d["base_attr"]) if d.get("base_attr") else None,
            base_attr_2=EquipAttr.from_dict(d["base_attr_2"]) if d.get("base_attr_2") else None,
            affixes=affixes,
            dingyin=d.get("dingyin") or {},
            warnings=d.get("_warnings", []),
            extra_data=d.get("_extra", {}),
        )
