"""燕云游戏配置的七文件存储边界。

业务层仍使用一份扁平 ``dict``，本模块只负责在读取时合并、保存时按领域拆分，
避免 UI 和计算代码感知物理文件结构。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from ....core.config.resolver import ConfigResolver, get_resolver

GAME_CONFIG_DIR = "yysls/game_config"

# 文件顺序同时作为稳定的展示顺序。每个顶层字段只能属于一个文件。
GAME_CONFIG_FILES: dict[str, tuple[str, ...]] = {
    f"{GAME_CONFIG_DIR}/basic.yaml": (
        "basic_config",
        "equip_display",
    ),
    f"{GAME_CONFIG_DIR}/levels_and_seasons.yaml": (
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
) -> None:
    """把完整业务文档按固定归属写回七份配置。

    每份文件保留自己的 ``content_version``；版本是远程发布代次，不因普通保存
    自动增长。
    """
    resolver = resolver or get_resolver()
    unknown = set(data) - set(GAME_CONFIG_SECTION_FILES)
    if unknown:
        raise ValueError(f"游戏配置包含未知顶层字段: {', '.join(sorted(unknown))}")
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
        resolver.save_merged(rel_path, document)


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
