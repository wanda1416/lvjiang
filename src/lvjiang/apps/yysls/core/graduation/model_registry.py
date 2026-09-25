"""多等级、多版本毕业率模型的发现与选择。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from loguru import logger

from .....core.config.resolver import get_resolver

DATA_REL_DIR = "yysls/graduation"
MODEL_SCHEMA_VERSION = 3


@dataclass(frozen=True, order=True)
class GraduationModelRef:
    """一份可执行毕业率模型的稳定身份。"""

    school: str
    scheme: str
    level: int
    version: int
    rel_path: str


def model_filename(school: str, scheme: str, level: int, version: int) -> str:
    """返回模型实体文件名；身份仍以 JSON 元数据为准。"""
    cleaned = scheme.strip()
    if not cleaned or any(char in cleaned for char in '<>:"/\\|?*'):
        raise ValueError("方案名称为空或包含文件名非法字符")
    if level <= 0 or version <= 0:
        raise ValueError("方案等级和版本号必须大于 0")
    return f"{school}_{cleaned}_{level}_v{version}.json"


def model_rel_path(school: str, scheme: str, level: int, version: int) -> str:
    return f"{DATA_REL_DIR}/{model_filename(school, scheme, level, version)}"


def _parse_model(rel_path: str, data: object) -> GraduationModelRef | None:
    if not isinstance(data, dict) or data.get("schema_version") != MODEL_SCHEMA_VERSION:
        # 旧用户模型明确不兼容：不猜等级、不迁移，也不让它进入方案列表。
        return None
    school = str(data.get("school") or "").strip()
    scheme = str(data.get("scheme") or "").strip()
    level = data.get("model_level")
    version = data.get("model_version")
    if (not school or not scheme
            or not isinstance(level, int) or isinstance(level, bool) or level <= 0
            or not isinstance(version, int) or isinstance(version, bool)
            or version <= 0):
        logger.warning(f"毕业率模型身份不完整，已忽略: {rel_path}")
        return None
    return GraduationModelRef(school, scheme, level, version, rel_path)


@lru_cache(maxsize=1)
def list_graduation_models() -> tuple[GraduationModelRef, ...]:
    """枚举当前配置分层中所有新格式模型。"""
    resolver = get_resolver()
    refs: dict[tuple[str, str, int, int], GraduationModelRef] = {}
    for filename in resolver.enumerate_entities(DATA_REL_DIR, "*.json"):
        rel_path = f"{DATA_REL_DIR}/{filename}"
        path = resolver.resolve_read(rel_path)
        if path is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"毕业率模型读取失败，已忽略 {rel_path}: {exc}")
            continue
        ref = _parse_model(rel_path, data)
        if ref is None:
            continue
        key = (ref.school, ref.scheme, ref.level, ref.version)
        previous = refs.get(key)
        if previous is not None and previous.rel_path != ref.rel_path:
            logger.warning(
                "毕业率模型身份重复，保留文件名排序靠后的实体: "
                f"{previous.rel_path}, {ref.rel_path}")
        refs[key] = ref
    return tuple(sorted(
        refs.values(),
        key=lambda item: (item.school, item.scheme, item.level, item.version),
    ))


def available_models(
    school: str = "", scheme: str = "",
) -> tuple[GraduationModelRef, ...]:
    return tuple(
        ref for ref in list_graduation_models()
        if (not school or ref.school == school)
        and (not scheme or ref.scheme == scheme)
    )


def select_graduation_model(
    school: str, scheme: str, world_level: int,
) -> GraduationModelRef | None:
    """选不超过个人世界等级的最高模型等级，再取该等级最新版本。"""
    candidates = [
        ref for ref in available_models(school, scheme)
        if ref.level <= world_level
    ]
    return max(candidates, key=lambda item: (item.level, item.version),
               default=None)


@lru_cache(maxsize=None)
def load_model(rel_path: str) -> dict[str, Any]:
    path = get_resolver().resolve_read(rel_path)
    if path is None:
        raise FileNotFoundError(rel_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if _parse_model(rel_path, data) is None:
        raise ValueError(f"unsupported graduation model: {rel_path}")
    return data


def invalidate_model_registry() -> None:
    list_graduation_models.cache_clear()
    load_model.cache_clear()
