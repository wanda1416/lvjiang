"""玩家信息元数据配置加载

从 config/session/profile.yaml 加载字段定义和分组信息。
此模块仅负责定义 user.json 的字段 schema，不负责展示控制。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR
from lvjiang.core.config import load_yaml

# 配置文件路径（用户级数据，位于 session 目录）
_PROFILE_PATH = SESSION_CONFIG_DIR / "profile.yaml"

# 全局单例
_config: ProfileConfig | None = None


@dataclass
class FieldDef:
    """字段元数据定义"""
    key: str
    label: str
    group: str
    type: str = "str"
    source: str = ""
    readonly: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FieldDef:
        return cls(
            key=data.get("key", ""),
            label=data.get("label", ""),
            group=data.get("group", ""),
            type=data.get("type", "str"),
            source=data.get("source", ""),
            readonly=data.get("readonly", False),
        )


@dataclass
class GroupDef:
    """分组定义"""
    key: str
    label: str
    order: int = 0

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> GroupDef:
        return cls(
            key=key,
            label=data.get("label", key),
            order=data.get("order", 0),
        )


@dataclass
class ProfileConfig:
    """玩家信息元数据配置"""
    fields: list[FieldDef] = field(default_factory=list)
    groups: list[GroupDef] = field(default_factory=list)

    # 缓存：按 key 索引字段
    _fields_by_key: dict[str, FieldDef] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._fields_by_key = {f.key: f for f in self.fields}

    def get_field(self, key: str) -> FieldDef | None:
        """按 key 获取字段定义"""
        return self._fields_by_key.get(key)

    def get_all_fields(self) -> list[FieldDef]:
        """获取所有字段（按定义顺序）"""
        return list(self.fields)

    def get_fields_by_group(self, group_key: str) -> list[FieldDef]:
        """获取指定分组的字段"""
        return [f for f in self.fields if f.group == group_key]

    def get_sorted_groups(self) -> list[GroupDef]:
        """获取按 order 排序的分组列表"""
        return sorted(self.groups, key=lambda g: g.order)


def _create_default_config():
    """创建默认的 profile.yaml（首次运行时）"""
    from lvjiang.core.config import save_yaml

    default_data = {
        "fields": [],
        "groups": {
            "basic": {"label": "基础", "order": 1},
        },
    }
    try:
        _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_yaml(_PROFILE_PATH, default_data)
        logger.info(f"已创建默认 profile.yaml: {_PROFILE_PATH}")
    except Exception as e:
        logger.error(f"创建默认 profile.yaml 失败: {e}")


def _load_config() -> ProfileConfig:
    """从 YAML 加载配置，文件不存在时创建默认配置"""
    # 一次性迁移：旧 profiles.yaml → 新 profile.yaml
    _legacy = SESSION_CONFIG_DIR / "profiles.yaml"
    if not _PROFILE_PATH.exists() and _legacy.exists():
        try:
            _legacy.rename(_PROFILE_PATH)
            logger.info(f"已迁移旧配置: {_legacy} → {_PROFILE_PATH}")
        except OSError as e:
            logger.warning(f"迁移旧配置失败: {e}")

    if not _PROFILE_PATH.exists():
        logger.info(f"profile.yaml 不存在，创建默认配置: {_PROFILE_PATH}")
        _create_default_config()

    try:
        data = load_yaml(_PROFILE_PATH)
    except Exception as e:
        logger.error(f"加载 profile.yaml 失败: {e}")
        return ProfileConfig()

    # 解析字段
    fields = [FieldDef.from_dict(f) for f in data.get("fields", [])]

    # 解析分组
    groups_data = data.get("groups", {})
    groups = [GroupDef.from_dict(k, v) for k, v in groups_data.items()]

    return ProfileConfig(fields=fields, groups=groups)


def get_profile_config() -> ProfileConfig:
    """获取全局配置单例（懒加载）"""
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def reload_profile_config() -> ProfileConfig:
    """重新加载配置（用于元数据定义保存后刷新）"""
    global _config
    _config = _load_config()
    return _config
