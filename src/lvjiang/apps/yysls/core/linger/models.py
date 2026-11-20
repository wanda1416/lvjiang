"""袅袅之音清单的领域模型（清单定义 + 用户记录）。

两类数据分得很清：

- **清单**（``config/system/yysls/lingering.yaml``，随版本下发）只描述游戏事实：
  地区、条目、可获得枚数、以及将来入账时写进账本的来源文案。它**不出现 profile
  key**——key 是用户可改名、可改周期与上限的变量，绑定关系属于定义层，由页面按
  key 自身的标记收集，清单跟着用户改配置走不了。
- **记录**（``users/<用户名>.linger.json``，用户事实）只记每个条目已得多少枚。

条目在记录里的键是 ``<地区 id>/<条目 id>``。改名只改 ``label``、不动 ``id``，
所以重命名条目不丢历史；清单删掉条目时记录原样保留，页面把它标成「清单已移除」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .board import LINGER_BOARD

#: 条目类型：决定「怎么记」以及「记到哪」
#: - single：单次/累加来源，勾选或填数字
#: - roster：每一枚对应一个具体对象（以武会友），展开逐位记名字
#: - periodic：绑配额 key，本周期已得就是配额当前值，按周期归零
ITEM_SINGLE = "single"
ITEM_ROSTER = "roster"
ITEM_PERIODIC = "periodic"
ITEM_KINDS = (ITEM_SINGLE, ITEM_ROSTER, ITEM_PERIODIC)

#: 条目的默认入账目标（库存里的袅袅之音）。清单里可用 ``target:`` 覆盖；
#: 写成 ``quota:<key>`` 就是周期型来源，写成 ``stock:<key>`` 是固定来源。
DEFAULT_TARGET = LINGER_BOARD.default_target

#: 记录文件的结构版本；读取时宽容处理更旧/未知字段，不做破坏性迁移。
#: v2 给条目加了可选的 ``slots``（逐个录入名字的子项），
#: v3 加了 ``booked``（已写进 Profile 账本的枚数）。
STATE_VERSION = 3


def now_iso() -> str:
    """记录里统一存 UTC ISO（秒级）：跨机器排序不需要猜时区。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def item_key(region_id: str, item_id: str) -> str:
    """记录文件里的条目键：条目 id 全局唯一，地区前缀冗余。

    保留双参签名（region_id 仅表明条目归属，不参与键值）：调用方不用逐处改。
    """
    return item_id


# ─── 清单定义 ────────────────────────────────────────────────


@dataclass(frozen=True)
class LingerItem:
    """清单里的一个条目。``count`` 是这条途径一共能拿多少枚，1 表示单次获取。

    ``named`` 为真表示这 ``count`` 枚各自对应一个具体对象（例如以武会友的每一位
    对手），用户要能逐个录入名字；此时已得枚数由填了几位推出，不再手填数字。
    """

    id: str
    #: 条目内容本身，不带分类前缀（分类单独存 ``category``）。
    label: str
    count: int
    source: str
    #: 条目分类（探游 / 成就 / 镇守 / 主线这类），可空；展示名 = 类型：条目。
    category: str = ""
    kind: str = ITEM_SINGLE
    target: str = DEFAULT_TARGET
    #: 大段说明（任务怎么完成、在哪接、需要注意什么）。列表里只显示摘要，
    #: 双击行看全文。
    detail: str = ""

    @property
    def display_name(self) -> str:
        """展示名：``类型：条目``；没有类型就只显示条目。细则（子项名）由调用方再拼。"""
        return f"{self.category}：{self.label}" if self.category else self.label

    @property
    def named(self) -> bool:
        """逐位录入（每一枚对应一个具体对象）。"""
        return self.kind == ITEM_ROSTER

    @property
    def periodic(self) -> bool:
        """周期型：已得等于配额 key 的本周期当前值。"""
        return self.kind == ITEM_PERIODIC


def _item_kind(raw: dict, where: str) -> str:
    """解析条目类型；``named: true`` 是早期写法，按逐位处理。"""
    value = raw.get("kind")
    if value is None:
        return ITEM_ROSTER if raw.get("named") else ITEM_SINGLE
    kind = str(value).strip()
    if kind not in ITEM_KINDS:
        raise ValueError(f"{where} 的 kind 必须是 {'/'.join(ITEM_KINDS)}")
    return kind


@dataclass(frozen=True)
class LingerRegion:
    """一个地区（或「通用」这类分组）。``order`` 决定展示顺序。"""

    id: str
    label: str
    order: int
    items: tuple[LingerItem, ...]

    @property
    def total(self) -> int:
        return sum(item.count for item in self.items)

    @property
    def fixed_total(self) -> int:
        """常驻来源的枚数合计（周期型不参与，它们按周期自己算）。"""
        return sum(item.count for item in self.items if not item.periodic)


@dataclass(frozen=True)
class LingerCatalog:
    """全部地区的有序集合。跨文件的 id 唯一性由 :func:`build_catalog` 保证。"""

    regions: tuple[LingerRegion, ...]

    @property
    def total(self) -> int:
        return sum(region.total for region in self.regions)

    @property
    def fixed_total(self) -> int:
        return sum(region.fixed_total for region in self.regions)

    @property
    def fixed_item_count(self) -> int:
        return sum(1 for region in self.regions for item in region.items
                   if not item.periodic)

    @property
    def item_count(self) -> int:
        return sum(len(region.items) for region in self.regions)

    def region(self, region_id: str) -> LingerRegion | None:
        for region in self.regions:
            if region.id == region_id:
                return region
        return None

    def item(self, key: str) -> tuple[LingerRegion, LingerItem] | None:
        """按条目 id（记录键）全局查找；返回它所在的地区与条目本身。"""
        for region in self.regions:
            for item in region.items:
                if item.id == key:
                    return region, item
        return None


def _require_text(value: Any, where: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{where} 缺少必填文本")
    return text


def _fallback_source(raw: dict) -> str:
    """来源缺省：有分类用 ``类型：条目``，否则就是条目本身（展示与账本一致）。"""
    category = str(raw.get("category") or "").strip()
    label = str(raw.get("label") or "").strip()
    return f"{category}：{label}" if category else label


def region_from_doc(doc: Any, *, source_name: str,
                    region_id: str | None = None) -> LingerRegion:
    """把一份地区清单 YAML 解析成 :class:`LingerRegion`。

    任何字段问题都抛 ``ValueError``（带文件名与条目位置），由加载层隔离：
    单个文件坏掉只丢这一个地区，不影响其它地区与页面打开。
    """
    if not isinstance(doc, dict):
        raise ValueError(f"{source_name} 顶层必须是 mapping")
    if region_id is None:
        region_id = _require_text(doc.get("id"), f"{source_name} 的 id")
    else:
        _require_text(region_id, f"{source_name} 的键")
    label = _require_text(doc.get("label"), f"{source_name} 的 label")
    raw_items = doc.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError(f"{source_name} 的 items 必须是非空列表")
    order = doc.get("order", 0)
    try:
        order_value = int(order)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source_name} 的 order 必须是整数") from exc

    items: list[LingerItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        where = f"{source_name} 第 {index + 1} 个条目"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} 必须是 mapping")
        item_id = _require_text(raw.get("id"), f"{where} 的 id")
        if item_id in seen:
            raise ValueError(f"{where} 的 id {item_id!r} 在本文件内重复")
        seen.add(item_id)
        try:
            count = int(raw.get("count", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{where} 的 count 必须是整数") from exc
        if count <= 0:
            raise ValueError(f"{where} 的 count 必须大于 0")
        item_label = _require_text(raw.get("label"), f"{where} 的 label")
        target = str(raw.get("target") or DEFAULT_TARGET).strip()
        kind = _item_kind(raw, where)
        # 类型与目标必须自洽：绑了配额 key 就是周期型，声明周期却没绑配额则降级为固定
        if target.startswith("quota:"):
            kind = ITEM_PERIODIC
        elif kind == ITEM_PERIODIC:
            kind = ITEM_SINGLE
        hidden = bool(raw.get("hidden", False))
        if hidden:
            continue
        items.append(LingerItem(
            id=item_id,
            category=str(raw.get("category") or "").strip(),
            label=item_label,
            count=count,
            source=str(raw.get("source") or _fallback_source(raw)).strip(),
            kind=kind,
            target=target,
            detail=str(raw.get("detail") or "").strip(),
        ))
    return LingerRegion(id=region_id, label=label, order=order_value,
                        items=tuple(items))


#: 顶层保留给元信息的键（不属于任何地区）
RESERVED_DOC_KEYS = frozenset({"content_version", "default_target"})


def catalog_from_doc(doc: Any, source_name: str) -> LingerCatalog:
    """解析整份清单文件；坏地区抛 ``ValueError`` 交给加载层。

    顶层每个键（除元信息外）就是一个地区：地区只是条目的属性分组，
    不再套 ``regions:`` 容器——local 覆盖层写单地区时直接顶层同名即可。
    """
    if not isinstance(doc, dict):
        raise ValueError(f"{source_name} 顶层必须是 mapping")
    region_ids = [k for k in doc if k not in RESERVED_DOC_KEYS]
    if not region_ids:
        raise ValueError(f"{source_name} 没有任何地区")
    regions = [
        region_from_doc(doc[region_id],
                        source_name=f"{source_name} 的地区 {region_id}",
                        region_id=str(region_id))
        for region_id in region_ids
    ]
    return build_catalog(regions)


def build_catalog(regions: list[LingerRegion]) -> LingerCatalog:
    """按 order 排序并校验地区 id 唯一。重复项由加载层决定丢弃顺序。"""
    seen: set[str] = set()
    unique: list[LingerRegion] = []
    for region in regions:
        if region.id in seen:
            raise ValueError(f"地区 id {region.id!r} 重复")
        seen.add(region.id)
        unique.append(region)
    unique.sort(key=lambda region: (region.order, region.id))
    return LingerCatalog(tuple(unique))


# ─── 用户记录 ────────────────────────────────────────────────


@dataclass
class ItemSlot:
    """``named`` 条目的一个子项：是否已获得、录入的名字与记录时间。

    名字可以为空（先记「拿到了」，之后补名字），所以「已获得」与「有名字」是
    两件事，不能拿名字是否有值当完成条件。
    """

    done: bool = False
    name: str = ""
    at: str = ""


@dataclass
class ItemProgress:
    """单个条目的记录。

    ``done`` 是用户手里的枚数（``named`` 条目由子项推出）；``booked`` 是已经写进
    Profile 账本的部分，恒 ``≤ done``。两者不等就是「待入账」——补录常驻内容时
    专门用来记住哪些还没记账。
    """

    done: int = 0
    booked: int = 0
    at: str = ""
    slots: list[ItemSlot] = field(default_factory=list)


@dataclass
class LingerState:
    """一个用户的记录文档。

    清单里没有的键（旧版本、或用户换过清单）原样保留，读取方自行忽略，
    绝不在加载时清理用户数据。
    """

    revision: int = 0
    items: dict[str, ItemProgress] = field(default_factory=dict)

    def done(self, key: str) -> int:
        """已得枚数：``named`` 条目由子项推出，其余读存储值。"""
        progress = self.items.get(key)
        return progress.done if progress else 0

    def resolve_done(self, key: str, item: LingerItem) -> int:
        """按条目类型取已得枚数，并收紧到 ``0..count``。"""
        if item.named:
            return min(sum(1 for slot in self.slots(key) if slot.done), item.count)
        return min(self.done(key), item.count)

    def slots(self, key: str) -> list[ItemSlot]:
        """该条目已录入的子项（可能少于清单枚数）。"""
        progress = self.items.get(key)
        return list(progress.slots) if progress else []

    def booked(self, key: str) -> int:
        """已经写进账本的枚数。"""
        progress = self.items.get(key)
        return progress.booked if progress else 0

    def set_booked(self, key: str, value: int, done: int) -> bool:
        """记录「已入账多少枚」；恒不超过已得枚数，0 表示从未入账。"""
        clamped = max(0, min(int(value), max(0, int(done))))
        progress = self.items.get(key)
        if progress is None:
            return False
        if progress.booked == clamped:
            return False
        progress.booked = clamped
        return True

    def slot_view(self, key: str, count: int) -> list[ItemSlot]:
        """补齐到 ``count`` 位、供界面逐行渲染的视图（副本，不改存储）。"""
        slots = self.slots(key)[:count]
        return slots + [ItemSlot() for _ in range(count - len(slots))]

    def set_done(self, key: str, value: int, count: int) -> bool:
        """写入已得枚数；返回是否真的改了（未变化不写盘、不涨 revision）。

        ``value`` 按 ``count`` 收紧到 ``0..count``；归零表示「没拿到」，
        直接从记录里删掉，避免文件被一堆 0 撑大。已有记录上的 ``booked`` 与
        ``slots`` 必须留着——它们不是这次改动能替用户决定丢弃的东西。
        """
        clamped = max(0, min(int(value), max(1, int(count))))
        if clamped == self.done(key):
            return False
        if clamped == 0:
            self.items.pop(key, None)
            return True
        progress = self.items.get(key)
        if progress is None:
            self.items[key] = ItemProgress(done=clamped, at=now_iso())
        else:
            progress.done = clamped
            progress.at = now_iso()
            progress.booked = min(progress.booked, clamped)
        return True

    def set_slots(self, key: str, slots: list[ItemSlot], count: int) -> bool:
        """整体写入子项；多余的空位被裁掉，全空时删除该条目。"""
        normalized = [
            ItemSlot(done=bool(slot.done), name=str(slot.name or "").strip(), at=slot.at)
            for slot in slots[:max(1, int(count))]
        ]
        while normalized and not normalized[-1].done and not normalized[-1].name:
            normalized.pop()
        if not any(slot.done or slot.name for slot in normalized):
            if key not in self.items:
                return False
            self.items.pop(key, None)
            return True
        done = sum(1 for slot in normalized if slot.done)
        last_at = next((slot.at for slot in reversed(normalized) if slot.at), now_iso())
        current = self.items.get(key)
        if current is not None and current.slots == normalized and current.done == done:
            return False
        booked = min(current.booked, done) if current is not None else 0
        self.items[key] = ItemProgress(
            done=done, booked=booked, at=last_at, slots=normalized)
        return True

    def recorded_total(self, catalog: LingerCatalog) -> tuple[int, int]:
        """常驻来源的已记录枚数与总枚数；周期型和清单外的键都不计入。"""
        done_total = 0
        for region in catalog.regions:
            for item in region.items:
                if item.periodic:
                    continue
                done_total += self.resolve_done(item_key(region.id, item.id), item)
        return done_total, catalog.fixed_total

    def completed_items(self, catalog: LingerCatalog) -> tuple[int, int]:
        """已拿满的条目数与清单条目总数。"""
        completed = 0
        for region in catalog.regions:
            for item in region.items:
                if item.periodic:
                    continue
                if self.resolve_done(item_key(region.id, item.id), item) >= item.count:
                    completed += 1
        return completed, catalog.fixed_item_count

    def orphan_keys(self, catalog: LingerCatalog) -> list[str]:
        """记录里有、当前清单已找不到的键（页面据此提示，不自动删除）。"""
        return sorted(key for key in self.items if catalog.item(key) is None)

    def booked_total(self, catalog: LingerCatalog) -> int:
        """已写进账本的枚数合计（清单外的键不计）。"""
        total = 0
        for region in catalog.regions:
            for item in region.items:
                if item.periodic:
                    continue
                key = item_key(region.id, item.id)
                total += min(self.booked(key), self.resolve_done(key, item))
        return total

    def pending_total(self, catalog: LingerCatalog) -> int:
        """已记录但还没入账的枚数合计。"""
        recorded, _ = self.recorded_total(catalog)
        return max(0, recorded - self.booked_total(catalog))

    def to_dict(self) -> dict:
        payload: dict[str, dict] = {}
        for key, progress in sorted(self.items.items()):
            entry: dict[str, Any] = {"done": progress.done, "at": progress.at}
            if progress.booked:
                entry["booked"] = progress.booked
            if progress.slots:
                entry["slots"] = [
                    {k: v for k, v in (
                        ("done", slot.done), ("name", slot.name), ("at", slot.at)
                    ) if v not in ("", False)}
                    for slot in progress.slots
                ]
            payload[key] = entry
        return {"version": STATE_VERSION, "revision": self.revision, "items": payload}

    @classmethod
    def from_dict(cls, data: Any) -> "LingerState":
        """宽容解析：坏条目逐条丢弃，不让一处脏数据挡住整份记录。"""
        if not isinstance(data, dict):
            return cls()
        revision = data.get("revision", 0)
        try:
            revision_value = max(0, int(revision))
        except (TypeError, ValueError):
            revision_value = 0
        raw_items = data.get("items")
        items: dict[str, ItemProgress] = {}
        if isinstance(raw_items, dict):
            for key, value in raw_items.items():
                # 键 = 条目 id（uuid8）。历史文件里的 `地区/条目` 旧键取后半归一。
                if isinstance(key, str) and "/" in key:
                    key = key.rpartition("/")[2]
                if not isinstance(key, str) or not key or not isinstance(value, dict):
                    continue
                try:
                    done = int(value.get("done", 0))
                except (TypeError, ValueError):
                    continue
                slots = _slots_from_raw(value.get("slots"))
                if done <= 0 and not slots:
                    continue
                done = done if done > 0 else sum(1 for slot in slots if slot.done)
                booked = value.get("booked", 0)
                try:
                    booked_value = max(0, min(int(booked), done))
                except (TypeError, ValueError):
                    booked_value = 0
                at = value.get("at")
                items[key] = ItemProgress(
                    done=done,
                    booked=booked_value,
                    at=at if isinstance(at, str) else "",
                    slots=slots,
                )
        return cls(revision=revision_value, items=items)


def _slots_from_raw(raw: Any) -> list[ItemSlot]:
    """解析子项列表；坏行直接跳过，不把整份记录判死。"""
    if not isinstance(raw, list):
        return []
    slots: list[ItemSlot] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        at = entry.get("at")
        slots.append(ItemSlot(
            done=bool(entry.get("done", False)),
            name=name.strip() if isinstance(name, str) else "",
            at=at if isinstance(at, str) else "",
        ))
    return slots
