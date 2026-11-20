"""一块「资源清单」板的身份，不包含条目内容。

清单 YAML、用户记录和入账都按这块板的字段落点。袅袅之音是第一块已有数据的板：
``id`` / 文档 kind 一旦写进云端就不能改名。再加一块板是再声明一个
:class:`ChecklistBoard`，而不是复制仓储。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChecklistBoard:
    """一张清单的稳定身份。

    ``default_target`` 只作固定/逐位条目的缺省入账目标。周期条目在自己身上绑
    ``quota:`` key，不写进这里。``sync_models`` 决定入账时哪些模型触发 profile
    的同步目标：袅袅的库存挂着八音窍，所以只放 ``quota``。
    """

    id: str
    title: str
    catalog_file: str
    document_kind: str
    document_suffix: str
    default_target: str
    sync_models: frozenset[str] = field(default_factory=lambda: frozenset({"quota"}))
    group_label: str = "分组"

    @property
    def switch_key(self) -> str:
        """「同步记账」在 ``ui_state`` 里的键，按板分开，避免两块板共用一个开关。"""
        return f"{self.id}_booking"

    def triggers_sync(self, model: str) -> bool:
        return model in self.sync_models
