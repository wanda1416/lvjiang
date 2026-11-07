"""变更记录查询条件。

历史对话框的顶部筛选、表头聚合筛选行、撤销后的刷新都通过这一份条件对象，
仓储层只按它生成 SQL——没有第二套"简化条件"旁路，避免同一个界面里两种
筛选语义。

条件是两种形态的组合，没有第三种：

- **精确集合**：``usernames`` / ``keys`` / ``change_types`` / ``sources`` /
  ``sync_froms``。空元组表示"不限"；空字符串是真实取值，用来表达
  "未记录来源"（``sources=("",)`` 只匹配 source 为空的行）。两者绝不能混用。
  ``model_types`` / ``key_names`` 是"只有一半"的单侧条件（按模型类型或按键名
  过滤，不限定配对的另一半），只有便捷查询入口用得到，界面不会产生它们。
- **模糊词**：``keyword``（跨列）、``old_text`` / ``new_text``（单列）。
  模糊词按"文本包含"匹配，不做 `%`/`_` 通配解释，转义在仓储层统一处理。

``key_matches`` 是 UI 解析出来的键命中集合：用户在关键词里输入的是标签
（``profile.yaml`` 里的展示名），SQL 看不到配置文件，所以由 UI 先把标签解析成
``(type, key)`` 再交给查询层，与跨列模糊词是"或"的关系。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TypeVar

_T = TypeVar("_T")

#: 可聚合成候选的维度；与仓储层的列表达式一一对应。
FACET_DIMENSIONS = ("username", "key", "change_type", "source", "sync_from")

#: 变动量方向：全部 / 增加 / 减少 / 为零。
DELTA_SCOPES = ("all", "pos", "neg", "zero")


def _clean_text(value: str) -> str:
    return (value or "").strip()


def _clean_unique(values: tuple[_T, ...]) -> tuple[_T, ...]:
    """去重后保持首次出现顺序；空集合就是"不限"。"""
    seen: dict[_T, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return tuple(seen)


@dataclass(frozen=True)
class HistoryQuery:
    """变更记录的筛选条件（不可变、可直接比较）。"""

    usernames: tuple[str, ...] = ()
    keys: tuple[tuple[str, str], ...] = ()
    model_types: tuple[str, ...] = ()
    key_names: tuple[str, ...] = ()
    change_types: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    sync_froms: tuple[str, ...] = ()
    keyword: str = ""
    old_text: str = ""
    new_text: str = ""
    key_matches: tuple[tuple[str, str], ...] = ()
    ts_from: str | None = None
    ts_until: str | None = None
    delta_scope: str = "all"

    def __post_init__(self) -> None:
        # 归一化输入：去重、去空白、校验枚举。相同条件必须生成相同 SQL，
        # 这样"条件没变就不重新查询"和测试里的断言都成立。
        object.__setattr__(self, "usernames", _clean_unique(tuple(self.usernames)))
        object.__setattr__(self, "keys", _clean_unique(tuple(self.keys)))
        object.__setattr__(self, "model_types", _clean_unique(tuple(self.model_types)))
        object.__setattr__(self, "key_names", _clean_unique(tuple(self.key_names)))
        object.__setattr__(self, "change_types", _clean_unique(tuple(self.change_types)))
        object.__setattr__(self, "sources", _clean_unique(tuple(self.sources)))
        object.__setattr__(self, "sync_froms", _clean_unique(tuple(self.sync_froms)))
        object.__setattr__(self, "key_matches", _clean_unique(tuple(self.key_matches)))
        object.__setattr__(self, "keyword", _clean_text(self.keyword))
        object.__setattr__(self, "old_text", _clean_text(self.old_text))
        object.__setattr__(self, "new_text", _clean_text(self.new_text))
        if self.delta_scope not in DELTA_SCOPES:
            raise ValueError(f"不支持的变动量方向: {self.delta_scope}")
        for value in (self.ts_from, self.ts_until):
            if value is not None and not value:
                raise ValueError("时间边界不能是空串，留空请传 None")

    def relaxed(self, dimension: str) -> HistoryQuery:
        """去掉某个维度的精确条件，其余保持不动。

        聚合候选要求"除自己之外的其它条件都成立"（Excel 语义）：否则用户
        勾了一个来源之后，候选列表就只剩它自己，想再加一个只能靠记忆输入。
        """
        if dimension not in FACET_DIMENSIONS:
            raise ValueError(f"不支持的聚合维度: {dimension}")
        if dimension == "username":
            return replace(self, usernames=())
        if dimension == "key":
            # 「键」这一维度同时包含模型类型与键名，放松时一起去掉。
            return replace(
                self, keys=(), key_matches=(), model_types=(), key_names=())
        if dimension == "change_type":
            return replace(self, change_types=())
        if dimension == "source":
            return replace(self, sources=())
        return replace(self, sync_froms=())

    @property
    def is_empty(self) -> bool:
        """没有任何限制条件时的全账本查询。"""
        return not (
            self.usernames or self.keys or self.model_types or self.key_names
            or self.change_types or self.sources or self.sync_froms
            or self.keyword or self.old_text or self.new_text or self.key_matches
            or self.ts_from or self.ts_until or self.delta_scope != "all"
        )
