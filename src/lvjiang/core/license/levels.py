"""可签发的授权等级

按**等级**而不是具体功能划分：功能会增删改名，等级不会。签发时写进激活码正文的是
``lv1`` 这样的等级名，门禁处问的也是等级——将来某个功能划进 Lv1，已签发的码自动
覆盖它，不用给老用户重新发码。

等级表随主程序走（这里），签发工具读的是同一份：让工具自己维护一份清单，迟早和
主程序对不上，签出开不了任何东西的码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_LEVELS_FILE = Path(__file__).with_name("levels.json")


@dataclass(frozen=True)
class Level:
    """一个授权等级"""

    name: str
    label: str
    desc: str = ""

    @property
    def display(self) -> str:
        return f"{self.label}　{self.desc}".strip()


@lru_cache(maxsize=1)
def load_levels() -> tuple[Level, ...]:
    """读取等级表；文件缺失或损坏时返回空元组而不是抛错。

    等级表读不出来的后果是「签发工具列不出可选项」，那是显而易见的；
    让主程序因为一个它平时根本不用的文件启动失败，才是不成比例的。
    """
    try:
        data = json.loads(_LEVELS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        from loguru import logger
        logger.warning(f"授权等级表不可用: {_LEVELS_FILE}")
        return ()
    levels = []
    for item in data.get("levels", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        levels.append(Level(
            name=name,
            label=str(item.get("label", name)),
            desc=str(item.get("desc", "")),
        ))
    return tuple(levels)


def level_label(name: str) -> str:
    """等级名 → 展示名；未登记的等级原样返回（老码可能带着已下线的等级）"""
    for level in load_levels():
        if level.name == name:
            return level.label
    return name
