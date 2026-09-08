"""伤害模型 YAML 的解析与 schema 校验

.. code-block:: yaml

    school: 鸣金·虹
    scheme: 基础方案
    source:                       # 与方案 JSON 同源，对不上就不是配套的
      file: 鸣金虹110阶竞速轴属性毕业率进阶计算器2.0.xlsx
      version: "2.0"
      sha256: 1190d212...
    skills:
      第一道剑气:
        kind: 剑
        charge: true              # 定音加成 = 蓄力技
        qi_ratio: 1.1
        outer_ratio: 1.3066
        outer_fixed: 361
        attr_ratio: 1.9598
        attr_fixed: 197
        modifiers: { generic: 0.2 }
      第一道剑气(气竭):
        force: { force_precision: true }
    buffs:
      远程笛: { generic: 0.2 }
      玉斗:   { intent_dmg: 0.1, direct_intent: 0.075 }

校验从严，未知字段一律在解析期失败。这份配置是给人读、给人改的，
写错了要当场知道；静默跳过一个拼错的字段，只会让人对着一个不动的
数字找半天。
"""

from __future__ import annotations

from typing import Any

from .....i18n import tr
from .models import (
    FORCE_FLAGS,
    MODIFIER_FIELDS,
    RATIO_FIELDS,
    DamageBuff,
    DamageModel,
    DamageModelError,
    DamageSkill,
)

#: 技能条目允许的键
_SKILL_KEYS = set(RATIO_FIELDS) | {"kind", "charge", "qi_ratio", "modifiers", "force"}

#: 文件顶层允许的键
_DOCUMENT_KEYS = {"school", "scheme", "source", "skills", "buffs"}


def _mapping(value: Any, what: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DamageModelError(
            tr("{what} 必须是映射，实际是 {kind}").format(
                what=what, kind=type(value).__name__)
        )
    return value


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DamageModelError(
            tr("{what} 必须是数值，实际是 {kind}").format(
                what=what, kind=type(value).__name__)
        )
    return float(value)


def _flag(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise DamageModelError(tr("{what} 必须是布尔值").format(what=what))
    return value


def _parse_modifiers(raw: Any, what: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, value in _mapping(raw, what).items():
        if name not in MODIFIER_FIELDS:
            raise DamageModelError(
                tr("{what}.{name} 不是已知修正字段").format(what=what, name=name)
            )
        out[str(name)] = _number(value, f"{what}.{name}")
    return out


def parse_skill(name: str, raw: Any) -> DamageSkill:
    data = _mapping(raw, name)
    unknown = set(data) - _SKILL_KEYS
    if unknown:
        raise DamageModelError(
            tr("{source} 含未知字段: {keys}").format(
                source=name, keys="、".join(sorted(unknown)))
        )
    kind = data.get("kind") or ""
    if not isinstance(kind, str):
        raise DamageModelError(tr("{source} 的 kind 必须是字符串").format(source=name))
    force: dict[str, bool] = {}
    for flag, value in _mapping(data.get("force"), f"{name}.force").items():
        if flag not in FORCE_FLAGS:
            raise DamageModelError(
                tr("{what}.{name} 不是已知强制结算开关").format(
                    what=f"{name}.force", name=flag)
            )
        force[str(flag)] = _flag(value, f"{name}.force.{flag}")
    return DamageSkill(
        name=name,
        kind=kind,
        charge=_flag(data.get("charge", False), f"{name}.charge"),
        qi_ratio=_number(data.get("qi_ratio", 0.0), f"{name}.qi_ratio"),
        modifiers=_parse_modifiers(data.get("modifiers"), f"{name}.modifiers"),
        force=force,
        **{
            field_name: _number(data.get(field_name, 0.0), f"{name}.{field_name}")
            for field_name in RATIO_FIELDS
        },
    )


def parse_model(data: Any, *, filename: str) -> DamageModel:
    """解析一份伤害模型文件"""
    payload = _mapping(data, filename)
    unknown = set(payload) - _DOCUMENT_KEYS
    if unknown:
        raise DamageModelError(
            tr("{filename} 顶层含未知字段: {keys}").format(
                filename=filename, keys="、".join(sorted(unknown)))
        )
    school = payload.get("school")
    if not isinstance(school, str) or not school:
        raise DamageModelError(
            tr("{filename} 缺少 school").format(filename=filename)
        )
    scheme = payload.get("scheme") or ""
    if not isinstance(scheme, str):
        raise DamageModelError(
            tr("{filename} 的 scheme 必须是字符串").format(filename=filename)
        )
    source = {
        str(key): str(value)
        for key, value in _mapping(payload.get("source"), f"{filename}.source").items()
    }
    skills = tuple(
        parse_skill(str(name), raw)
        for name, raw in _mapping(payload.get("skills"), f"{filename}.skills").items()
    )
    buffs = tuple(
        DamageBuff(
            name=str(name),
            modifiers=_parse_modifiers(raw, f"{filename}.buffs.{name}"),
        )
        for name, raw in _mapping(payload.get("buffs"), f"{filename}.buffs").items()
    )
    return DamageModel(
        school=school, scheme=scheme, source=source, skills=skills, buffs=buffs)
