"""燕云游戏配置的七文件存储边界。

业务层仍使用一份扁平 ``dict``，本模块只负责在读取时合并、保存时按领域拆分，
避免 UI 和计算代码感知物理文件结构。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

import yaml
from loguru import logger

from ....core.config.resolver import ConfigResolver, get_resolver

GAME_CONFIG_DIR = "yysls/game_config"

# 文件顺序同时作为稳定的展示顺序。每个顶层字段只能属于一个文件。
GAME_CONFIG_FILES: dict[str, tuple[str, ...]] = {
    f"{GAME_CONFIG_DIR}/basic.yaml": ("basic_config",),
    # 赛季设置：赛季本身与赛季关联的装备等级能力
    f"{GAME_CONFIG_DIR}/seasons.yaml": (
        "level_configs",
        "season_configs",
    ),
    f"{GAME_CONFIG_DIR}/affixes.yaml": (
        "affix_caps",
        "affix_categories",
        "affix_aliases",
        "affix_parts",
    ),
    f"{GAME_CONFIG_DIR}/equipment.yaml": (
        "base_attrs",
        "equipment_sets",
        "gongjue_bonuses",
        "equipment_name_series",
        "weapon_types",
    ),
    f"{GAME_CONFIG_DIR}/martial_arts.yaml": ("martial_arts",),
    f"{GAME_CONFIG_DIR}/schools.yaml": ("schools",),
    f"{GAME_CONFIG_DIR}/playstyles.yaml": ("playstyles",),
}

GAME_CONFIG_SECTION_FILES = {
    section: rel_path
    for rel_path, sections in GAME_CONFIG_FILES.items()
    for section in sections
}


def game_config_file_for(section: str) -> str | None:
    """返回顶层字段所属文件；未知字段返回 ``None``。"""
    return GAME_CONFIG_SECTION_FILES.get(section)


def load_game_config(resolver: ConfigResolver | None = None) -> dict:
    """读取七份分层配置并合并为业务层使用的扁平文档。"""
    resolver = resolver or get_resolver()
    for root in (resolver.local_dir,):
        old_path = root / GAME_CONFIG_DIR / "levels_and_seasons.yaml"
        if old_path.is_file():
            logger.warning(
                f"旧赛季配置 {old_path} 不再读取；请备份后手工改名为 seasons.yaml；"
                "若新文件已存在，请核对合并，勿直接覆盖")

    merged: dict = {}
    for rel_path, sections in GAME_CONFIG_FILES.items():
        data = resolver.load_merged(rel_path)
        if not isinstance(data, dict):
            raise ValueError(f"游戏配置必须是 dict: {rel_path}")
        for section in sections:
            if section in data:
                merged[section] = deepcopy(data[section])
    return merged


def save_game_config(
    data: Mapping[str, object],
    resolver: ConfigResolver | None = None,
) -> list[str]:
    """把完整业务文档按固定归属写回七份配置，只写内容真正变过的文件。

    每份文件保留自己的 ``content_version``；版本是远程发布代次，不因普通保存
    自动增长。

    只写变化的文件不只是省 IO：``save_merged`` 会做并发检查（磁盘在本窗口
    打开后被改过就要求确认），无条件写七份会让「只改了词条别名」也因另一个
    文件被别的窗口保存而整次失败。返回实际写入的相对路径，便于调用方记录。
    """
    resolver = resolver or get_resolver()
    unknown = set(data) - set(GAME_CONFIG_SECTION_FILES)
    if unknown:
        raise ValueError(f"游戏配置包含未知顶层字段: {', '.join(sorted(unknown))}")
    written: list[str] = []
    for rel_path, sections in GAME_CONFIG_FILES.items():
        current = resolver.load_merged(rel_path)
        document = {
            "content_version": current.get("content_version", 1),
            **{
                section: deepcopy(data[section])
                for section in sections
                if section in data
            },
        }
        if _same_content(current, document):
            continue
        resolver.save_merged(rel_path, document)
        written.append(rel_path)
    return written


def _same_content(current: Mapping[str, object], document: Mapping[str, object]) -> bool:
    """忽略 ``content_version`` 比对业务内容。

    版本号由写盘自动递增，且生效层（system/local）的版本与即将写入的版本
    本来就可能不同；拿它参与比对会让每份文件都被判成「有变化」。

    **比对必须对键序敏感**：武学/流派/玩法是「名称 → 定义」的映射，键序就是
    展示顺序，纯排序保存时 Python 的 dict 相等判定认为「没变化」，会把开发模式
    下的拖动结果整次丢掉。这里改用规范化文本比对（``sort_keys=False``）。
    """
    def payload(doc: Mapping[str, object]) -> dict[str, object]:
        return {key: value for key, value in doc.items() if key != "content_version"}

    if payload(current) == payload(document):
        # 值完全相同，但键序可能不同——用文本序列化再确认一次
        dumped = [
            yaml.safe_dump(payload(doc), allow_unicode=True,
                           default_flow_style=False, sort_keys=False)
            for doc in (current, document)
        ]
        return dumped[0] == dumped[1]
    return False


def system_game_config_section(
    section: str,
    resolver: ConfigResolver | None = None,
) -> dict:
    """读取某个字段所在的 system 原始文档，供系统条目删除守卫使用。"""
    rel_path = game_config_file_for(section)
    if rel_path is None:
        return {}
    return (resolver or get_resolver()).load_system(rel_path)


def game_config_is_customized(resolver: ConfigResolver | None = None) -> bool:
    """任意一份游戏配置存在 local 覆盖即视为使用了自定义配置。"""
    resolver = resolver or get_resolver()
    return any((resolver.local_dir / rel_path).is_file()
               for rel_path in GAME_CONFIG_FILES)


__all__ = [
    "GAME_CONFIG_DIR",
    "GAME_CONFIG_FILES",
    "GAME_CONFIG_SECTION_FILES",
    "game_config_file_for",
    "game_config_is_customized",
    "load_game_config",
    "save_game_config",
    "system_game_config_section",
]
