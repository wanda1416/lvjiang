"""profile 账本同步 —— 远端 ``remote_id`` 与本地 ``history.id`` 是两套编号。

本地设备 A、B 完全可能各自产生 ``id=100`` 的 history 行；远端按**接收顺序**
分配自己的 ``remote_id``。所以：

- 上行只报本地 id 供幂等（``(instance_id, local_id)``），远端不回传映射
- 下行按 ``remote_id`` 水位拉，落地时**不写本地 history**（回放只影响 entries），
  因此不存在"把远端行又推回去"的回声
- ``tick`` / ``reset`` 是时间派生，永不上云，两端按同一份 profile.yaml 各自算

新设备落地 = 远端 entries 快照 + ``remote_id`` 之后的增量回放。
"""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field

from loguru import logger

from ..account.service import profile_sync_state, save_profile_sync_state
from ..sync.client import SyncError, post
from . import repository


@dataclass
class ProfileSyncReport:
    pushed: int = 0
    pulled: int = 0
    snapshots: int = 0
    errors: list[str] = field(default_factory=list)
    #: 需用户确认才能覆盖的用户（远端快照来自别的设备，且本地已有数据）
    pending: list[dict] = field(default_factory=list)
    backup: str = ""

    def describe(self) -> str:
        if not (self.pushed or self.pulled or self.snapshots or self.errors or self.pending):
            return "已是最新，无改动"
        parts = [f"上行 {self.pushed} 条", f"下行 {self.pulled} 条"]
        if self.snapshots:
            parts.append(f"快照 {self.snapshots} 个")
        if self.backup:
            parts.append(f"已备份到 {self.backup}")
        if self.errors:
            parts.append(f"失败 {len(self.errors)}")
        return "，".join(parts)


def _state() -> dict:
    state = profile_sync_state()
    state.setdefault("remote_history_id", 0)
    state.setdefault("local_history_id", 0)
    # 本设备落地时用的是哪一份快照（账户级多份里的 id）
    if not isinstance(state.get("profile_snapshot_id"), int):
        state["profile_snapshot_id"] = 0
    return state


def _local_history_id(state: dict) -> int:
    value = state.get("local_history_id")
    return value if isinstance(value, int) and value >= 0 else 0


#: 同步进度与云端不一致时的提示。**只提示，绝不动本地数据**——自动重置水位
#: 一旦判断出错就会静默丢数据，必须由用户自己决定「清除同步」。
RESET_HINT = "本地记录的同步进度比云端新（云端可能被重建或已换库）。请在账户管理中点「清除同步」后重新登录同步。"
NO_SNAPSHOT_HINT = "云端还没有快照。请先在其中一台设备上点「刷新快照」建立基线，再让其他设备登录。"


def remote_head(token: str) -> int | None:
    """云端账本当前最大 remote_id。**鉴权失败返回 None**（不能当 0——否则
    会误导出「进度比云端新」的错误结论）；网络失败也返回 None 让调用方跳过。"""
    try:
        response = post("/api/sync/profile/snapshot", {}, token)
    except SyncError:
        return None
    head = response.get("head_remote_id")
    return head if isinstance(head, int) and head >= 0 else None


def state_problem(token: str) -> str:
    """同步前的进度校验：返回提示文案，没问题返回空串。

    只做「发现并告知」，不修正任何本地状态。只比较 ``remote_history_id``：
    它与 head 同在远端编号空间；``local_history_id`` 是本地编号，与 head
    不可比（新库 head 归零不代表本地编号要归零）。
    鉴权失败（head 未知）时跳过校验——那是令牌问题，不是进度问题。
    """
    state = _state()
    head = remote_head(token)
    if head is None:
        return ""                       # 令牌/网络问题：让后续真正的同步调用报准确的错
    remote = int(state.get("remote_history_id") or 0)
    if remote > head:
        return RESET_HINT
    return ""


def clear_sync_state() -> None:
    """清除本地同步进度（用户显式操作）。快照与账本数据不动。"""
    save_profile_sync_state({})
    logger.info("[profile sync] 已按用户操作清除本地同步进度")


def _apply_op(op: dict) -> None:
    """把一条远端 op 折进 entries；**不写本地 history**，因此不会回声上行。"""
    username, type_, key = op["username"], op["type"], op["key"]
    if op["op"] == "delta":
        current = repository.db_read_entry(username, type_, key)
        base = current.get("value") if isinstance(current, dict) else None
        base = float(base) if isinstance(base, (int, float)) else 0.0
        value = base + float(op.get("delta") or 0)
        repository.db_upsert(username, type_, key, value, change_type=None)
        return
    text = op.get("value_text") or ""
    raw = op.get("value")
    if type_ == "note" or (text and raw is None):
        repository.db_upsert(username, type_, key, 0.0, value_text=text, change_type=None)
        return
    repository.db_upsert(username, type_, key, float(raw or 0), change_type=None)


def remote_snapshots(token: str) -> list[dict]:
    """云端快照清单（账户级、可多份历史，最新在前；只带摘要）。"""
    try:
        response = post("/api/sync/profile/snapshot", {}, token)
    except SyncError as error:
        logger.warning("[profile sync] 快照清单获取失败：{}", error)
        return []
    return [row for row in (response.get("snapshots") or []) if isinstance(row, dict)]


def latest_snapshot(token: str) -> dict | None:
    """最新一份快照的完整内容（含全部用户 entries）；没有则 None。"""
    snapshots = remote_snapshots(token)
    if not snapshots:
        return None
    snapshot_id = snapshots[0].get("id")
    if not isinstance(snapshot_id, int):
        return None
    try:
        response = post("/api/sync/profile/snapshot", {"id": snapshot_id}, token)
    except SyncError as error:
        logger.warning("[profile sync] 快照详情获取失败：{}", error)
        return None
    snapshot = response.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else None


def current_instance_id() -> str:
    """本实例标识（已登录时必然存在）。"""
    from ..account.service import current_instance_id as _current

    return _current()

def overwrite_warnings(token: str) -> list[dict]:
    """本机将被云端快照覆盖的用户清单。

    快照是账户级的一份；判定：快照来自别实例 **且** 本地那几个用户已有数据。
    本地为空（新设备）不需要警告——那里没有东西可丢。
    """
    mine = current_instance_id()
    if not mine:
        return []
    snapshot = latest_snapshot(token)
    if snapshot is None or snapshot.get("origin_instance_id") == mine:
        return []
    warnings: list[dict] = []
    try:
        entries = json.loads(snapshot.get("entries") or "[]")
    except ValueError:
        return []
    names = sorted({
        name for entry in entries if isinstance(entry, dict)
        for name in [entry.get("username")]
        if isinstance(name, str)
    })
    for username in names:
        local = repository.db_read_all(username) or {}
        total = sum(len(group) for group in local.values() if isinstance(group, dict))
        if not total:
            continue
        warnings.append({
            "username": username,
            "entries": total,
            "history": repository.db_count_history(username),
            "origin_name": snapshot.get("origin_name") or "另一台设备",
        })
    return warnings


def backup_local() -> str:
    """覆盖前把本地 profile 数据整份备份，返回备份目录（失败返回空串）。

    备份 ``profile.db``（含 -wal/-shm）与 ``users/``——正是快照会覆盖的范围。
    """
    import shutil
    from datetime import datetime

    from ...constants import SESSION_CONFIG_DIR, USERS_DIR

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = SESSION_CONFIG_DIR / f"_sync-backup-{stamp}"
    try:
        target.mkdir(parents=True, exist_ok=True)
        database = SESSION_CONFIG_DIR / "profile.db"
        for suffix in ("", "-wal", "-shm"):
            source = database.with_name(database.name + suffix)
            if source.is_file():
                shutil.copy2(source, target / source.name)
        users = USERS_DIR
        if users.is_dir():
            shutil.copytree(users, target / "users", dirs_exist_ok=True)
    except OSError as error:
        logger.error("[profile sync] 覆盖前备份失败：{}", error)
        return ""
    logger.info("[profile sync] 已备份本地 profile 数据到 {}", target)
    return str(target)


def _bootstrap(token: str, report: ProfileSyncReport) -> None:
    """新设备落地：取**最新一份**账户级快照（含所有用户），作为本地基线。"""
    state = _state()
    snapshot = latest_snapshot(token)
    if snapshot is None:
        report.errors.append(NO_SNAPSHOT_HINT)
        return
    try:
        entries = json.loads(snapshot.get("entries") or "[]")
    except ValueError:
        report.errors.append("快照格式无效")
        return
    applied = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        username = entry.get("username")
        type_ = entry.get("type")
        key = entry.get("key")
        if not isinstance(username, str) or not isinstance(type_, str) or not isinstance(key, str):
            continue
        if not username or not type_ or not key:
            continue
        value = entry.get("value")
        repository.db_upsert(
            username, type_, key,
            float(value) if isinstance(value, (int, float)) else 0.0,
            updated_at=entry.get("updated_at") or None,
            value_text=entry.get("value_text") or "", change_type=None)
        applied += 1
    # 两个编号别混：profile_snapshot_id 是「哪一份快照」（表主键），
    # snapshot_remote_id 是「该快照覆盖到账本的哪个水位」。
    snapshot_row_id = int(snapshot.get("id") or 0)
    watermark = int(snapshot.get("snapshot_remote_id") or 0)
    state["profile_snapshot_id"] = snapshot_row_id
    state["remote_history_id"] = max(int(state.get("remote_history_id") or 0), watermark)
    save_profile_sync_state(state)
    report.snapshots += 1
    logger.info("[profile sync] 已应用快照 #{}（水位 {}，{} 项）",
                snapshot_row_id, watermark, applied)


def pull_and_replay(token: str, *, allow_overwrite: bool = False,
                    tolerate_no_snapshot: bool = False) -> ProfileSyncReport:
    """把远端增量折进 entries。首次（无水位）先取快照作基线。

    非初始设备（远端快照来自别的实例、本地又有数据）**不覆盖**：只回填
    ``report.pending`` 让界面确认。确认后带 ``allow_overwrite=True`` 再来一次，
    此时先整份备份本地 profile 数据再落地。
    ``tolerate_no_snapshot``：远端还没有任何快照时不视为错误（首建快照的
    refresh 路径用它——那里 bootstrap 本来就没有可取的基线）。
    """
    report = ProfileSyncReport()
    state = _state()
    problem = state_problem(token)
    if problem:
        report.errors.append(problem)     # 只提示，本地数据不动
        return report
    # bootstrap 只做一次：以「从未落地过快照」为准（profile_snapshot_id==0）。
    # **不能**用 remote_history_id==0 判断——新库的快照水位就是 0，那样会
    # 每次同步都把 entries 回滚到旧快照，抹掉 tick 累计与未推送的改动
    # （这正是 2026-11-03 数据损坏事故的根源）。
    if not state.get("profile_snapshot_id"):
        snapshots = [row for row in remote_snapshots(token)]
        if not snapshots:
            if tolerate_no_snapshot:
                pass                      # 远端还没有基线，直接从 0 回放
            else:
                report.errors.append(NO_SNAPSHOT_HINT)
                return report
        else:
            pending = overwrite_warnings(token)
            if pending and not allow_overwrite:
                report.pending = pending
                return report
            if pending:
                report.backup = backup_local()
                if not report.backup:
                    report.errors.append("本地备份失败，已中止覆盖")
                    return report
            try:
                _bootstrap(token, report)
            except SyncError as error:
                report.errors.append(str(error))
                return report
            state = _state()
    since = int(state.get("remote_history_id") or 0)
    while True:
        try:
            response = post("/api/sync/profile/pull",
                                {"since_remote_id": since, "limit": 500}, token)
        except SyncError as error:
            report.errors.append(str(error))
            break
        ops = response.get("ops") or []
        mine = current_instance_id()
        applied = 0
        for op in ops:
            # 回声护栏：自己实例推的 op 且本地 history 已有该行 → 其效果已含在
            # 本地状态里（entries 重建自完整 history），重放会造成 delta 双算。
            # 库重建后的窗口期（水位归零）尤其会撞上这批自己的历史 op。
            op_instance = op.get("instance_id")
            op_local_id = op.get("local_id")
            if (isinstance(op_instance, str) and op_instance == mine
                    and isinstance(op_local_id, int)
                    and repository.db_history_row_exists(op_local_id)):
                continue
            try:
                _apply_op(op)
                applied += 1
            except Exception as error:      # 单条失败不拖垮整轮
                report.errors.append(f"{op.get('username')}/{op.get('key')}: {error}")
        report.pulled += applied
        cursor = int(response.get("last_remote_id") or since)
        if cursor <= since or not ops:
            break
        since = cursor
        state["remote_history_id"] = since
        save_profile_sync_state(state)
    state["remote_history_id"] = since
    save_profile_sync_state(state)
    return report


def push_local(token: str) -> ProfileSyncReport:
    """把本地 ``history.id`` 水位之后的 set/delta 上行。"""
    report = ProfileSyncReport()
    state = _state()
    since = _local_history_id(state)
    while True:
        rows = repository.db_get_history_since(since, 200)
        if not rows:
            break
        ops = [{
            "local_id": row["id"], "username": row["username"],
            "type": row["type"], "key": row["key"], "op": row["change_type"],
            "value": row["new_value"] if row["change_type"] == "set" else None,
            "delta": row["delta_value"] if row["change_type"] == "delta" else None,
            "value_text": row["new_value_text"], "happened_at": row["ts"],
        } for row in rows]
        try:
            response = post("/api/sync/profile/push", {"ops": ops}, token)
        except SyncError as error:
            report.errors.append(str(error))
            break
        report.pushed += int(response.get("accepted") or 0)
        since = rows[-1]["id"]
        state["local_history_id"] = since
        save_profile_sync_state(state)
    return report


def _key_definitions() -> list[dict]:
    """profile.yaml 的定义摘要（type/key/label/group/cap）。

    云端只做展示，没有重置/编辑能力，因此**不含 period**——周期是客户端
    运行时概念（周期边界归零），对展示毫无意义。浏览器无法零依赖解析 YAML，
    这份摘要随快照上传，供网页把裸 key 渲染成带中文标签的表。
    """
    from .schema import get_profile_config

    keys: list[dict] = []
    for model_type, key_defs in get_profile_config().keys_by_model.items():
        for kd in key_defs:
            item: dict[str, str | int] = {"type": model_type, "key": kd.key,
                                          "label": kd.label or kd.key, "group": kd.group or ""}
            cap = getattr(kd, "cap", None)
            if cap is not None:
                item["cap"] = cap
            keys.append(item)
    return keys


def refresh_snapshot(token: str) -> ProfileSyncReport:
    """把本机**全部用户**的当前 entries 作为一份账户级快照上传，
    绑到「此刻的远端最大 remote_id」。

    **上传前先拉一遍**：快照绑定的 ``snapshot_remote_id`` 语义是「该 id 及之前
    的变更都已体现在这份 entries 里」。若不先同步，别的设备刚推上来的行会被
    这个水位"吞掉"——新设备拿到的是旧基线，且那些行再也不会被回放。
    已有设备各自按自己的水位继续，不受影响。
    """
    from ..user_config import list_user_names

    report = ProfileSyncReport()
    pulled = pull_and_replay(token, tolerate_no_snapshot=True)
    report.pulled = pulled.pulled
    report.errors.extend(pulled.errors)
    state = _state()
    payload: list[dict] = []
    for username in list_user_names():
        entries = repository.db_read_all(username)
        payload.extend(
            {"username": username, "type": type_, "key": key,
             "value": entry.get("value"), "value_text": entry.get("value_text") or "",
             "updated_at": entry.get("updated_at") or ""}
            for type_, group in entries.items() for key, entry in group.items()
        )
    if not payload:
        report.errors.append("本机没有可上传的 profile 数据")
        return report
    try:
        response = post("/api/sync/profile/snapshot/put",
                        {"entries": payload, "keys": _key_definitions(),
                         "origin_name": socket.gethostname()}, token)
    except SyncError as error:
        report.errors.append(str(error))
        return report
    state["profile_snapshot_id"] = int(response.get("id") or 0) or None
    # 快照水位必须一并记下：快照语义是「该 remote_id 及之前的变更都已体现在
    # entries 里」。漏记会让本机后续拉取从 0 开始重复回放。
    watermark = int(response.get("snapshot_remote_id") or 0)
    state["remote_history_id"] = max(int(state.get("remote_history_id") or 0), watermark)
    report.snapshots += 1
    # 快照已经代表了此刻全部用户的完整状态：本地既有历史无需再逐行上行，否则
    # 首次同步要把几万条陈年账本全推一遍（而新设备根本不需要它们）。
    state["local_history_id"] = repository.db_max_history_id()
    save_profile_sync_state(state)
    logger.info("[profile sync] 快照刷新完成：{}", report.describe())
    return report


def sync_all(token: str, *, allow_overwrite: bool = False) -> ProfileSyncReport:
    """一轮完整同步：先拉后推（先拿到远端状态，再把自己的新变更补上）。

    ``allow_overwrite=False`` 时若本机是「非初始设备」，只回报 pending 并
    停下——绝不静默覆盖本地数据。
    """
    pull = pull_and_replay(token, allow_overwrite=allow_overwrite)
    if pull.pending or pull.errors and not pull.snapshots and not pull.pulled:
        return pull
    push = push_local(token)
    return ProfileSyncReport(pushed=push.pushed, pulled=pull.pulled,
                             snapshots=pull.snapshots, pending=pull.pending,
                             backup=pull.backup,
                             errors=[*pull.errors, *push.errors])
