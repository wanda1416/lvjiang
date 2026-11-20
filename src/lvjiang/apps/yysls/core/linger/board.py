"""袅袅之音这块清单板。云端 kind 是 ``linger``，已有数据后不可改名。"""
from __future__ import annotations

from ..checklist.board import ChecklistBoard

LINGER_BOARD = ChecklistBoard(
    id="linger",
    title="袅袅之音",
    catalog_file="yysls/lingering.yaml",
    document_kind="linger",
    document_suffix=".linger.json",
    default_target="stock:niaoniao",
    # 库存入账不能触发同步：niaoniao 挂着八音窍（byq_countdown / byq_jindu）。
    sync_models=frozenset({"quota"}),
    group_label="地区",
)
