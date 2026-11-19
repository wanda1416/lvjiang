"""袅袅之音清单：内容定义（YAML）+ 用户记录（``.linger.json``）。

对外只暴露三件事：读清单、读写某个用户的记录、以及两者合成的进度统计。
入账（写 profile）与周期型来源不在本模块——它们属于下一版，见
``docs/20-plans/02-profile/04-linger-checklist.md``。
"""
from __future__ import annotations

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
