"""袅袅之音清单：内容定义（YAML）+ 用户记录（``.linger.json``）+ 可选入账。

这是第一块清单板（``LINGER_BOARD``）。对外仍是：读清单、读写某个用户的记录、
合成进度。入账在 :mod:`.booking`，默认关闭。板身份见
``docs/20-plans/02-profile/04-linger-checklist.md``。
"""
from __future__ import annotations

from .board import LINGER_BOARD
from .catalog import LINGER_FILE, load_catalog
from .models import (
    ITEM_KINDS,
    ITEM_PERIODIC,
    ITEM_ROSTER,
    ITEM_SINGLE,
    STATE_VERSION,
    ItemProgress,
    ItemSlot,
    LingerCatalog,
    LingerItem,
    LingerRegion,
    LingerState,
    build_catalog,
    catalog_from_doc,
    item_key,
    now_iso,
    region_from_doc,
)
from .overrides import (
    add_item,
    known_categories,
    load_effective_catalog,
    move_item,
    remove_category,
    remove_item,
    rename_category,
    revert_item,
    set_batch_target,
    upsert_item,
)
from .store import DOCUMENT_KIND, DOCUMENT_SUFFIX, LingerStore

__all__ = [
    "LINGER_BOARD",
    "DOCUMENT_KIND",
    "DOCUMENT_SUFFIX",
    "LINGER_FILE",
    "ITEM_KINDS",
    "ITEM_PERIODIC",
    "ITEM_ROSTER",
    "ITEM_SINGLE",
    "STATE_VERSION",
    "ItemProgress",
    "ItemSlot",
    "LingerCatalog",
    "LingerItem",
    "LingerRegion",
    "LingerState",
    "LingerStore",
    "add_item",
    "known_categories",
    "load_effective_catalog",
    "move_item",
    "remove_category",
    "rename_category",
    "remove_item",
    "revert_item",
    "set_batch_target",
    "upsert_item",
    "build_catalog",
    "catalog_from_doc",
    "item_key",
    "load_catalog",
    "now_iso",
    "region_from_doc",
]
