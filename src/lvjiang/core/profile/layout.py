"""用户总览的可见结构（``profile.json``）

**职责分工**：``profile.yaml`` 管定义——有哪些 key、标签、上限、周期、同步
目标；``profile.json`` 管可见结构——有哪些分组、每组展示哪些列、列顺序、
合计行开关与列宽。两者同处 ``config/session``，随账号同步，不随版本包分发。

**为什么独立成文件**：``interface.json`` 是会话级界面状态（窗口尺寸、当前
页面、批量草稿……），只在本机有效。总览结构需要跨机器一致、并且要在 web 端
展示，因此从 ``interface.json`` 的 ``profile`` 节点与 ``ui_state.profile_overview``
迁出，单独成为一个可上传/下载的文件。

**活跃分组不进本文件**：用户「上次看的是哪个分组」是本机视图状态，切换页签
的频率远高于结构编辑，同步它只会制造无意义的版本。它留在
``interface.json`` 的 ``ui_state.profile_active_group``。

**列宽存 key 不存下标**：旧实现把列宽按下标数组存，增删列必须手工搬数组。
这里按列 key 存，增删列天然对齐；用户名列宽只存一份（``name_column_width``），
各分组天然一致。列宽是像素值，下载到别的机器时要按度量环境折算后 clamp，
见 :func:`scaled_width`。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from loguru import logger

from lvjiang.core.config import get_interface_store
from lvjiang.core.fs_util import atomic_write_bytes, interprocess_lock
from lvjiang.core.json_files import formatted_json_bytes

LAYOUT_FILENAME = "profile.json"

#: 文件结构版本；缺失或更旧即按 v1 迁移/初始化。
CURRENT_VERSION = 1

#: 单列宽度上限，挡住别的机器带来的离谱宽列。
MAX_COLUMN_WIDTH = 480

#: 没有任何记录时的列宽，与总览表 ``setDefaultSectionSize(120)`` 一致。
DEFAULT_COLUMN_WIDTH = 120

#: ``interface.json`` 中迁移前的旧节点名（迁移后清空）。
_LEGACY_PROFILE_NODE = "profile"
_LEGACY_SUBKEYS = ("overview_groups", "overview_group_order", "overview_active_group")
_LEGACY_WIDTHS_NODE = "profile_overview"
_ACTIVE_GROUP_KEY = "profile_active_group"


# ─── 数据模型 ────────────────────────────────────────────────


@dataclass(frozen=True)
class LayoutMetric:
    """记录列宽是在什么度量环境下量出来的。

    列宽是像素值，跨机器直接照搬会在字体/缩放不同的机器上失真。这里只记录
    解释宽度所必需的三项，不承载任何身份信息（谁写的是服务端的事）。
    """

    ui_scale: float = 1.0
    font_px: int = 0
    lang: str = ""


@dataclass
class GroupLayout:
    """一个总览分组的可见结构。"""

    columns: list[str] = field(default_factory=list)
    show_totals: bool = False
    name_column_width: int = 0
    column_widths: dict[str, int] = field(default_factory=dict)

    def as_store_dict(self) -> dict:
        """``store.get_groups()`` 历史上返回的形态，供 UI 无感消费。"""
        return {"columns": list(self.columns), "show_totals": bool(self.show_totals)}


@dataclass
class OverviewLayout:
    group_order: list[str] = field(default_factory=list)
    groups: dict[str, GroupLayout] = field(default_factory=dict)

    def ordered(self) -> dict[str, GroupLayout]:
        """按 ``group_order`` 排列，未登记的组按字典顺序追加在末尾。"""
        ordered = {name: self.groups[name] for name in self.group_order if name in self.groups}
        for name, group in self.groups.items():
            ordered.setdefault(name, group)
        return ordered


@dataclass
class ProfileLayout:
    version: int = CURRENT_VERSION
    metric: LayoutMetric = field(default_factory=LayoutMetric)
    overview: OverviewLayout = field(default_factory=OverviewLayout)


# ─── 度量环境（由 UI 层在启动时写入） ─────────────────────────


_environment = LayoutMetric()
#: 最近一次读盘失败的原因；UI 用它提示「当前展示的是默认结构」。
last_read_error: str = ""


def set_environment(metric: LayoutMetric) -> None:
    """由总览页在字体/缩放确定后调用，之后每次落盘都把当前环境写进文件。"""
    global _environment
    _environment = metric


def current_environment() -> LayoutMetric:
    return _environment


# ─── 路径与读写 ──────────────────────────────────────────────


def layout_path() -> Path:
    """``config/session/profile.json``（动态取常量，便于测试隔离）。"""
    from lvjiang import constants

    return Path(constants.SESSION_CONFIG_DIR) / LAYOUT_FILENAME


def load(*, path: Path | None = None) -> ProfileLayout:
    """读取结构文件；缺失时先尝试从旧 ``interface.json`` 节点迁移。"""
    target = path or layout_path()
    ensure_migrated(target)
    return _read(target)


def save(doc: ProfileLayout, *, path: Path | None = None) -> None:
    """整份写入（调用方已明确要落盘）。"""
    target = path or layout_path()
    with interprocess_lock(target, suffix="layout"):
        _write(target, doc)


def save_if_exists(doc: ProfileLayout, *, path: Path | None = None) -> None:
    """只在文件已存在时写回，避免「打开程序就凭空创建结构文件」。"""
    target = path or layout_path()
    if not target.exists():
        return
    save(doc, path=target)


def _read(target: Path) -> ProfileLayout:
    global last_read_error
    if not target.exists():
        last_read_error = ""
        return ProfileLayout(metric=current_environment())
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        doc = _from_mapping(raw)
    except Exception as exc:  # noqa: BLE001 - 损坏文件不应拖垮总览页
        last_read_error = f"{target.name}: {exc}"
        logger.error("profile.json 解析失败，按默认结构展示: {}: {}", target, exc)
        return ProfileLayout(metric=current_environment())
    last_read_error = ""
    return doc


def _from_mapping(raw: object) -> ProfileLayout:
    if not isinstance(raw, dict):
        raise ValueError("顶层必须是对象")
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise ValueError("缺少合法的 version")
    raw_metric = raw.get("metric")
    metric_raw: dict = raw_metric if isinstance(raw_metric, dict) else {}
    metric = LayoutMetric(
        ui_scale=_as_float(metric_raw.get("ui_scale"), 1.0),
        font_px=_as_int(metric_raw.get("font_px"), 0),
        lang=str(metric_raw.get("lang") or ""),
    )
    raw_overview = raw.get("overview")
    overview_raw: dict = raw_overview if isinstance(raw_overview, dict) else {}
    raw_groups = overview_raw.get("groups")
    groups_raw: dict = raw_groups if isinstance(raw_groups, dict) else {}
    groups: dict[str, GroupLayout] = {}
    for name, value in groups_raw.items():
        if not isinstance(name, str) or not name or not isinstance(value, dict):
            continue
        columns = value.get("columns")
        widths = value.get("column_widths")
        groups[name] = GroupLayout(
            columns=[k for k in columns if isinstance(k, str)] if isinstance(columns, list) else [],
            show_totals=value.get("show_totals") is True,
            name_column_width=max(0, _as_int(value.get("name_column_width"), 0)),
            column_widths={
                k: max(0, _as_int(v, 0)) for k, v in widths.items() if isinstance(k, str)
            }
            if isinstance(widths, dict)
            else {},
        )
    order_raw = overview_raw.get("group_order")
    order = [n for n in order_raw if isinstance(n, str)] if isinstance(order_raw, list) else []
    return ProfileLayout(
        version=version, metric=metric, overview=OverviewLayout(group_order=order, groups=groups)
    )


def parse_bytes(raw: bytes) -> ProfileLayout:
    """解析并校验一段结构文件内容，**不落盘**。

    下载路径先过这一关：远端内容坏了就什么都不动，而不是先覆盖再报错。
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"页面结构不是合法的 UTF-8 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("页面结构顶层必须是对象")
    version = data.get("version")
    if isinstance(version, int) and version > CURRENT_VERSION:
        raise ValueError(
            f"页面结构版本 {version} 高于本程序支持的 {CURRENT_VERSION}，请升级客户端")
    return _from_mapping(data)


def _write(target: Path, doc: ProfileLayout) -> None:
    payload = {
        "version": CURRENT_VERSION,
        "metric": {
            "ui_scale": round(doc.metric.ui_scale, 4),
            "font_px": int(doc.metric.font_px),
            "lang": doc.metric.lang,
        },
        "overview": {
            "group_order": [n for n in doc.overview.ordered()],
            "groups": {
                name: {
                    "columns": list(group.columns),
                    "show_totals": bool(group.show_totals),
                    "name_column_width": int(group.name_column_width),
                    "column_widths": {
                        key: int(width) for key, width in group.column_widths.items()
                    },
                }
                for name, group in doc.overview.ordered().items()
            },
        },
    }
    atomic_write_bytes(
        target, formatted_json_bytes(payload), prefix=f".{target.stem}_", suffix=".tmp"
    )


# ─── 分组读写（store 层复用） ────────────────────────────────


def group_snapshot(*, path: Path | None = None) -> dict[str, dict]:
    """有序分组快照，形态与历史实现一致：``{name: {"columns", "show_totals"}}``。"""
    doc = load(path=path)
    return {name: group.as_store_dict() for name, group in doc.overview.ordered().items()}


def mutate_groups(
    mutator: Callable[[dict[str, GroupLayout]], None], *, path: Path | None = None,
) -> None:
    """锁内读-改-写分组；顺序以字典顺序为准并写回 ``group_order``。

    只碰结构文件，不动 ``interface.json``；活跃分组由调用方另行维护。
    """
    target = path or layout_path()
    with interprocess_lock(target, suffix="layout"):
        ensure_migrated(target)
        doc = _read(target)
        groups = doc.overview.ordered()
        mutator(groups)
        doc.overview.groups = groups
        doc.overview.group_order = list(groups)
        doc.metric = current_environment()
        _write(target, doc)


def set_group_widths(
    group_name: str,
    *,
    name_column_width: int,
    widths: dict[str, int],
    path: Path | None = None,
) -> None:
    """写入一个分组的列宽；用户名列宽对所有分组生效（天然一致）。"""
    clean = {
        str(key): max(0, int(value)) for key, value in widths.items() if isinstance(key, str)
    }
    name_width = max(0, int(name_column_width))

    def _apply(groups: dict[str, GroupLayout]) -> None:
        group = groups.setdefault(group_name, GroupLayout())
        group.column_widths = clean
        if name_width > 0:
            for other in groups.values():
                other.name_column_width = name_width

    mutate_groups(_apply, path=path)


def group_widths(
    group_name: str, *, path: Path | None = None,
) -> tuple[int, dict[str, int]]:
    """返回 ``(用户名列宽, {列 key: 宽度})``；无记录时为空值。"""
    doc = load(path=path)
    group = doc.overview.groups.get(group_name)
    if group is None:
        return 0, {}
    return group.name_column_width, dict(group.column_widths)


def rename_column_keys(
    mapper: Callable[[str], str], *, path: Path | None = None,
) -> int:
    """按 ``mapper`` 改写所有分组的列引用与列宽键；返回改动的引用数。

    定义里改名后，总览分组不能继续引用旧 key（列会凭空消失）。列宽跟着
    一起搬，否则改名等于把用户调好的宽度清掉。``mapper`` 对无关 key 返回
    原值，因此重复执行结果相同——崩溃恢复依赖这一点。
    """
    target = path or layout_path()
    if not target.exists():
        return 0
    changed = 0

    def _apply(groups: dict[str, GroupLayout]) -> None:
        nonlocal changed
        for group in groups.values():
            renamed: list[str] = []
            for key in group.columns:
                new_key = mapper(key) or key
                changed += 1 if new_key != key else 0
                renamed.append(new_key)
            group.columns = renamed
            if group.column_widths:
                widths: dict[str, int] = {}
                for key, width in group.column_widths.items():
                    new_key = mapper(key) or key
                    widths.setdefault(new_key, width)
                group.column_widths = widths

    mutate_groups(_apply, path=target)
    return changed


# ─── 列宽折算与 clamp ───────────────────────────────────────


def scaled_width(
    raw: int,
    *,
    stored: LayoutMetric,
    current: LayoutMetric,
    minimum: int,
    maximum: int = MAX_COLUMN_WIDTH,
) -> int:
    """把记录宽度折算到当前度量环境，并夹在 ``[minimum, maximum]``。

    ``minimum`` 由调用方按当前字体实测（够显示表头即可），所以任何字体、
    语言下都不会把列标题截断；``maximum`` 挡住别机带来的离谱宽列。

    折算结果**不回写文件**：只有用户自己拖动列宽才更新记录，否则 A 下载→
    按自己字体折算→回写→B 再折算，宽度会在两台机器之间来回漂。
    """
    floor = max(1, int(minimum))
    ceiling = max(floor, int(maximum))
    if raw <= 0:
        return min(max(DEFAULT_COLUMN_WIDTH, floor), ceiling)
    font_ratio = (
        current.font_px / stored.font_px
        if stored.font_px > 0 and current.font_px > 0
        else 1.0
    )
    scale_ratio = (
        current.ui_scale / stored.ui_scale
        if stored.ui_scale > 0 and current.ui_scale > 0
        else 1.0
    )
    value = int(round(raw * font_ratio * scale_ratio))
    return max(floor, min(value, ceiling))


# ─── 从 interface.json 迁移（v1，一次性） ────────────────────


def ensure_migrated(path: Path | None = None) -> bool:
    """结构文件缺失时，把旧 ``interface.json`` 节点迁成 ``profile.json``。

    只有在确实存在旧数据时才创建文件；全新实例保持无文件状态，等用户真正
    编辑结构后才落盘。返回是否执行了迁移。
    """
    target = path or layout_path()
    if target.exists():
        return False
    session = get_interface_store()
    raw_legacy = session.get_node(_LEGACY_PROFILE_NODE, {})
    legacy: dict = raw_legacy if isinstance(raw_legacy, dict) else {}
    raw_groups = legacy.get("overview_groups")
    groups_legacy: dict = raw_groups if isinstance(raw_groups, dict) else {}
    raw_ui_state = session.get_node("ui_state", {})
    ui_state: dict = raw_ui_state if isinstance(raw_ui_state, dict) else {}
    raw_widths = ui_state.get(_LEGACY_WIDTHS_NODE)
    widths_legacy: dict = raw_widths if isinstance(raw_widths, dict) else {}
    if not groups_legacy and not widths_legacy:
        return False

    overview = _migrate_overview(legacy, groups_legacy, widths_legacy)
    backup_path = _backup_legacy(
        target,
        {
            "overview_groups": groups_legacy,
            "overview_group_order": legacy.get("overview_group_order", []),
            "overview_active_group": legacy.get("overview_active_group", ""),
            "ui_state_profile_overview": widths_legacy,
        },
    )
    doc = ProfileLayout(metric=current_environment(), overview=overview)
    with interprocess_lock(target, suffix="layout"):
        _write(target, doc)

    active = legacy.get("overview_active_group")
    existing_active = session.get_node("ui_state", {})
    existing_active = (
        existing_active.get(_ACTIVE_GROUP_KEY) if isinstance(existing_active, dict) else None
    )
    if isinstance(active, str) and active and not existing_active:
        session.update_node("ui_state", {_ACTIVE_GROUP_KEY: active})
    _cleanup_legacy(session)
    logger.info(
        "profile 总览结构已迁移到 {}（{} 个分组），旧节点已清理；迁移前备份: {}",
        target, len(overview.groups), backup_path,
    )
    return True


def _migrate_overview(
    legacy: dict, groups_raw: dict, widths_raw: dict,
) -> OverviewLayout:
    order_raw = legacy.get("overview_group_order")
    order = [n for n in order_raw if isinstance(n, str)] if isinstance(order_raw, list) else []
    # 旧实现按记录顺序优先、其余按字典序追加；迁移必须复刻同一顺序，
    # 否则升级后第一次打开会发现分组顺序变了。
    names = [n for n in order if n in groups_raw]
    names += [n for n in groups_raw if n not in names]

    groups: dict[str, GroupLayout] = {}
    for name in names:
        value = groups_raw.get(name)
        value = value if isinstance(value, dict) else {}
        columns = value.get("columns")
        columns = [k for k in columns if isinstance(k, str)] if isinstance(columns, list) else []
        widths = widths_raw.get(name)
        widths = widths if isinstance(widths, list) else []
        name_width = _as_int(widths[0], 0) if widths else 0
        column_widths = {}
        for index, key in enumerate(columns):
            width = _as_int(widths[index + 1], 0) if index + 1 < len(widths) else 0
            if width > 0:
                column_widths[key] = width
        groups[name] = GroupLayout(
            columns=columns,
            show_totals=value.get("show_totals") is True,
            name_column_width=max(0, name_width),
            column_widths=column_widths,
        )
    return OverviewLayout(group_order=names, groups=groups)


def _cleanup_legacy(session) -> None:
    """删除旧节点，避免 interface.json 与 profile.json 双源。"""

    def _drop(node):
        if not isinstance(node, dict):
            return node
        return {k: v for k, v in node.items() if k not in _LEGACY_SUBKEYS}

    remaining = session.mutate_node(_LEGACY_PROFILE_NODE, _drop)
    if not remaining:
        session.delete_node(_LEGACY_PROFILE_NODE)

    def _drop_widths(node):
        if not isinstance(node, dict):
            return node
        return {k: v for k, v in node.items() if k != _LEGACY_WIDTHS_NODE}

    session.mutate_node("ui_state", _drop_widths)


def _backup_legacy(target: Path, payload: dict) -> Path:
    """迁移前把旧结构留一份，便于人工回退；不参与任何自动读取。"""
    return _write_backup(
        target, formatted_json_bytes(payload), name="profile-layout-before.json"
    )


def backup_current(*, reason: str = "manual", path: Path | None = None) -> Path | None:
    """把现有结构文件原样备份到 ``_backup-<时间戳>/``；没有文件时返回 ``None``。

    下载覆盖与迁移都必须先留退路：结构是用户手工排出来的，丢了没法重建。
    """
    target = path or layout_path()
    if not target.exists():
        return None
    backup = _write_backup(target, target.read_bytes(), name="profile-layout-before.json")
    logger.info("页面结构已备份（{}）: {}", reason, backup)
    return backup


def _write_backup(target: Path, payload: bytes, *, name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = target.parent / f"_backup-{stamp}"
    suffix = 1
    while directory.exists():
        suffix += 1
        directory = target.parent / f"_backup-{stamp}-{suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    backup = directory / name
    atomic_write_bytes(backup, payload, prefix=f".{Path(name).stem}_", suffix=".tmp")
    return backup


# ─── 小工具 ──────────────────────────────────────────────────


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return default
    return default


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def with_metric(doc: ProfileLayout, metric: LayoutMetric) -> ProfileLayout:
    """返回带指定度量环境的副本（测试与上传前刷新用）。"""
    return replace(doc, metric=metric)
