"""词条与首词条的生效等级范围。

游戏每个赛季都会调整词条库：新等阶移除一部分旧词条、加入新词条。被移除
的词条不能从配置里删掉——历史装备上仍然存着它，OCR 仍会扫到它，低等阶
装备上它依然合法。所以「系统认不认识这个词条」和「这个等级还能不能新产出
它」必须分开表达，后者就是这里的等级范围。

范围两端都可以不配：不配即开区间。``through_level: 110`` 表示 110 及以下
有效、115 起退役；``from_level: 115`` 表示 115 起才出现。
"""

from __future__ import annotations

from typing import NamedTuple

FROM_KEY = "from_level"
THROUGH_KEY = "through_level"
NAME_KEY = "name"


class LevelRange(NamedTuple):
    """生效等级区间；两端 0 表示该侧开区间。"""

    from_level: int = 0
    through_level: int = 0

    def covers(self, level: int | None) -> bool:
        """该等级是否落在区间内。

        等级未知（None/0）时一律放行：范围是用来收窄新产出的，不是用来给
        缺失数据判罪的。
        """
        if not isinstance(level, int) or isinstance(level, bool) or level <= 0:
            return True
        if self.from_level and level < self.from_level:
            return False
        return not (self.through_level and level > self.through_level)

    @property
    def configured(self) -> bool:
        return bool(self.from_level or self.through_level)


OPEN = LevelRange()


def _level(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def parse_range(raw: object) -> LevelRange:
    """从 ``{from_level, through_level}`` 读区间；非法或缺省得到开区间。"""
    if not isinstance(raw, dict):
        return OPEN
    return LevelRange(_level(raw.get(FROM_KEY)), _level(raw.get(THROUGH_KEY)))


def parse_entry(raw: object) -> tuple[str, LevelRange]:
    """读一条「词条名 + 可选等级范围」。

    条目可以是裸字符串（开区间），也可以是 ``{name, from_level,
    through_level}``。名称为空时返回空名，由调用方跳过。
    """
    if isinstance(raw, dict):
        return str(raw.get(NAME_KEY) or "").strip(), parse_range(raw)
    return str(raw or "").strip(), OPEN


def dump_entry(name: str, level_range: LevelRange) -> str | dict:
    """写回配置：开区间仍写成裸字符串，避免给绝大多数词条平白加两层。"""
    if not level_range.configured:
        return name
    entry: dict = {NAME_KEY: name}
    if level_range.from_level:
        entry[FROM_KEY] = level_range.from_level
    if level_range.through_level:
        entry[THROUGH_KEY] = level_range.through_level
    return entry


__all__ = [
    "FROM_KEY",
    "NAME_KEY",
    "OPEN",
    "THROUGH_KEY",
    "LevelRange",
    "dump_entry",
    "parse_entry",
    "parse_range",
]
