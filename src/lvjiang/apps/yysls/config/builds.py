"""出装搭配独立存储。编辑器/模拟器共用，绝不写角色基础属性或备战方案。"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from uuid import uuid4

import fasteners

from ....core.config.resolver import (
    ConfigResolver,
    SystemContentProtected,
    get_resolver,
)
from .equipment_slots import EQUIPMENT_SLOTS

BUILDS_PATH = "yysls/game_config/builds.yaml"
REQUIREMENT_PRIORITIES = ("required", "optimal", "recommended")


@dataclass
class BuildDefinition:
    id: str
    name: str
    playstyle: str
    level: int
    chengyin: bool = True
    combat_type: str = "pve"
    gongjue: str = ""
    gongjue_level: int = 0
    equipment: dict[str, dict] = field(default_factory=dict)
    requirements: list[dict] = field(default_factory=list)

    @classmethod
    def create(cls, name: str, playstyle: str, level: int) -> BuildDefinition:
        return cls(uuid4().hex, name, playstyle, level)

    @classmethod
    def from_dict(cls, key: str, data: dict) -> BuildDefinition:
        return cls(
            id=key, name=str(data.get("name") or ""), playstyle=str(data.get("playstyle") or ""),
            level=int(data.get("level") or 0), chengyin=bool(data.get("chengyin", True)),
            combat_type=str(data.get("combat_type") or "pve"),
            gongjue=str(data.get("gongjue") or ""), gongjue_level=int(data.get("gongjue_level") or 0),
            equipment=copy.deepcopy(data.get("equipment") or {}),
            requirements=copy.deepcopy(data.get("requirements") or []),
        )

    def to_dict(self) -> dict:
        from ..core.loadout.affix_distribution import equipment_template

        data = asdict(self)
        data.pop("id")
        data["equipment"] = equipment_template(self.equipment)
        data["requirements"] = [
            {key: row[key] for key in ("affix", "priority", "minimum", "maximum") if key in row}
            for row in self.requirements
        ]
        return data

    def validate(self) -> None:
        from ..core.loadout.affix_distribution import TOTAL_AFFIXES_MAX

        if not self.name.strip() or not self.playstyle or self.level <= 0:
            raise ValueError("请填写出装名称、玩法及装备等级")
        if self.combat_type not in ("pve", "pvp"):
            raise ValueError("请选择 PVE 或 PVP")
        if set(self.equipment) - set(EQUIPMENT_SLOTS):
            raise ValueError("出装搭配包含未知装备槽位")
        for row in self.requirements:
            if not row.get("affix") or row.get("priority") not in REQUIREMENT_PRIORITIES:
                raise ValueError("要求必须选择词条和优先级")
            lo, hi = row.get("minimum", 0), row.get("maximum", TOTAL_AFFIXES_MAX)
            if (not isinstance(lo, int) or not isinstance(hi, int)
                    or not 0 <= lo <= hi <= TOTAL_AFFIXES_MAX):
                raise ValueError(
                    f"要求的数量范围必须满足 0 ≤ 最少 ≤ 最多 ≤ {TOTAL_AFFIXES_MAX}")


class BuildRepository:
    def __init__(self, resolver: ConfigResolver | None = None):
        self.resolver = resolver or get_resolver()

    def all(self, playstyle: str | None = None) -> list[BuildDefinition]:
        document = self.resolver.load_merged(BUILDS_PATH)
        result = []
        for key, raw in (document.get("builds") or {}).items():
            if not isinstance(raw, dict) or (playstyle is not None and raw.get("playstyle") != playstyle):
                continue
            try:
                result.append(BuildDefinition.from_dict(key, raw))
            except (TypeError, ValueError) as exc:
                from loguru import logger
                logger.error("出装搭配 {} 无法读取，请检查等级和数据格式：{}", key, exc)
        return result

    def common_requirements(self, combat_type: str) -> list[dict]:
        raw = self.resolver.load_merged(BUILDS_PATH)
        return copy.deepcopy((raw.get("common_requirements") or {}).get(combat_type) or [])

    def save(self, build: BuildDefinition, *, expected: dict | None = None) -> None:
        build.validate()
        self._mutate(build.id, build.to_dict(), expected)

    def delete(self, build: BuildDefinition) -> None:
        if not self.can_delete(build):
            raise SystemContentProtected("系统预设不能删除，请复制后编辑")
        self._mutate(build.id, None, build.to_dict())

    def can_delete(self, build: BuildDefinition) -> bool:
        if self.resolver.is_dev_mode():
            return True
        # 和 load_merged/save_merged 选择同一个有效基底，远端新增预设也受保护。
        base_dir = (self.resolver.remote_dir if self.resolver.remote_supersedes(BUILDS_PATH)
                    else self.resolver.system_dir)
        return build.id not in (self.resolver._load_yaml(base_dir / BUILDS_PATH).get("builds") or {})

    def _mutate(self, key: str, value: dict | None, expected: dict | None) -> None:
        # 多窗口/多进程只合并当前实体；比较打开时快照，拒绝覆盖同一搭配的新修改。
        lock_path = self.resolver.local_dir / "yysls/game_config/builds.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with fasteners.InterProcessLock(str(lock_path)):
            document = copy.deepcopy(self.resolver.load_merged(BUILDS_PATH))
            rows = document.setdefault("builds", {})
            current = rows.get(key)
            if current != expected:
                raise ValueError("该出装搭配已被其他窗口修改或删除，请重新加载；当前草稿未丢失")
            if value is None:
                rows.pop(key, None)
            else:
                rows[key] = value
            document.setdefault("content_version", 1)
            self.resolver.save_merged(BUILDS_PATH, document)


def check_requirements(counts: dict[str, int], requirements: list[dict]) -> list[dict]:
    """不篡改用户目标；要求的满足程度供 UI 与后续智能分析共同消费。"""
    from ..core.loadout.affix_distribution import TOTAL_AFFIXES_MAX

    return [
        {**copy.deepcopy(row), "actual": counts.get(row["affix"], 0),
         "satisfied": (row.get("minimum", 0) <= counts.get(row["affix"], 0)
                       <= row.get("maximum", TOTAL_AFFIXES_MAX))}
        for row in requirements
    ]
