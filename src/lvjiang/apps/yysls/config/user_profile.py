"""玩家数据模型配置加载

从 config/session/profile.yaml 加载按模型归档的 key 定义。
profile.yaml 结构：

    daily:
      - key: niaoniao_of_week
        label: 袅袅(本周)
        period: week
        ...
    realtime:
      - key: tili
        ...
    resource:
      ...
    activity:
      ...

此模块仅负责定义加载，不负责运行时数据存储（运行时数据在 user.json 的 profile 节点）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR
from lvjiang.core.config import load_yaml, save_yaml

from .profile_models import (
    ALL_MODELS,
    KeyDef,
    parse_key_def,
)

# 配置文件路径（用户级数据，位于 session 目录）
_PROFILE_PATH = SESSION_CONFIG_DIR / "profile.yaml"

# 全局单例
_config: ProfileSchema | None = None


# ─── profile 节点读写（共享工具函数）────────────────────


def read_profile_entry(data: dict, model_type: str, key: str) -> dict:
    """从 user.json 数据中读取 profile 节点值

    Returns: {"value": ..., "total": ..., "updated_at": ...} 或空 dict
    """
    profile = data.get("profile", {})
    model_data = profile.get(model_type, {})
    return model_data.get(key, {})


def write_profile_entry(
    data: dict,
    model_type: str,
    key: str,
    value,
    total: int | None = None,
) -> None:
    """向 user.json 数据中写入 profile 节点值（就地修改 data）"""
    profile = data.setdefault("profile", {})
    model_data = profile.setdefault(model_type, {})
    entry = model_data.setdefault(key, {})
    entry["value"] = value
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if total is not None:
        entry["total"] = total


@dataclass
class ProfileSchema:
    """玩家数据模型配置

    按模型类型归档存储 key 定义，同时提供扁平视图和按模型查询。
    """

    # 按模型类型归档：{"daily": [DailyKeyDef, ...], "realtime": [...], ...}
    keys_by_model: dict[str, list[KeyDef]] = field(default_factory=dict)

    # 内部缓存
    _all_keys: list[KeyDef] = field(default_factory=list, repr=False)
    _keys_by_key: dict[str, KeyDef] = field(default_factory=dict, repr=False)
    _model_of: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._rebuild_index()

    def _rebuild_index(self):
        """重建内部索引"""
        self._all_keys = []
        self._keys_by_key = {}
        self._model_of = {}
        for model_type in ALL_MODELS:
            for key_def in self.keys_by_model.get(model_type, []):
                self._all_keys.append(key_def)
                self._keys_by_key[key_def.key] = key_def
                self._model_of[key_def.key] = model_type

    def get_key(self, key: str) -> KeyDef | None:
        """按 key 获取定义"""
        return self._keys_by_key.get(key)

    def get_all_keys(self) -> list[KeyDef]:
        """获取所有 key 定义（按定义顺序）"""
        return list(self._all_keys)

    def get_keys_by_model(self, model: str) -> list[KeyDef]:
        """获取指定模型类型的 key 定义"""
        return list(self.keys_by_model.get(model, []))

    def get_model_type(self, key: str) -> str | None:
        """获取指定 key 的模型类型"""
        return self._model_of.get(key)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 YAML 兼容的 dict"""
        result: dict[str, Any] = {}
        for model_type in ALL_MODELS:
            keys = self.keys_by_model.get(model_type, [])
            if keys:
                result[model_type] = [k.to_dict() for k in keys]
        return result


def _load_config() -> ProfileSchema:
    """从 YAML 加载配置

    文件不存在时返回空配置（正常首次运行）。
    文件存在但加载失败或格式无效时抛出异常。
    """
    if not _PROFILE_PATH.exists():
        logger.info(f"profile.yaml 不存在: {_PROFILE_PATH}")
        return ProfileSchema()

    data = load_yaml(_PROFILE_PATH)  # 加载失败直接抛异常

    # 检测旧格式
    if "fields" in data or "groups" in data:
        raise ValueError("profile.yaml 为旧格式（含 fields/groups），请手动更新为新格式")

    # 新格式：按模型类型加载
    keys_by_model: dict[str, list[KeyDef]] = {}
    for model_type in ALL_MODELS:
        items = data.get(model_type, [])
        if not isinstance(items, list):
            continue
        key_defs = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"profile.yaml 中 {model_type} 包含非 dict 条目: {item!r}")
            key_def = parse_key_def(model_type, item)  # 无效 key 直接抛异常
            if key_def.key:
                key_defs.append(key_def)
        keys_by_model[model_type] = key_defs

    return ProfileSchema(keys_by_model=keys_by_model)


def get_profile_config() -> ProfileSchema:
    """获取全局配置单例（懒加载）"""
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def reload_profile_config() -> ProfileSchema:
    """重新加载配置（用于定义保存后刷新）"""
    global _config
    _config = _load_config()
    return _config


def save_profile_config(schema: ProfileSchema) -> None:
    """保存配置到 profile.yaml"""
    try:
        data = schema.to_dict()
        _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_yaml(_PROFILE_PATH, data)
        logger.info(f"已保存 profile.yaml: {_PROFILE_PATH}")
    except Exception as e:
        logger.error(f"保存 profile.yaml 失败: {e}")
        raise
