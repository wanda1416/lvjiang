"""出装搭配独立存储。编辑器/模拟器共用，绝不写角色基础属性或备战方案。"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from uuid import uuid4

import fasteners
import yaml
from loguru import logger

from ....core.config.resolver import (
    ConfigResolver,
    SystemContentProtected,
    get_resolver,
)
from .equipment_slots import EQUIPMENT_SLOTS

GEAR_SETS_DIR = "yysls/gear_sets"
BUILD_SCHEMA_VERSION = 2
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
    storage: str = "local"  # 读取来源／保存目标；不写进文件。
    content_version: int = 1

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
            content_version=int(data.get("content_version") or 1),
        )

    def to_dict(self) -> dict:
        from ..core.loadout.affix_distribution import equipment_template

        data = asdict(self)
        data.pop("storage")
        data = {"content_version": data.pop("content_version"), "schema_version": BUILD_SCHEMA_VERSION, **data}
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
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", self.id):
            raise ValueError("搭配 ID 格式无效")
        if self.storage not in ("system", "local"):
            raise ValueError("请选择系统预置或本地保存位置")
        if self.combat_type != "pve":
            raise ValueError("出装搭配目前只支持 PVE")
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
        result = []
        for filename in self.resolver.enumerate_entities(GEAR_SETS_DIR, "*.yaml"):
            rel_path = f"{GEAR_SETS_DIR}/{filename}"
            path = self.resolver.resolve_read(rel_path)
            if path is None:
                continue
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or raw.get("schema_version") != BUILD_SCHEMA_VERSION:
                    raise ValueError("搭配文件结构版本不支持")
                if raw.get("id") != filename.removesuffix(".yaml"):
                    raise ValueError("搭配 ID 与文件名不一致")
                build = BuildDefinition.from_dict(raw["id"], raw)
                build.storage = "local" if path == self.resolver.local_dir / rel_path else "system"
                build.validate()
                if playstyle is None or build.playstyle == playstyle:
                    result.append(build)
            except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                logger.error("出装搭配 {} 无法读取，请检查文件：{}", filename, exc)
        return sorted(result, key=lambda build: (build.storage != "system", build.name, build.id))

    def save(self, build: BuildDefinition, *, expected: dict | None = None) -> None:
        build.validate()
        if not self.can_write(build):
            raise SystemContentProtected("系统预置只读，请复制或另存为本地搭配")
        self._mutate(build, build.to_dict(), expected)

    def delete(self, build: BuildDefinition) -> None:
        build.validate()
        if not self.can_delete(build):
            raise SystemContentProtected("系统预设不能删除，请复制后编辑")
        self._mutate(build, None, build.to_dict())

    def can_delete(self, build: BuildDefinition) -> bool:
        return self.can_write(build) and (self.resolver.is_dev_mode() or not self.resolver.is_system_entity(
            f"{GEAR_SETS_DIR}/{build.id}.yaml"))

    def can_write(self, build: BuildDefinition) -> bool:
        return build.storage == "local" or self.resolver.is_dev_mode()

    def _mutate(self, build: BuildDefinition, value: dict | None, expected: dict | None) -> None:
        rel_path = f"{GEAR_SETS_DIR}/{build.id}.yaml"
        lock_path = self.resolver.local_dir / f"{GEAR_SETS_DIR}/.locks/{build.id}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with fasteners.InterProcessLock(str(lock_path)):
            current = next((item for item in self.all() if item.id == build.id), None)
            if (current is None and self.resolver.resolve_read(rel_path) is not None) or (
                    (current.to_dict() if current else None) != expected) or (
                    current is not None and current.storage != build.storage):
                raise ValueError("该出装搭配已被其他窗口修改或删除，请重新加载；当前草稿未丢失")
            if value is None:
                self.resolver.delete_entity(rel_path, layer=build.storage)
            else:
                self.resolver.write_entity(rel_path, yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
                                           layer=build.storage, force=True)


def check_requirements(counts: dict[str, int], requirements: list[dict]) -> list[dict]:
    """不篡改用户目标；要求的满足程度供 UI 与后续智能分析共同消费。"""
    from ..core.loadout.affix_distribution import TOTAL_AFFIXES_MAX

    return [
        {**copy.deepcopy(row), "actual": counts.get(row["affix"], 0),
         "satisfied": (row.get("minimum", 0) <= counts.get(row["affix"], 0)
                       <= row.get("maximum", TOTAL_AFFIXES_MAX))}
        for row in requirements
    ]
