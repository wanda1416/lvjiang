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
import sys
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from ..account.service import profile_sync_state, save_profile_sync_state
from ..sync.client import SyncError, post
from . import repository
from .arbitrate import canonical_event, is_strictly_newer


@dataclass
class ProfileSyncReport:
    pushed: int = 0
    pulled: int = 0
    snapshots: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)
    #: 需用户确认才能覆盖的用户（远端快照来自别的设备，且本地已有数据）
    pending: list[dict] = field(default_factory=list)
    backup: str = ""
    #: 早于云端快照、本批不再重传的本地 history.id
    skipped_pre_snapshot: list[int] = field(default_factory=list)

    def describe(self) -> str:
        if not (self.pushed or self.pulled or self.snapshots or self.errors
                or self.pending or self.skipped_pre_snapshot):
            return "已是最新，无改动"
        parts = [f"上行 {self.pushed} 条", f"下行 {self.pulled} 条"]
        if self.snapshots:
            parts.append(f"快照 {self.snapshots} 个")
        if self.rejected:
            parts.append(f"时间异常放弃 {self.rejected} 条")
        if self.backup:
            parts.append(f"已备份到 {self.backup}")
        if self.skipped_pre_snapshot:
            parts.append(f"早于快照跳过 {len(self.skipped_pre_snapshot)} 条")
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
NO_SNAPSHOT_HINT = "云端还没有总览快照；请先在 PC 端完成初始同步后继续。"


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
    """同步前的进度校验。水位超前不再要求用户清除，交给 rebase 把水位拉回。"""
    del token
    return ""


def profile_protocol_block(token: str) -> str:
    """协议不是 2 时返回提示，调用方不得再发 Profile op。"""
    try:
        body = post("/api/sync/state", {}, token)
    except SyncError as error:
        return str(error)
    if body.get("protocol_version") != 2:
        return "服务端尚未升级，已停止总览同步"
    return ""


def clear_sync_state() -> None:
    """清除本地同步进度（用户显式操作）。快照与账本数据不动。"""
    save_profile_sync_state({})
    logger.info("[profile sync] 已按用户操作清除本地同步进度")


def _quota_key(key: str):
    """配额定义。未注册周期或不是配额时返回 None，回放就不做周期归零。"""
    from .models import QuotaKeyDef
    from .schema import get_profile_config

    found = get_profile_config().get_key(key, model_type="quota")
    return found if isinstance(found, QuotaKeyDef) else None


def _wall_clock(value: str) -> datetime | None:
    """解析时间并留下钟面。带偏移的时间去掉时区，不换算到本机时区。

    周期边界是本地钟面（例如每天 05:00），库存的 ``updated_at`` 也是无时区钟面。
    把 ``09:00+08:00`` 按运行机器的时区换算，会在非东八区把周期判错。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed


def _period_relation(op: dict, current: dict) -> str | None:
    """配额操作相对本地条目的周期：``new`` / ``same`` / ``old``。无法判断时返回 None。"""
    if op.get("type") != "quota":
        return None
    happened_at = _wall_clock(op.get("happened_at") or "")
    if happened_at is None:
        return None
    key_def = _quota_key(str(op.get("key") or ""))
    if key_def is None:
        return None
    from .periods import get_period_boundary

    reset_day = getattr(key_def, "reset_day", 0)
    try:
        boundary = get_period_boundary(
            key_def.period, key_def.reset_time, happened_at, reset_day)
        now_boundary = get_period_boundary(
            key_def.period, key_def.reset_time, datetime.now(), reset_day)
    except (TypeError, ValueError):
        return None
    local_at = current.get("updated_at") or ""
    if not local_at:
        # 没有本地时间：操作自己已经落在过去的周期里，就不要写进当前值。
        if boundary < now_boundary:
            return "old"
        return "new"
    local_dt = _wall_clock(local_at)
    if local_dt is None:
        return None
    if local_dt < boundary:
        return "new"
    try:
        local_boundary = get_period_boundary(
            key_def.period, key_def.reset_time, local_dt, reset_day)
    except (TypeError, ValueError):
        return None
    if boundary < local_boundary:
        return "old"
    return "same"


def _apply_op(op: dict) -> bool:
    """把一条远端 op 折进本地 entries 与账本；返回是否实际生效。

    **回放照常写账本**（``change_type`` 取 op 语义，``device_id`` 取来源实例，
    ``origin_local_id`` 取来源设备的 local_id）。账本行的身份是
    ``(device_id, origin_local_id)``，与本地自增 id 无关，所以：

    - 重复回放（页级重试、水位没落盘就崩溃）天然幂等，见
      :func:`repository.db_ledger_op_seen`；
    - 上行按 ``device_id = 本机`` 过滤，回放行永不被代传，构不成回声。

    以前这里刻意不写 history 来"防回声"，代价是跨端写入完全不可见：值被改
    回去却查不到是谁、什么时候、从哪台设备改的（2026-11-06 太平武墓被另一
    实例的陈旧 set 覆盖就是这么丢的）。

    回放按 remote_id（到达序）驱动，但**合并按操作时间**：同一周期里，
    远端来的旧 set（``happened_at`` 早于本地现值的 ``updated_at``）不覆盖
    新值。配额跨进新周期时先把本机底数当成 0 再套用；属于上一周期的操作
    直接丢弃。两种情况都不另写 reset 历史。delta 在同一周期内加在当前值上。

    回放写入时把条目 ``updated_at`` 定为 op 的 ``happened_at`` 而不是本地
    墙钟：两台设备的时钟互不可信，本地墙钟会让回放后的所有条目都"新于"
    此后到达的远端 set，护栏从此永久误跳（2026-11-04 手机端 3179 事故的
    根因）。条目时间戳必须是**源写入时间**，护栏比较的才是同一种时间。
    """
    if op.get("op") not in (None, "set"):
        return False
    origin = str(op.get("instance_id") or "")
    origin_local_id = op.get("local_id")
    op_id = str(op.get("op_id") or "")
    if not op_id and origin and isinstance(origin_local_id, int):
        op_id = f"legacy:{origin}:{origin_local_id}"
    if not op_id:
        logger.warning("[profile sync] 忽略缺 op_id 的 op：remote_id={}",
                       op.get("remote_id"))
        return False
    if repository.db_op_id_seen(op_id):
        return False

    username, type_, key = op["username"], op["type"], op["key"]
    happened = canonical_event(op.get("happened_at") or "")
    current = repository.db_read_entry(username, type_, key)
    if not isinstance(current, dict):
        current = {}
    text = op.get("value_text") or ""
    raw = op.get("value")
    if type_ == "note" or (text and raw is None):
        value = float(current.get("value") or 0)
        value_text = text
    else:
        value = float(raw or 0)
        value_text = ""
    remote_id = op.get("remote_id")
    remote_mark = remote_id if isinstance(remote_id, int) else None
    newer = is_strictly_newer(
        happened, op_id, current.get("updated_at") or "", current.get("op_id") or "",
    )
    locked = bool(current.get("locked")) or repository.db_is_locked(username, type_, key)
    if locked or not newer:
        repository.db_upsert(
            username, type_, key, value,
            change_type="set",
            source=str(op.get("source") or ""),
            value_text=value_text,
            device_id=origin,
            origin_local_id=origin_local_id if isinstance(origin_local_id, int) else None,
            history_ts=happened or None,
            op_id=op_id,
            remote_id=remote_mark,
            touch_entry=False,
        )
        return False
    repository.db_upsert(
        username, type_, key, value,
        updated_at=happened or None,
        change_type="set",
        source=str(op.get("source") or ""),
        value_text=value_text,
        device_id=origin,
        origin_local_id=origin_local_id if isinstance(origin_local_id, int) else None,
        history_ts=happened or None,
        op_id=op_id,
        remote_id=remote_mark,
    )
    return True


def hadwent(happened: str) -> str | None:
    """回放写入用的条目时间戳：源 op 时间；缺失时 None（upsert 落本地墙钟）。

    独立小函数只为命名意图清晰：条目的 ``updated_at`` 必须"曾经发生"在
    源写入时刻，而不是本机回放时刻。
    """
    return happened or None


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

    判定：快照来自别实例 **且** 本地那几个用户已有数据；或**无论快照来自谁**，
    本地存在晚于快照创建时间的 history 行——快照是"过去某刻"的状态，本地
    完全可能有快照之后、未推送的新数据，"快照来自本机"绝不构成静默覆盖的
    通行证（2026-11-04 事故：本机旧快照静默盖掉 323 行未推送更新）。
    本地为空（新设备）不需要警告——那里没有东西可丢。
    """
    mine = current_instance_id()
    if not mine:
        return []
    snapshot = latest_snapshot(token)
    if snapshot is None:
        return []
    created_at = snapshot.get("created_at") or ""
    from_mine = snapshot.get("origin_instance_id") == mine
    newer_users: set[str] = set()
    if from_mine and created_at:
        # 只算**本机写的**行：跨端回放行同样落在这张表里，把它们算进来会让
        # "快照已包含本地全部状态"这个判断失效，白白弹一次覆盖确认。
        newer_users = repository.db_users_with_history_since(
            created_at, author=repository._author_stamp())
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
        if from_mine and username not in newer_users:
            continue   # 快照已包含该用户的全部本地状态，覆盖无损
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
    candidates = []
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
        # 锁定条目拒绝快照覆盖是**预期内**的（锁的语义就是物理失效），静默
        # 跳过即可——upsert 的锁拒绝分支会记 ERROR，整份落地会刷一屏误导
        # 性"写入被拒绝"，让人误以为数据事故（2026-11-04 13:20 的日志）。
        candidates.append({
            "username": username,
            "type": type_,
            "key": key,
            "value": entry.get("value"),
            "value_text": entry.get("value_text") or "",
            "updated_at": (
                canonical_event(entry.get("updated_at") or "")
                or canonical_event(str(snapshot.get("created_at") or ""))
            ),
            "op_id": str(entry.get("op_id") or ""),
        })
    applied = repository.db_merge_snapshot_candidates(candidates)
    # 两个编号别混：profile_snapshot_id 是「哪一份快照」（表主键），
    # snapshot_remote_id 是「该快照覆盖到账本的哪个水位」。
    snapshot_row_id = int(snapshot.get("id") or 0)
    watermark = int(snapshot.get("snapshot_remote_id") or 0)
    state["profile_snapshot_id"] = snapshot_row_id
    state["remote_history_id"] = max(int(state.get("remote_history_id") or 0), watermark)
    state["snapshot_remote_id"] = watermark
    save_profile_sync_state(state)
    report.snapshots += 1
    logger.info("[profile sync] 已应用快照 #{}（水位 {}，{} 项）",
                snapshot_row_id, watermark, applied)


def _needs_snapshot_rebase(state: dict, summaries: list[dict]) -> bool:
    """本地快照 id 与云端最新一份不同，或水位低于那份快照时要按 key 合并。"""
    if not summaries:
        return False
    latest = summaries[0]
    snap_id = int(latest.get("id") or 0)
    snap_wm = int(latest.get("snapshot_remote_id") or 0)
    local_id = int(state.get("profile_snapshot_id") or 0)
    local_wm = int(state.get("remote_history_id") or 0)
    if snap_id and local_id != snap_id:
        return True
    return local_wm < snap_wm


def pull_and_replay(token: str, *, allow_overwrite: bool = False,
                    tolerate_no_snapshot: bool = False,
                    on_progress=None, stop=None) -> ProfileSyncReport:
    """先按需合并最新快照，再回放水位之后的 set。

    ``allow_overwrite`` 保留给旧调用方，不再阻断同步。
    ``tolerate_no_snapshot`` 保留给创建第一份快照的路径。
    """
    del allow_overwrite, tolerate_no_snapshot
    report = ProfileSyncReport()
    blocked = profile_protocol_block(token)
    if blocked:
        report.errors.append(blocked)
        return report
    state = _state()
    head = remote_head(token)
    if isinstance(head, int) and int(state.get("remote_history_id") or 0) > head:
        state["remote_history_id"] = 0
        save_profile_sync_state(state)
    summaries = remote_snapshots(token)
    if not summaries:
        logger.info("[profile sync] 云端还没有总览快照，直接上传本机记录")
    elif _needs_snapshot_rebase(state, summaries):
        try:
            _bootstrap(token, report)
        except SyncError as error:
            report.errors.append(str(error))
            return report
        state = _state()
    since = int(state.get("remote_history_id") or 0)
    page = 0
    while True:
        if stop and stop():
            report.errors.append("已停止")
            break
        page += 1
        if on_progress:
            on_progress({"step": "overview", "detail": f"回放 第{page}页",
                         "pulled": report.pulled})
        try:
            response = post("/api/sync/profile/pull",
                                {"since_remote_id": since, "limit": 500}, token)
        except SyncError as error:
            report.errors.append(str(error))
            break
        ops = response.get("ops") or []
        applied = 0
        for op in ops:
            # 判重与回声护栏都在 _apply_op 里，依据是账本行身份
            # (来源实例, 该实例的 local_id)，不再靠"id 匹配 + 内容指纹"猜。
            try:
                if _apply_op(op):
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
    # 干净拉取记一笔诊断时间。单条 op 回放失败只进 report.errors，游标照样前移。
    if not report.errors and not report.pending:
        state["last_pull_ok_at"] = datetime.now().isoformat(timespec="seconds")
    save_profile_sync_state(state)
    return report


#: 单次同步允许的最大上行积压。只数 set 与 delta；周期 reset 不上传。
#: 分批（每批 200）上传。D1 免费额度 10 万行写/天，按每天 2000 行可支撑
#: 50 个用户，1000 的上限绰绰有余。真正超过的（长期离线数月、水位错乱）
#: 仍拒绝并提示刷新快照重建基线。
MAX_PUSH_BACKLOG = 1000


def _snapshot_candidates(token: str) -> dict[tuple[str, str, str], tuple[str, str]]:
    """最新快照里每个 key 的 ``(updated_at, op_id)``。没有快照时为空。"""
    state = _state()
    if not state.get("profile_snapshot_id"):
        return {}
    snapshot = latest_snapshot(token)
    if not snapshot:
        return {}
    try:
        entries = json.loads(snapshot.get("entries") or "[]")
    except ValueError:
        return {}
    candidates: dict[tuple[str, str, str], tuple[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        username, type_, key = entry.get("username"), entry.get("type"), entry.get("key")
        if isinstance(username, str) and isinstance(type_, str) and isinstance(key, str):
            candidates[(username, type_, key)] = (
                str(entry.get("updated_at") or ""), str(entry.get("op_id") or ""),
            )
    return candidates


def push_local(token: str, on_progress=None, stop=None) -> ProfileSyncReport:
    """上传新于快照候选的本机 set/delta。更旧的行标成 -1，不进云端。"""
    from .repository import _author_stamp

    report = ProfileSyncReport()
    blocked = profile_protocol_block(token)
    if blocked:
        report.errors.append(blocked)
        return report
    author = _author_stamp()
    repository.db_reopen_abandoned(author)
    if not repository.db_list_unacked(author, 1):
        return report
    candidates = _snapshot_candidates(token)
    batch = 0
    while True:
        if stop and stop():
            report.errors.append("已停止")
            break
        rows = repository.db_list_unacked(author, 200)
        if not rows:
            break
        upload = []
        abandon: list[tuple[int, int]] = []
        for row in rows:
            candidate = candidates.get((row["username"], row["type"], row["key"]))
            other_at, other_id = candidate if candidate is not None else ("", "")
            if is_strictly_newer(row.get("ts") or "", row.get("op_id") or "", other_at, other_id):
                upload.append(row)
            else:
                abandon.append((int(row["id"]), -1))
        if abandon:
            repository.db_mark_remote_ids(abandon)
        if not upload:
            continue
        batch += 1
        if on_progress:
            on_progress({"step": "overview", "detail": f"上行 第{batch}批",
                         "pushed": report.pushed})
        ops = [{
            "op_id": row["op_id"],
            "local_id": int(row.get("origin_local_id") or row["id"]),
            "username": row["username"],
            "type": row["type"],
            "key": row["key"],
            "op": "set",
            "value": row["new_value"],
            "value_text": row["new_value_text"],
            "source": row.get("source") or "",
            "happened_at": canonical_event(row.get("ts") or "") or row.get("ts") or "",
        } for row in upload]
        try:
            response = post("/api/sync/profile/push", {"ops": ops}, token)
        except SyncError as error:
            report.errors.append(str(error))
            break
        results = response.get("results")
        if not isinstance(results, list):
            report.errors.append("服务端没有按条返回 ack")
            break
        by_op = {row["op_id"]: row for row in upload}
        marks: list[tuple[int, int]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            local = by_op.get(str(item.get("op_id") or ""))
            if local is None:
                continue
            status = item.get("status")
            if status in ("accepted", "duplicate") and isinstance(item.get("remote_id"), int):
                marks.append((int(local["id"]), int(item["remote_id"])))
                if status == "accepted":
                    report.pushed += 1
            elif status == "rejected":
                marks.append((int(local["id"]), -1))
                report.rejected += 1
                logger.warning("[profile sync] 放弃上传 {}：{}",
                               local["op_id"], item.get("error") or status)
        repository.db_mark_remote_ids(marks)
        if len(marks) != len(upload):
            report.errors.append("有上行结果无法对应本地行，下轮重试")
            break
    if report.rejected:
        report.errors.append(
            f"有 {report.rejected} 条记录因时间异常未上传，请校准系统时间")
    return report


def _key_definitions() -> list[dict]:
    """profile.yaml 的定义摘要（type/key/label/group/cap），**直接解析原始 YAML**。

    三处刻意的取舍：
    - **不走 ProfileSchema**：schema 会把「周期未注册」的条目挪进
      ``ignored_by_model``（插件没加载时 yysls 的 season 键会被整条移出），
      用它做过滤会误删这些键；原始 YAML 不受插件影响。
    - **不含 period**：云端只做展示，没有重置/编辑能力，周期是客户端运行时
      概念（周期边界归零），对展示毫无意义。
    - **只用于过滤，不上传**：定义的唯一来源是 profile.yaml（文档通道同步），
      不随快照存副本——副本必然漂移。网页直接解析 profile.yaml 取标签。
    """
    import yaml

    from ...constants import SESSION_CONFIG_DIR

    path = SESSION_CONFIG_DIR / "profile.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as error:
        logger.warning("[profile sync] profile.yaml 读取失败，定义摘要为空：{}", error)
        return []
    if not isinstance(data, dict):
        return []
    keys: list[dict] = []
    for model_type, items in data.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not isinstance(key, str) or not key:
                continue
            entry: dict[str, str | int | float] = {
                "type": model_type, "key": key,
                "label": str(item.get("label") or key),
                "group": str(item.get("group") or ""),
            }
            cap = item.get("cap")
            if isinstance(cap, (int, float)):
                entry["cap"] = cap
            keys.append(entry)
    return keys


def _skipped_pre_snapshot(raw) -> list[int]:
    if not isinstance(raw, list):
        return []
    ids: list[int] = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("local_id"), int):
            ids.append(item["local_id"])
        elif isinstance(item, int):
            ids.append(item)
    return ids


def local_tz_offset_minutes() -> int:
    """本机相对 UTC 的偏移（分钟）。东八区为 480。"""
    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return 0
    return int(offset.total_seconds() // 60)


def _snapshot_entries() -> tuple[list[dict], int]:
    """本机有定义的总览条目，以及被跳过的无定义残留条数。"""
    from ..user_config import list_user_names

    defined = {(item["type"], item["key"]) for item in _key_definitions()}
    payload: list[dict] = []
    orphan = 0
    for username in list_user_names():
        entries = repository.db_read_all(username)
        for type_, group in entries.items():
            for key, entry in group.items():
                if (type_, key) not in defined:
                    orphan += 1
                    continue
                payload.append(
                    {"username": username, "type": type_, "key": key,
                     "value": entry.get("value"),
                     "value_text": entry.get("value_text") or "",
                     "updated_at": entry.get("updated_at") or "",
                     "op_id": entry.get("op_id") or ""})
    return payload, orphan


def local_profile_has_entries() -> bool:
    """本机是否已有能写入快照的总览值。"""
    payload, _orphan = _snapshot_entries()
    return bool(payload)


def refresh_snapshot(token: str, *, allow_empty: bool = False) -> ProfileSyncReport:
    """把本机**全部用户**的当前 entries 作为一份账户级快照上传，
    绑到「此刻的远端最大 remote_id」。

    **只有 PC 能建快照**：Android 是没有总览编辑能力的镜像端，建快照等于把
    手机那份 db 当成全网基线；这里做硬门禁，任何调用路径都挡住。

    **上传前先拉一遍**：快照绑定的 ``snapshot_remote_id`` 语义是「该 id 及之前
    的变更都已体现在这份 entries 里」。若不先同步，别的设备刚推上来的行会被
    这个水位"吞掉"——新设备拿到的是旧基线，且那些行再也不会被回放。
    已有设备各自按自己的水位继续，不受影响。

    ``allow_empty=False``（手动「创建快照」）在没有任何可上传条目时直接失败。
    初始播种传 True，允许用空列表建立第一份基线。
    """
    report = ProfileSyncReport()
    if hasattr(sys, "getandroidapilevel"):
        report.errors.append("手机端不能创建总览快照；请在 PC 端完成初始同步")
        return report
    pulled = pull_and_replay(token, tolerate_no_snapshot=True)
    report.pulled = pulled.pulled
    report.errors.extend(pulled.errors)
    # 拉取失败就不要建快照：水位会把没拉到的远端行吞掉。
    if pulled.errors:
        return report
    state = _state()
    # 快照只带「有定义」的条目：entries 里可能存在定义已删除/改名的残留键
    # （例如 _of_week 改成 _of_month 后旧行还在，或某次写入用错了模型类型）。
    # 它们没有标签、没有分组，进快照只会在网页上冒出裸键列。**只过滤快照，
    # 不删本地数据**——本地是否清理由用户决定。
    payload, orphan = _snapshot_entries()
    if orphan:
        logger.info("[profile sync] 快照跳过 {} 条无定义的历史残留条目", orphan)
    if not payload and not allow_empty:
        report.errors.append("本机没有可上传的 profile 数据")
        return report
    try:
        response = post("/api/sync/profile/snapshot/put",
                        {"entries": payload,
                         "origin_name": socket.gethostname()}, token)
    except SyncError as error:
        report.errors.append(str(error))
        return report
    state["profile_snapshot_id"] = int(response.get("id") or 0) or None
    # 快照水位必须一并记下：快照语义是「该 remote_id 及之前的变更都已体现在
    # entries 里」。漏记会让本机后续拉取从 0 开始重复回放。
    watermark = int(response.get("snapshot_remote_id") or 0)
    state["remote_history_id"] = max(int(state.get("remote_history_id") or 0), watermark)
    state["snapshot_remote_id"] = watermark
    report.snapshots += 1
    save_profile_sync_state(state)
    logger.info("[profile sync] 快照刷新完成：{}", report.describe())
    return report


def sync_all(token: str, *, allow_overwrite: bool = False,
             on_progress=None, stop=None) -> ProfileSyncReport:
    """一轮完整同步：先确认协议，再拉后推。"""
    del allow_overwrite
    blocked = profile_protocol_block(token)
    if blocked:
        return ProfileSyncReport(errors=[blocked])
    pull = pull_and_replay(token, on_progress=on_progress, stop=stop)
    if pull.errors and not pull.snapshots and not pull.pulled:
        return pull
    push = push_local(token, on_progress=on_progress, stop=stop)
    return ProfileSyncReport(
        pushed=push.pushed, pulled=pull.pulled, snapshots=pull.snapshots,
        rejected=push.rejected, pending=pull.pending, backup=pull.backup,
        errors=[*pull.errors, *push.errors])
