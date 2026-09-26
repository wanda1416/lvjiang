"""基础属性配置存储（兼容旧的 play_styles 命名）。

基础属性数据属于会话级数据（由面板属性反推），不应提交到 git。
存储在 ``config/session/yysls/play_styles.json``：

    {
        流派名: {基础属性名称: {field_name: value, ...}}
    }
此文件与用户无关，所有用户共享同一套基础属性配置。
"""
from __future__ import annotations

from loguru import logger

from . import session_node


def _load() -> dict:
    """读取基础属性文档。"""
    return session_node.load("play_styles")


def get_play_styles(school: str) -> dict[str, dict]:
    """获取指定流派的全部基础属性配置。

    Args:
        school: 流派名称

    Returns:
        基础属性字典：名称 → {field_name: value, ...}
    """
    data = _load()
    return dict(data.get(school) or {})


def save_play_style(school: str, name: str, attrs: dict, *,
                    derivation: dict | None = None) -> None:
    """保存一套基础属性。

    Args:
        school: 流派名称
        name: 基础属性名称
        attrs: 属性字典 {field_name: value}
        derivation: 同次推导上下文；手动保存时清除旧上下文。
    """
    def _apply(documents: dict[str, dict]) -> dict[str, dict]:
        styles = documents["play_styles"]
        derivations = documents["attr_derivations"]
        styles.setdefault(school, {})[name] = attrs
        if derivation is None:
            (derivations.get(school) or {}).pop(name, None)
        else:
            derivations.setdefault(school, {})[name] = derivation
        return documents

    session_node.mutate_many(("play_styles", "attr_derivations"), _apply)
    logger.debug(f"已保存基础属性: {school}/{name}")


def delete_play_style(school: str, name: str) -> None:
    """删除一套基础属性。

    Args:
        school: 流派名称
        name: 基础属性名称
    """
    def _apply(documents: dict[str, dict]) -> dict[str, dict]:
        documents["play_styles"].get(school, {}).pop(name, None)
        # 推导上下文与基础属性同名同流派，留着会让同名的新配置读到旧装配
        (documents["attr_derivations"].get(school) or {}).pop(name, None)
        return documents

    session_node.mutate_many(("play_styles", "attr_derivations"), _apply)
    logger.debug(f"已删除基础属性: {school}/{name}")


def rename_play_style(school: str, old_name: str, new_name: str) -> None:
    """重命名一套基础属性。

    Args:
        school: 流派名称
        old_name: 旧名称
        new_name: 新名称
    """
    def _apply(documents: dict[str, dict]) -> dict[str, dict]:
        school_styles = documents["play_styles"].get(school, {})
        if old_name in school_styles:
            school_styles[new_name] = school_styles.pop(old_name)
        # 推导上下文按名字索引，不跟着搬就查不回这套是怎么推出来的
        derivations = documents["attr_derivations"].get(school, {})
        if old_name in derivations:
            derivations[new_name] = derivations.pop(old_name)
        return documents

    session_node.mutate_many(("play_styles", "attr_derivations"), _apply)
    logger.debug(f"已重命名基础属性: {school}/{old_name} → {new_name}")
