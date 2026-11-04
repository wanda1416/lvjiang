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


def _apply_op(op: dict) -> bool:
    """把一条远端 op 折进 entries；返回是否实际生效。

    **不写本地 history**，因此不会回声上行。

    回放按 remote_id（到达序）驱动，但**合并按操作时间**：远端来的旧 set
    （``happened_at`` 早于本地现值的 ``updated_at``）不覆盖新值——离线设备
    补传的陈旧 set 正是这种形态。delta 恒应用（加法可交换，顺序无关）。
    """
    username, type_, key = op["username"], op["type"], op["key"]
    happened = op.get("happened_at") or ""
    if op["op"] == "delta":
        current = repository.db_read_entry(username, type_, key)
        base = current.get("value") if isinstance(current, dict) else None
        base = float(base) if isinstance(base, (int, float)) else 0.0
        value = base + float(op.get("delta") or 0)
        repository.db_upsert(username, type_, key, value, change_type=None)
        return True
    text = op.get("value_text") or ""
    raw = op.get("value")
    if type_ != "note" and raw is not None:
        current = repository.db_read_entry(username, type_, key)
        local_at = (current or {}).get("updated_at") or ""
        if local_at and happened and happened < local_at:
            logger.info("[profile sync] 跳过旧 set：{}/{} {}@{} < 本地@{}",
                        username, type_, key, happened, local_at)
            return False
    if type_ == "note" or (text and raw is None):
        repository.db_upsert(username, type_, key, 0.0, value_text=text, change_type=None)
        return True
    repository.db_upsert(username, type_, key, float(raw or 0), change_type=None)
    return True


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
        newer_users = repository.db_users_with_history_since(created_at)
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
        # 锁定条目拒绝快照覆盖是**预期内**的（锁的语义就是物理失效），静默
        # 跳过即可——upsert 的锁拒绝分支会记 ERROR，整份落地会刷一屏误导
        # 性"写入被拒绝"，让人误以为数据事故（2026-11-04 13:20 的日志）。
        if repository.db_is_locked(username, type_, key):
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
    # 落地前的本地历史不上行：快照已代表全部用户的当前状态，自己的陈年
    # 账本推上去只会把旧值回放给别的设备（Android 首次同步曾因此倾倒
    # 22853 条历史，覆盖了别的设备刚写入的新值）。与 refresh_snapshot
    # 的语义一致。
    state["local_history_id"] = repository.db_max_history_id()
    save_profile_sync_state(state)
    report.snapshots += 1
    logger.info("[profile sync] 已应用快照 #{}（水位 {}，{} 项）",
                snapshot_row_id, watermark, applied)


def pull_and_replay(token: str, *, allow_overwrite: bool = False,
                    tolerate_no_snapshot: bool = False,
                    on_progress=None, stop=None) -> ProfileSyncReport:
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
        mine = current_instance_id()
        applied = 0
        for op in ops:
            # 回声护栏：自己实例推的 op 且本地 history 已有该行 → 其效果已含在
            # 本地状态里（entries 重建自完整 history），重放会造成 delta 双算。
            # id 匹配是快路径；库重建后本地 id 重新编号、云端旧 local_id 对不上，
            # 用内容指纹（用户+key+时间+数值）兜底，防止旧 delta 重复应用。
            op_instance = op.get("instance_id")
            op_local_id = op.get("local_id")
            if isinstance(op_instance, str) and op_instance == mine:
                if (isinstance(op_local_id, int)
                        and repository.db_history_row_exists(op_local_id)):
                    continue
                if repository.db_history_fingerprint_exists(
                        op["username"], op["type"], op["key"],
                        op.get("happened_at") or "",
                        delta=op.get("delta") if op.get("op") == "delta" else None,
                        new_value=op.get("value") if op.get("op") == "set" else None):
                    continue
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
    save_profile_sync_state(state)
    return report


#: 单次同步允许的最大上行积压。常规量按「周一 05:00 的周期重置风暴」定：
#: 19 用户 × ~25 个周期 key ≈ 500 条 reset，属**正常业务量**而非水位异常，
#: 上限必须容得下；分批（每批 200）上传。D1 免费额度 10 万行写/天，按每天
#: 2000 行可支撑 50 个用户，1000 的上限绰绰有余。真正超过的（长期离线数月、
#: 水位错乱）仍拒绝并提示刷新快照重建基线。
MAX_PUSH_BACKLOG = 1000


def push_local(token: str, on_progress=None, stop=None) -> ProfileSyncReport:
    """把本地 ``history.id`` 水位之后的 set/delta 上行——**只推本机写的行**。

    - 请求携带 ``base_remote_id``（自己的拉取水位）：云端发现落后于 head
      即拒绝，强制「先拉最新再上传」，防止基于过期基线的 op 混入。
    - 积压超过 :data:`MAX_PUSH_BACKLOG` 直接拒绝并提示刷新快照。
    """
    from .repository import _author_stamp

    report = ProfileSyncReport()
    state = _state()
    since = _local_history_id(state)
    author = _author_stamp()
    first = repository.db_get_history_since(since, MAX_PUSH_BACKLOG + 1, author=author)
    if len(first) > MAX_PUSH_BACKLOG:
        report.errors.append(
            f"待上行积压超过 {MAX_PUSH_BACKLOG} 条；请先「刷新快照」重建基线后再同步")
        return report
    batch = 0
    while True:
        if stop and stop():
            report.errors.append("已停止")
            break
        rows = repository.db_get_history_since(since, 200, author=author)
        if not rows:
            break
        batch += 1
        if on_progress:
            on_progress({"step": "overview", "detail": f"上行 第{batch}批",
                         "pushed": report.pushed})
        ops = [{
            "local_id": row["id"], "username": row["username"],
            "type": row["type"], "key": row["key"],
            # reset 上行映射为 set(0)：回放端按时间戳归零，跨边界离线设备
            # 不会把 delta 加在周期前的旧基线上（本地 change_type 保留
            # 'reset' 供 UI 显示「周期重置」）。
            "op": "delta" if row["change_type"] == "delta" else "set",
            "value": (row["new_value"] if row["change_type"] != "delta" else None),
            "delta": (row["delta_value"] if row["change_type"] == "delta" else None),
            "value_text": row["new_value_text"], "happened_at": row["ts"],
        } for row in rows]
        try:
            response = post("/api/sync/profile/push", {
                "ops": ops,
                "base_remote_id": int(state.get("remote_history_id") or 0)}, token)
        except SyncError as error:
            report.errors.append(str(error))
            break
        report.pushed += int(response.get("accepted") or 0)
        since = rows[-1]["id"]
        state["local_history_id"] = since
        save_profile_sync_state(state)
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
    definitions = _key_definitions()
    # 快照只带「有定义」的条目：entries 里可能存在定义已删除/改名的残留键
    # （例如 _of_week 改成 _of_month 后旧行还在，或某次写入用错了模型类型）。
    # 它们没有标签、没有分组，进快照只会在网页上冒出裸键列。**只过滤快照，
    # 不删本地数据**——本地是否清理由用户决定。
    defined = {(item["type"], item["key"]) for item in definitions}
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
                     "updated_at": entry.get("updated_at") or ""})
    if orphan:
        logger.info("[profile sync] 快照跳过 {} 条无定义的历史残留条目", orphan)
    if not payload:
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
    report.snapshots += 1
    # 快照已经代表了此刻全部用户的完整状态：本地既有历史无需再逐行上行，否则
    # 首次同步要把几万条陈年账本全推一遍（而新设备根本不需要它们）。
    state["local_history_id"] = repository.db_max_history_id()
    save_profile_sync_state(state)
    logger.info("[profile sync] 快照刷新完成：{}", report.describe())
    return report


def sync_all(token: str, *, allow_overwrite: bool = False,
             on_progress=None, stop=None) -> ProfileSyncReport:
    """一轮完整同步：先拉后推（先拿到远端状态，再把自己的新变更补上）。

    ``allow_overwrite=False`` 时若本机是「非初始设备」，只回报 pending 并
    停下——绝不静默覆盖本地数据。
    """
    pull = pull_and_replay(token, allow_overwrite=allow_overwrite,
                         on_progress=on_progress, stop=stop)
    if pull.pending or pull.errors and not pull.snapshots and not pull.pulled:
        return pull
    push = push_local(token, on_progress=on_progress, stop=stop)
    return ProfileSyncReport(pushed=push.pushed, pulled=pull.pulled,
                             snapshots=pull.snapshots, pending=pull.pending,
                             backup=pull.backup,
                             errors=[*pull.errors, *push.errors])
