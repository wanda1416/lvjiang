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

另有一份内置配置「满级属性」，取自毕业率方案 Excel 的 100% 毕业基准
（方案 JSON 的 ``baseline_attrs``）。它不落盘：那份数据本来就在方案里，
再存一份就会在换赛季重新导入 Excel 之后悄悄过期。内置项对每个流派都
存在，随方案更新自动跟上，不能改名也不能删。
"""
from __future__ import annotations

from loguru import logger

from . import session_node


def _load() -> dict:
    """读取插件会话节点（首次运行时回退旧的独立文件）"""
    return session_node.load()


#: 内置的满级属性配置名。取自毕业率方案的 100% 毕业基准。
FULL_GRADUATION = "满级属性"


def _full_graduation_attrs(school: str) -> dict | None:
    """该流派的 100% 毕业基准属性；没有方案或方案格式旧了就返回 None"""
    # 延迟导入：core.graduation 反过来要用 config，模块级导入会成环
    from ..config import get_game_config
    from ..core.graduation import get_graduation_scheme_combat_attrs

    schemes = (get_game_config().get_schools().get(school) or {}).get("schemes")
    if not schemes:
        return None
    try:
        attrs = get_graduation_scheme_combat_attrs(school, schemes[0])
    except Exception as e:
        logger.debug(f"{school} 没有可用的满级属性: {e}")
        return None
    return attrs.to_dict()


def get_play_styles(school: str) -> dict[str, dict]:
    """指定流派**存下来的**基础属性配置。

    只读存储，不含内置项——要连内置的一起用
    :func:`get_base_attr_profiles`。分成两个函数是因为写回路径必须看到
    纯存储：把内置项混进来，一次「读出来改一个字段再存回去」就会把那
    份内置的快照落盘，从此不再跟着方案走。

    Args:
        school: 流派名称

    Returns:
        基础属性字典：名称 → {field_name: value, ...}
    """
    data = _load()
    all_styles = data.get("play_styles", {})
    return dict(all_styles.get(school) or {})


def get_base_attr_profiles(school: str) -> dict[str, dict]:
    """可供选用的基础属性配置：内置的「满级属性」+ 存下来的。

    内置项排在最前，随毕业率方案走。存量里若有同名的手存配置，内置的
    赢——内置的才是「Excel 说的满级」，手存的那份多半是早先手抄的，
    已经跟不上赛季了。
    """
    profiles: dict[str, dict] = {}
    builtin = _full_graduation_attrs(school)
    if builtin is not None:
        profiles[FULL_GRADUATION] = builtin
    for name, attrs in get_play_styles(school).items():
        profiles.setdefault(name, attrs)
    return profiles


def _reject_builtin(name: str) -> None:
    if name == FULL_GRADUATION:
        raise ValueError(
            f"「{FULL_GRADUATION}」是内置配置，取自毕业率方案，不能改名或删除")


def save_play_style(school: str, name: str, attrs: dict) -> None:
    """保存一套基础属性。

    Args:
        school: 流派名称
        name: 基础属性名称
        attrs: 属性字典 {field_name: value}
    """
    _reject_builtin(name)

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
    _reject_builtin(name)

    def _apply(data: dict) -> dict:
        data.get("play_styles", {}).get(school, {}).pop(name, None)
        # 推导上下文与基础属性同名同流派，留着会让同名的新配置读到旧装配
        (data.get("attr_derivations", {}).get(school) or {}).pop(name, None)
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
    _reject_builtin(old_name)
    _reject_builtin(new_name)

    def _apply(data: dict) -> dict:
        school_styles = data.get("play_styles", {}).get(school, {})
        if old_name in school_styles:
            school_styles[new_name] = school_styles.pop(old_name)
        # 推导上下文按名字索引，不跟着搬就查不回这套是怎么推出来的
        derivations = data.get("attr_derivations", {}).get(school, {})
        if old_name in derivations:
            derivations[new_name] = derivations.pop(old_name)
        return data

    session_node.mutate(_apply)
    logger.debug(f"已重命名基础属性: {school}/{old_name} → {new_name}")
