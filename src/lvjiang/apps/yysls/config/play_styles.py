"""基础属性配置存储（兼容旧的 play_styles 命名）。

基础属性数据属于会话级数据（由面板属性反推），不应提交到 git。
存储在 session.json 的 ``yysls`` 节点（旧的独立 yysls.json 仍可读，
见 session_node）：

    play_styles: {
        流派名: {
            基础属性名称: { field_name: value, ... }
        }
    }
    play_style_versions: {
        流派名: { 基础属性名称: 版本号 }
    }

此文件与用户无关，所有用户共享同一套基础属性配置。

## 版本号的用途

基础属性是「面板 - 装备」反推出来的，装备侧的换算一旦修正，此前存下的
基础属性就不再等于「面板 - 正确装备」。由于只存了反推结果、没存当时的
面板值与装备快照，**无法自动补偿**，只能请用户重新填一次面板。

版本号标记某套基础属性是用哪一代换算反推的：低于
``BASE_ATTR_VERSION`` 即为过期，UI 据此提示重填。缺失视作第 1 代。
"""
from __future__ import annotations

from loguru import logger

from . import session_node

#: 基础属性的反推代次。
#:
#: 2 —— 修正五维转换系数（敏 1.0→0.9、劲 0.246/1.315→0.225/1.36 等，
#: 见 core/combat/combat_attrs.py 的五维转换系数段）。第 1 代反推时装备
#: 侧的五维贡献被高估，装备带劲/势/敏词条的角色，存下的基础属性偏低。
BASE_ATTR_VERSION = 2


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
        versions = data.setdefault("play_style_versions", {})
        versions.setdefault(school, {})[name] = BASE_ATTR_VERSION
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
        data.get("play_style_versions", {}).get(school, {}).pop(name, None)
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
        # 版本随名字一起搬，否则改个名字就被误判成过期
        school_versions = data.get("play_style_versions", {}).get(school, {})
        if old_name in school_versions:
            school_versions[new_name] = school_versions.pop(old_name)
        return data

    session_node.mutate(_apply)
    logger.debug(f"已重命名基础属性: {school}/{old_name} → {new_name}")


def get_play_style_version(school: str, name: str) -> int:
    """某套基础属性是用哪一代反推的。缺失视作第 1 代。"""
    versions = _load().get("play_style_versions", {})
    value = (versions.get(school) or {}).get(name)
    return value if isinstance(value, int) else 1


def is_play_style_stale(school: str, name: str) -> bool:
    """该套基础属性是否需要用户重新填写面板反推"""
    return get_play_style_version(school, name) < BASE_ATTR_VERSION


def stale_play_styles() -> list[tuple[str, str]]:
    """全部需要重填的基础属性，按 (流派, 名称) 返回"""
    data = _load()
    versions = data.get("play_style_versions", {})
    stale: list[tuple[str, str]] = []
    for school, styles in (data.get("play_styles") or {}).items():
        school_versions = versions.get(school) or {}
        for name in styles:
            value = school_versions.get(name)
            if not isinstance(value, int) or value < BASE_ATTR_VERSION:
                stale.append((school, name))
    return stale
