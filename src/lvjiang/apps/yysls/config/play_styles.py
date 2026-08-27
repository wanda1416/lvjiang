"""基础属性配置存储（兼容旧的 play_styles 命名）。

基础属性数据属于会话级数据（由面板属性反推），不应提交到 git。
存储在 session.json 的 ``yysls`` 节点（旧的独立 yysls.json 仍可读，
见 session_node）：

    play_styles: {
        流派名: {
            基础属性名称: { field_name: value, ... }
        }
    }

此文件与用户无关，所有用户共享同一套基础属性配置。
"""
from __future__ import annotations

from loguru import logger

from . import session_node


def _load() -> dict:
    """读取插件会话节点（首次运行时回退旧的独立文件）"""
    return session_node.load()


def get_play_styles(school: str) -> dict[str, dict]:
    """获取指定流派的全部基础属性配置。

    Args:
        school: 流派名称

    Returns:
        基础属性字典：名称 → {field_name: value, ...}
    """
    data = _load()
    all_styles = data.get("play_styles", {})
    return dict(all_styles.get(school) or {})


def save_play_style(school: str, name: str, attrs: dict) -> None:
    """保存一套基础属性。

    Args:
        school: 流派名称
        name: 基础属性名称
        attrs: 属性字典 {field_name: value}
    """
    def _apply(data: dict) -> dict:
        all_styles = data.setdefault("play_styles", {})
        all_styles.setdefault(school, {})[name] = attrs
        return data

    session_node.mutate(_apply)
    logger.debug(f"已保存基础属性: {school}/{name}")


def delete_play_style(school: str, name: str) -> None:
    """删除一套基础属性。

    Args:
        school: 流派名称
        name: 基础属性名称
    """
    def _apply(data: dict) -> dict:
        data.get("play_styles", {}).get(school, {}).pop(name, None)
        return data

    session_node.mutate(_apply)
    logger.debug(f"已删除基础属性: {school}/{name}")


def rename_play_style(school: str, old_name: str, new_name: str) -> None:
    """重命名一套基础属性。

    Args:
        school: 流派名称
        old_name: 旧名称
        new_name: 新名称
    """
    def _apply(data: dict) -> dict:
        school_styles = data.get("play_styles", {}).get(school, {})
        if old_name in school_styles:
            school_styles[new_name] = school_styles.pop(old_name)
        return data

    session_node.mutate(_apply)
    logger.debug(f"已重命名基础属性: {school}/{old_name} → {new_name}")
