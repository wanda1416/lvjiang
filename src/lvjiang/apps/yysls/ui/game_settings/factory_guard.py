"""出厂内容删除守卫 —— 供设置面板判断某条目能否删除

出厂配置是开发者提供的初始内容，用户模式下不允许删除（详见
docs/30-architecture/05-config-layering.md「删除：默认禁止」）。用户自己
新增的条目不受限制，照常可删。

面板拿不到 system 编辑权限时，删除按钮应**置灰**并说明替代方案，
而不是让用户点了之后静默失败——后端已经拦下删除，但用户看不到原因。
"""

from __future__ import annotations

from lvjiang.core.config.resolver import get_resolver

from .....i18n import tr

#: 燕云游戏配置（聚合文件）相对路径
GAME_CONFIG_REL = "yysls/game_config.yaml"

#: 停用替代方案的提示语（有激活机制的场景）
DISABLE_HINT = tr("出厂配置不可删除。如不需要，请改用停用/取消勾选。")
#: 无停用机制时的提示语
READONLY_HINT = tr("出厂配置不可删除，只能修改其取值；新增的条目可以删除。")


def is_user_mode() -> bool:
    """当前是否为用户模式（拿不到 system 编辑权限）"""
    return not get_resolver().is_dev_mode()


def factory_dict_keys(rel_path: str, *path: str) -> set[str]:
    """出厂文档中某个 dict 节点的键集合；节点不存在返回空集"""
    node: object = get_resolver().load_system(rel_path)
    for key in path:
        if not isinstance(node, dict):
            return set()
        node = node.get(key)
    return set(node) if isinstance(node, dict) else set()


def factory_list_values(rel_path: str, *path: str, field: str | None = None) -> set:
    """出厂文档中某个列表节点的取值集合

    field 非空时取每项的该字段（如 weapon_types 取 name），否则取元素本身。
    """
    node: object = get_resolver().load_system(rel_path)
    for key in path:
        if not isinstance(node, dict):
            return set()
        node = node.get(key)
    if not isinstance(node, list):
        return set()
    if field is None:
        return {v for v in node if isinstance(v, (str, int, float))}
    return {item.get(field) for item in node
            if isinstance(item, dict) and item.get(field) is not None}


def deletable(name: object, factory: set, *, hint: str = READONLY_HINT) -> tuple[bool, str]:
    """判断条目能否删除，返回 (可删除, 不可删除时的提示语)

    开发模式恒可删——编排出厂配置是开发者的职责。
    """
    if not is_user_mode() or name not in factory:
        return True, ""
    return False, hint
