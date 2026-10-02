"""用户 Profile 数据模型配置加载

从 config/session/profile.yaml 加载按模型归档的 key 定义。
profile.yaml 结构：

    quota:
      - key: weekly_task
        label: 周任务
        period: week
        ...
    regen:
      - key: energy
        ...
    stock:
      ...

此模块仅负责定义加载，不负责运行时数据存储（运行时数据在 profile.db，
按 username 隔离）。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR
from lvjiang.core.config import load_yaml, save_yaml

from ...i18n import tr
from .models import (
    ALL_MODELS,
    MODEL_NOTE,
    MODEL_QUOTA,
    KeyDef,
    parse_key_def,
    parse_sync_key,
)
from .periods import get_profile_period

# 配置文件路径（用户级数据，位于 session 目录）
_PROFILE_PATH = SESSION_CONFIG_DIR / "profile.yaml"

# 全局单例
_config: ProfileSchema | None = None


@dataclass
class ProfileSchema:
    """用户 Profile 数据模型配置

    按模型类型归档存储 key 定义，同时提供扁平视图和按模型查询。
    """

    # 按模型类型归档：{"quota": [QuotaKeyDef, ...], "regen": [...], ...}
    keys_by_model: dict[str, list[KeyDef]] = field(default_factory=dict)

    # ── 以下两项是"看不懂但必须原样留着"的数据 ──
    #
    # 周期由插件注册（例如 yysls 的 season）。不加载该插件时核心无法解释这些
    # 定义，但**绝不能因此改动或删除它们**：profile.yaml 是用户数据，下一次
    # 带着插件启动还要照原样用。所以这里把它们按原始 dict 留在一边，既不进
    # 索引（所有消费者自然忽略），写回时又能一字不差地还原。
    #
    #: 已知模型下无法解释的条目：{模型类型: [(原始下标, 原始 dict), ...]}
    ignored_by_model: dict[str, list[tuple[int, dict[str, Any]]]] = field(
        default_factory=dict)
    #: 整段不认识的顶层小节（插件自带的模型类型），原样保留
    unknown_sections: dict[str, Any] = field(default_factory=dict)

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

    def get_key(self, key: str, model_type: str | None = None) -> KeyDef | None:
        """按 key 获取定义

        model_type 可选：指定时校验 key 所属模型，不匹配返回 None。
        """
        kd = self._keys_by_key.get(key)
        if kd is None:
            return None
        if model_type is not None and self._model_of.get(key) != model_type:
            return None
        return kd

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
        """序列化为 YAML 兼容的 dict

        无法解释的条目按原始下标插回原位，整段不认识的小节原样带回：写回
        profile.yaml 时不得删除或改写任何一条用户定义。
        """
        result: dict[str, Any] = {}
        for model_type in ALL_MODELS:
            items = [k.to_dict() for k in self.keys_by_model.get(model_type, [])]
            for index, raw in self.ignored_by_model.get(model_type, []):
                items.insert(min(index, len(items)), dict(raw))
            if items:
                result[model_type] = items
        for section, payload in self.unknown_sections.items():
            if section not in result:
                result[section] = payload
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
        raise ValueError(tr("profile.yaml 为旧格式（含 fields/groups），请手动更新为新格式"))

    # 新格式：按模型类型加载。不属于已知模型的顶层小节整段留底——那通常是
    # 未加载插件自带的模型，核心看不懂，但不是脏数据。
    unknown_sections = {
        str(section): payload for section, payload in data.items()
        if str(section) not in ALL_MODELS
    }
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

    _validate_sync_targets(keys_by_model)
    ignored = _set_aside_unknown_periods(keys_by_model)
    return ProfileSchema(
        keys_by_model=keys_by_model,
        ignored_by_model=ignored,
        unknown_sections=unknown_sections,
    )


def _set_aside_unknown_periods(
    keys_by_model: dict[str, list[KeyDef]],
) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    """把用了未注册周期的 quota 定义挪出索引，原样留底。

    周期由插件注册（yysls 的 season 就是其一）。不加载该插件时核心算不出它的
    周期边界，后台 tick 会持续失败——所以这些定义不能进索引。但也**不能因此
    判定整个 profile.yaml 无效**：那会让所有 Profile 功能一起瘫掉，而问题只在
    一条定义上。更不能顺手删掉或改掉它的周期：这是用户数据，下次带着插件启动
    还要照原样用。

    返回 {模型类型: [(原始下标, 原始 dict), ...]}，由 to_dict 插回原位。
    """
    ignored: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    kept: list[KeyDef] = []
    for index, key_def in enumerate(keys_by_model.get(MODEL_QUOTA, [])):
        period = getattr(key_def, "period", "")
        if period and get_profile_period(period) is None:
            logger.warning(
                f"profile.yaml 中 quota:{key_def.key} 使用了未注册周期 "
                f"{period!r}（通常是未加载对应插件），本次忽略该定义；"
                f"它会原样保留在文件里，不会被修改或删除"
            )
            ignored.setdefault(MODEL_QUOTA, []).append(
                (index, dict(key_def.raw) if key_def.raw else key_def.to_dict()))
            continue
        kept.append(key_def)
    if MODEL_QUOTA in ignored:
        keys_by_model[MODEL_QUOTA] = kept
    return ignored


def _validate_sync_targets(keys_by_model: dict[str, list[KeyDef]]) -> None:
    """校验 sync_targets 配置：note 模型不能作为同步目标"""
    note_keys = {kd.key for kd in keys_by_model.get(MODEL_NOTE, [])}
    if not note_keys:
        return
    for model_type, kds in keys_by_model.items():
        for kd in kds:
            for st in kd.sync_targets:
                st_model, st_key = parse_sync_key(st.key)
                if st_model == MODEL_NOTE and st_key in note_keys:
                    logger.warning(
                        f"sync_targets 配置错误: "
                        f"{model_type}:{kd.key} 的目标 {st.key} 是 note 模型，"
                        f"note 不参与同步，该目标将被忽略"
                    )


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


def _reject_unknown_periods(keys_by_model: dict[str, list[KeyDef]]) -> None:
    """保存前拒绝写出核心无法解释的新周期。

    针对的是编辑器里新建/改出来的定义；加载时留底的那些不在索引里，不受这条
    约束，也正因此才能原样写回。
    """
    for key_def in keys_by_model.get(MODEL_QUOTA, []):
        period = getattr(key_def, "period", "")
        if period and get_profile_period(period) is None:
            raise ValueError(
                f"profile.yaml 中 quota:{key_def.key} "
                f"使用了未注册周期 {period!r}"
            )


def _carry_over_preserved(schema: ProfileSchema) -> ProfileSchema:
    """把"看不懂但必须留着"的部分带到待保存的 schema 上。

    定义编辑器是从自己的草稿新建 ProfileSchema 的——它看不见留底数据（那正是
    "忽略"的本意），自然也带不上。不在这里补回去，保存一次就等于替用户把这些
    定义删了。放在保存入口而不是每个调用方，是因为这件事不能依赖调用方记得。
    """
    if schema.ignored_by_model or schema.unknown_sections:
        return schema
    current = _config
    if current is None:
        try:
            current = _load_config()
        except Exception as exc:  # noqa: BLE001 — 读不出来就没得保留
            logger.warning(f"读取现有 profile.yaml 失败，无法保留未识别定义: {exc}")
            return schema
    if not current.ignored_by_model and not current.unknown_sections:
        return schema
    return replace(
        schema,
        ignored_by_model=deepcopy(current.ignored_by_model),
        unknown_sections=deepcopy(current.unknown_sections),
    )


def save_profile_config(schema: ProfileSchema) -> None:
    """保存配置到 profile.yaml"""
    try:
        # 只校验进了索引的定义——留底的那些本来就是核心解释不了的，拿它们
        # 去校验只会把用户锁在"存不进去"的状态里。
        _reject_unknown_periods(schema.keys_by_model)
        data = _carry_over_preserved(schema).to_dict()
        _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_yaml(_PROFILE_PATH, data)
        logger.info(f"已保存 profile.yaml: {_PROFILE_PATH}")
    except Exception as e:
        logger.error(f"保存 profile.yaml 失败: {e}")
        raise
