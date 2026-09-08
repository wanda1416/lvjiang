"""属性来源的装配状态存储。

存两样东西，都在 session.json 的 ``yysls`` 节点里：

    attr_loadout:      { 流派: 装配状态 }              # 当前在编辑的
    attr_derivations:  { 流派: { 基础属性名: 装配状态 } }  # 存过的推导

装配状态就是游戏里实际能装的东西——等级、四个心法槽（每槽一门 +
重数）、套装/武备/神工/吃食各一项。两门武学由流派决定，不在其中。

## 为什么要存第二份

「存为基础属性」只写一份扁平数值的话，事后没人知道它是怎么来的：
用的哪一级、上了哪几门心法各几重、吃没吃药。数值一旦对不上，只能
从头再配一遍。``attr_derivations`` 与 ``play_styles`` 同名同流派，
拿名字就能查回当时的装配，也能在数据更新后按同一份装配重推一次。
"""
from __future__ import annotations

from loguru import logger

from . import session_node

_LOADOUT_KEY = "attr_loadout"
_DERIVATION_KEY = "attr_derivations"


def _load() -> dict:
    return session_node.load()


def get_loadout(school: str) -> dict:
    """当前正在编辑的装配状态；没有则空 dict"""
    data = _load().get(_LOADOUT_KEY) or {}
    stored = data.get(school)
    return dict(stored) if isinstance(stored, dict) else {}


def save_loadout(school: str, loadout: dict) -> None:
    """记住当前装配，下次打开推导对话框直接接着用"""
    def _apply(data: dict) -> dict:
        data.setdefault(_LOADOUT_KEY, {})[school] = loadout
        return data

    session_node.mutate(_apply)


def get_derivation(school: str, name: str) -> dict:
    """某套基础属性当时用的装配状态；没有则空 dict

    空 dict 意味着这套基础属性不是推导来的（多半是抄面板反推的），
    或者存于本功能之前。
    """
    data = _load().get(_DERIVATION_KEY) or {}
    stored = (data.get(school) or {}).get(name)
    return dict(stored) if isinstance(stored, dict) else {}


def save_derivation(school: str, name: str, loadout: dict) -> None:
    """连同基础属性一起记下推导用的装配"""
    def _apply(data: dict) -> dict:
        data.setdefault(_DERIVATION_KEY, {}).setdefault(school, {})[name] = loadout
        return data

    session_node.mutate(_apply)
    logger.debug(f"已记录推导装配: {school}/{name}")


def delete_derivation(school: str, name: str) -> None:
    """基础属性删掉时一并清掉，避免同名的新配置读到旧装配"""
    def _apply(data: dict) -> dict:
        (data.get(_DERIVATION_KEY, {}).get(school) or {}).pop(name, None)
        return data

    session_node.mutate(_apply)
