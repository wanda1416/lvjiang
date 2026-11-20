"""用户对清单的增删改：写 ``config/local/yysls/lingering.yaml`` 的同名覆盖层。

local 与 system 是**同一层结构、同一文件名**，只是优先级更高——不是另一套格式：

- 改一条：写 ``regions.<地区 id>.items`` 里那条（列表按 ``id`` 对齐原位替换，见
  合并层的注册表语义；只写被改的条目，不复制整张表）；
- 新增一条：在该地区 ``items`` 里加一条新 id 的条目；
- 隐藏一条：写 ``hidden: true``。合并层不允许删系统条目（和产品里其它系统内容
  一致），隐藏是用户层的「删除」语义，记录文件里的历史原样保留。

整个文档经 ``resolver.save_merged`` 落盘：开发模式写 system，用户模式自动求
diff 只存改动；离线包与远程下发都按既有规则走，不需要任何特判。
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from loguru import logger

from .....core.config.merge import (
    REGISTRY_LIST_PATHS,
    register_registry_list_identity,
    register_registry_list_paths,
)
from .....core.config.resolver import get_resolver
from .models import (
    DEFAULT_TARGET,
    ITEM_PERIODIC,
    ITEM_SINGLE,
    RESERVED_DOC_KEYS,
    LingerCatalog,
    catalog_from_doc,
)

#: 清单相对路径（system/local 同名）
LINGER_FILE = "yysls/lingering.yaml"

# 条目列表的合并语义（按 id 原位替换）。import 即注册：页面与测试只要碰到本
# 模块就生效，不依赖 AppHooks 是否已加载插件；幂等，重复注册无副作用。
# 顶层每个地区键的 items 都按注册表语义合并（`*.items` 通配全部地区）。
if LINGER_FILE not in REGISTRY_LIST_PATHS:
    register_registry_list_paths(LINGER_FILE, ("*.items",))
    register_registry_list_identity(LINGER_FILE, {"*.items": "id"})
def _load_full() -> dict:
    """读取当前合并视图（system + local），作为编辑的完整文档。"""
    return get_resolver().load_merged(LINGER_FILE)


def _save_full(doc: dict) -> None:
    get_resolver().save_merged(LINGER_FILE, doc)


def read_default_target() -> str:
    """顶部「同步模型」的当前值：local 覆盖层里的 ``default_target``。"""
    value = get_resolver().load_local(LINGER_FILE).get("default_target")
    target = str(value or "").strip()
    if target:
        return target
    doc = get_resolver().load_system(LINGER_FILE)
    return str(doc.get("default_target") or DEFAULT_TARGET).strip()


def set_default_target(target: str) -> None:
    """改固定/逐位共用的同步模型；只影响这两类，周期型各用自己的 key。"""
    doc = _load_full()
    clean = str(target or "").strip()
    if clean and clean != DEFAULT_TARGET:
        doc["default_target"] = clean
    else:
        doc.pop("default_target", None)
    _save_full(doc)


def load_effective_catalog(resolver=None) -> LingerCatalog:
    """页面唯一入口：合并视图 → 生效清单。"""
    if resolver is None:
        return catalog_from_doc(_load_full(), source_name=LINGER_FILE)
    doc = resolver.load_merged(LINGER_FILE)
    return catalog_from_doc(doc, source_name=LINGER_FILE)


def known_categories(catalog: LingerCatalog | None = None) -> list[str]:
    """类型下拉的选项：清单里用过的类型 + 用户自建的，按字母序。

    类型只是条目的展示前缀，不单独建管理界面——下拉本身可编辑，输入新名字
    保存条目时它就自然存在了；这里收集它们只为下次输入时能直接选。
    """
    if catalog is None:
        catalog = load_effective_catalog()
    found = {
        item.category
        for region in catalog.regions for item in region.items
        if item.category
    }
    return sorted(found)


def rename_category(old: str, new: str) -> int:
    """把一个类型改名的条数（对清单里所有该类型的条目生效）。"""
    old_clean = str(old or "").strip()
    new_clean = str(new or "").strip()
    if not old_clean or not new_clean or old_clean == new_clean:
        return 0
    doc = _load_full()
    changed = 0
    for region_id, region in doc.items():
        if region_id in RESERVED_DOC_KEYS or not isinstance(region, dict):
            continue
        for entry in region.get("items") or []:
            if isinstance(entry, dict) and str(entry.get("category") or "") == old_clean:
                entry["category"] = new_clean
                changed += 1
    if changed:
        _save_full(doc)
    return changed


def remove_category(name: str) -> int:
    """清掉一个类型（把该类型的条目改为无类型）。"""
    clean = str(name or "").strip()
    if not clean:
        return 0
    doc = _load_full()
    changed = 0
    for region_id, region in doc.items():
        if region_id in RESERVED_DOC_KEYS or not isinstance(region, dict):
            continue
        for entry in region.get("items") or []:
            if isinstance(entry, dict) and str(entry.get("category") or "") == clean:
                entry.pop("category", None)
                changed += 1
    if changed:
        _save_full(doc)
    return changed


# ─── 条目定位 ────────────────────────────────────────────────


def _find_region(doc: dict, region_id: str) -> dict | None:
    """顶层键即地区（元信息键除外）；见 :func:`catalog_from_doc` 的结构说明。"""
    if region_id in RESERVED_DOC_KEYS:
        return None
    region = doc.get(region_id)
    return region if isinstance(region, dict) else None


def _find_item(region: dict, item_id: str) -> tuple[list, int] | None:
    items = region.get("items")
    if not isinstance(items, list):
        return None
    for index, entry in enumerate(items):
        if isinstance(entry, dict) and str(entry.get("id") or "") == item_id:
            return items, index
    return None


def _region_id_of(key: str) -> str:
    """条目键（=条目 id）→ 所在地区 id（经 catalog 反查）。"""
    found = load_effective_catalog().item(key)
    if found is None:
        raise KeyError(f"条目不存在：{key!r}")
    return found[0].id


# ─── 页面用的增删改 ──────────────────────────────────────────


def new_item_id() -> str:
    """新增条目的 id：与系统条目同格式的纯随机 uuid8。

    id 从生成起永不变更——与地区、分类、条目名、类型完全解耦，改任何
    字段都不断用户记录（见 lingering.yaml 文件头约定）。
    """
    return uuid4().hex[:8]


def upsert_item(key: str, fields: dict) -> None:
    """修改一条（含用户新增过的条目）；不存在的条目视为配置错误，忽略并记日志。"""
    try:
        region_id = _region_id_of(key)
    except KeyError:
        logger.warning(f"袅袅之音编辑落空：条目 {key} 不存在")
        return
    doc = _load_full()
    region = _find_region(doc, region_id)
    if region is None:
        logger.warning(f"袅袅之音编辑落空：地区 {region_id} 不存在")
        return
    found = _find_item(region, key)
    if found is None:
        logger.warning(f"袅袅之音编辑落空：条目 {key} 不存在")
        return
    items, index = found
    clean = _clean_fields(fields, require_id=False)
    items[index] = {**items[index], **clean}
    _save_full(doc)


def add_item(region_id: str, fields: dict) -> str:
    """新增一条到指定地区，返回条目键（=条目 id，全局唯一）。"""
    doc = _load_full()
    region = _find_region(doc, region_id)
    if region is None:
        raise ValueError(f"地区 {region_id} 不存在")
    items = region.get("items")
    if not isinstance(items, list):
        items = []
        region["items"] = items
    item_id = new_item_id()
    entry = {"id": item_id, **_clean_fields(fields, require_id=False)}
    items.append(entry)
    _save_full(doc)
    return item_id


def remove_item(key: str) -> None:
    """隐藏一条：用户层写 ``hidden: true``（系统条目不允许真删）。"""
    try:
        region_id = _region_id_of(key)
    except KeyError:
        return
    doc = _load_full()
    region = _find_region(doc, region_id)
    if region is None:
        return
    found = _find_item(region, key)
    if found is None:
        return
    items, index = found
    items[index] = {**items[index], "hidden": True}
    _save_full(doc)


def revert_item(key: str) -> None:
    """撤销这条的所有手工改动：hidden 与字段覆盖一并清掉，回到 system 原样。

    system 里的原条目以 system 层为准恢复（不是拿合并视图回填，避免把用户
    改过的值又写成一份新的覆盖）。
    """
    try:
        region_id = _region_id_of(key)
    except KeyError:
        return
    resolver = get_resolver()
    merged = _load_full()
    region = _find_region(merged, region_id)
    if region is None:
        return
    found = _find_item(region, key)
    if found is None:
        return
    items, index = found
    system_doc = resolver.load_system(LINGER_FILE)
    system_region = _find_region(system_doc, region_id)
    system_found = (_find_item(system_region, key)
                    if system_region is not None else None)
    if system_found is None:
        # 用户新增的条目：直接从列表里去掉
        items.pop(index)
    else:
        system_items, system_index = system_found
        items[index] = dict(system_items[system_index])
    _save_full(merged)


def move_item(key: str, before_key: str | None) -> bool:
    """把 ``key`` 挪到 ``before_key`` 前面（``None`` = 挪到本地区末尾）。

    顺序是用户偏好，写在 local 的 ``__order__``（注册表语义）：只存拖过的那批
    id，system 后来新增的条目没被提到会自动排到后面——不会因为用户排过一次序
    就把新内容挡在外面。挪到「正好等于当前系统顺序」的位置时 diff 归零，
    local 里的顺序增量随之消失，行为与其它覆盖一致。
    """
    try:
        region_id = _region_id_of(key)
    except KeyError:
        return False
    doc = _load_full()
    region = _find_region(doc, region_id)
    if region is None:
        return False
    found = _find_item(region, key)
    if found is None:
        return False
    items = found[0]
    moving = found[1]

    if before_key is None:
        target_index = len(items) - 1
    else:
        anchor = _find_item(region, before_key)
        if anchor is None:
            return False
        # 相邻上移会让 anchor 落到移动项之后；以目标位置的最终态为准换算
        target_index = anchor[1] if anchor[1] < moving else anchor[1] - 1

    entry = items.pop(moving)
    items.insert(min(target_index, len(items)), entry)
    _save_full(doc)
    return True


def set_batch_target(keys: list[str], target: str) -> int:
    """把一批条目统一绑到同一个目标，返回实际改动的条数。

    ``target`` 为空表示解绑（退回固定类，走顶部「同步模型」）。
    """
    clean = str(target or "").strip()
    catalog = load_effective_catalog()
    doc = _load_full()
    changed = 0
    for key in keys:
        found = catalog.item(key)
        if found is None:
            continue
        region = _find_region(doc, found[0].id)
        if region is None:
            continue
        entry_found = _find_item(region, key)
        if entry_found is None:
            continue
        items, index = entry_found
        entry = dict(items[index])
        if clean:
            entry["target"] = clean
            entry["kind"] = ITEM_PERIODIC
        else:
            entry.pop("target", None)
            entry.pop("kind", None)
        items[index] = entry
        changed += 1
    if changed:
        _save_full(doc)
    return changed


def _clean_fields(fields: dict, *, require_id: bool = True) -> dict:
    """只收清单认识的字段；target/kind 的自洽规则与解析层一致。"""
    result: dict[str, Any] = {}
    for name in ("category", "label", "count", "source", "detail"):
        if name in fields:
            result[name] = fields[name]
    kind = str(fields.get("kind") or ITEM_SINGLE)
    target = str(fields.get("target") or "").strip()
    if target.startswith("quota:"):
        kind = ITEM_PERIODIC
    elif kind == ITEM_PERIODIC:
        kind = ITEM_SINGLE
        target = ""
    if target and target != DEFAULT_TARGET:
        result["target"] = target
        result["kind"] = kind
    elif kind != ITEM_SINGLE:
        result["kind"] = kind
    if require_id and "id" in fields:
        result["id"] = fields["id"]
    return result
