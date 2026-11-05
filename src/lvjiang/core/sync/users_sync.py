"""用户表同步 —— 角色名册（带顺序）与用户资料入服务端 account_users 表。

- 上行：本地名册（``session.json`` 的 ``users`` 数组）+ 每个用户的资料
  （``users/{名}.json`` 去 ``ui_state``）。整批推送：资料行按 updated_at
  后写覆盖（LWW），顺序以本次推送为准。
- 下行：远端名册顺序重排本地 ``users`` 数组（本地独有的名字 append 保留）；
  资料合并回本地 json（本机 ``ui_state`` 永远保留）。
- 用户资料不再走文档通道（``user_doc`` 已废弃）；头像二进制与 profile.yaml
  仍是文档。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from ..sync.client import SyncError, post


#: 本地用户名册所在（session.json 的 users 数组）
def _session_path() -> Path:
    from ...constants import SESSION_CONFIG_DIR
    return SESSION_CONFIG_DIR / "session.json"


def _users_dir() -> Path:
    from ...constants import USERS_DIR
    return USERS_DIR


def _sync_state() -> dict:
    """上次成功同步时的名册签名，落 account.json 的 users_sync 小节。"""
    from ..account.service import account_config_path

    try:
        data = json.loads(account_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    state = data.get("users_sync") if isinstance(data, dict) else None
    return dict(state) if isinstance(state, dict) else {}


def _save_sync_state(state: dict) -> None:
    from ..account.service import account_config_path

    path = account_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
    except (OSError, ValueError):
        return
    data["users_sync"] = state
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as error:
        logger.warning("[users sync] 同步状态写回失败：{}", error)


def _roster() -> list[str]:
    from ..user_config import is_valid_username

    try:
        data = json.loads(_session_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = data.get("users") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [name for name in raw
            if isinstance(name, str) and is_valid_username(name)]


def _set_roster(names: list[str]) -> bool:
    """按给定顺序重写本地名册；失败返回 False（不动文件）。"""
    path = _session_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["users"] = names
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as error:
        logger.warning("[users sync] 名册顺序写回失败：{}", error)
        return False
    return True


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


@dataclass
class UsersSyncReport:
    pushed: int = 0
    pulled: int = 0
    avatars: int = 0
    reordered: bool = False
    errors: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if not (self.pushed or self.pulled or self.avatars or self.errors):
            return "已是最新，无改动"
        parts = [f"名册上行 {self.pushed}", f"下行 {self.pulled}"]
        if self.avatars:
            parts.append(f"头像 {self.avatars}")
        if self.reordered:
            parts.append("顺序已按云端重排")
        if self.errors:
            parts.append(f"失败 {len(self.errors)}")
        return "，".join(parts)


def sync_users(token: str, on_progress=None, stop=None) -> UsersSyncReport:
    """一轮名册同步：先推本地（资料 LWW + 顺序仅在本地变过时），再拉远端。

    顺序语义：本地名册与「上次同步签名」一致 → 常规推送（``order_changed=false``，
    服务端不动顺序）；不一致 → 本地排过序，``order_changed=true`` 让本次顺序生效。
    下行按云端顺序重排本地名册（本地独有名字 append）。
    """
    report = UsersSyncReport()
    def _prog(detail, **kw):
        if on_progress:
            on_progress({"step": "roster", "detail": detail, **kw})
    _prog("推送")
    roster = _roster()
    users_dir = _users_dir()
    state = _sync_state()
    signature = state.get("roster") if isinstance(state.get("roster"), list) else None
    order_changed = roster != signature

    if roster:
        from ..account.e2e import current_key, encrypt_json
        e2e_key = current_key()
        payload = []
        position = 0
        for name in roster:
            path = users_dir / f"{name}.json"
            if not path.is_file():
                # 名册里有名字但没有资料文件：不推空资料。空 data + 新时间戳会
                # 被服务端 LWW 当成更新，把云端真实属性冲成空对象。
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data.pop("ui_state", None)      # 界面偏好永不上云
            except (OSError, ValueError):
                data = {}
            # 基础属性端到端加密：本地明文 → 上云密文（封装对象按原样透传）。
            # 无密钥（未登录窗口/异常）则退回明文，读取端按 legacy 处理。
            cloud_data = encrypt_json(e2e_key, data) if e2e_key and data else data
            payload.append({"username": name, "position": position,
                            "updated_at": _mtime_iso(path),
                            "data": cloud_data})
            position += 1
        # 内容签名：名册顺序 + 各用户资料都没变 → 本次推送整个跳过。
        # 签名基于**上云形态**（密文），改密换钥后密文必变 → 自动触发全量重推。
        # 否则每次同步都全量发 19 行、D1 白写几十行配额，界面还恒显「上行 19」。
        if payload:
            content_sig = hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if not order_changed and content_sig == state.get("content_sig"):
                pass                                   # 无变化：跳过推送
            else:
                try:
                    response = post("/api/sync/users/push",
                                    {"users": payload, "order_changed": order_changed}, token)
                    # 服务端回报真实变更行数（新增/资料更新/顺序变更），
                    # 没变就是 0，不再把「发了多少行」当「上行了多少」。
                    report.pushed = int(response.get("changed", len(payload)))
                    state["content_sig"] = content_sig
                    _save_sync_state(state)
                    _prog("已推送", pushed=report.pushed)
                except SyncError as error:
                    report.errors.append(f"上行失败: {error}")

    try:
        _prog("拉取")
        response = post("/api/sync/users/pull", {}, token)
    except SyncError as error:
        report.errors.append(f"下行失败: {error}")
        return report
    remote_users = response.get("users") or []

    # 资料落地：密文解密后合并回本地 json（保留本机 ui_state）
    from ..account.e2e import current_key, is_encrypted
    from ..account.e2e import decrypt_json as _decrypt
    e2e_key = current_key()
    local_roster = _roster()
    for row in remote_users:
        name = row.get("username")
        data = row.get("data")
        if not isinstance(name, str) or not isinstance(data, dict) or not data:
            continue
        if is_encrypted(data):
            decrypted = _decrypt(e2e_key, data) if e2e_key else None
            if not decrypted:
                # 密文但本机无钥（改密后旧数据/异常）：跳过，绝不能把密文写进
                # 本地明文文件覆盖真实数据
                continue
            data = decrypted
        path = users_dir / f"{name}.json"
        if not path.is_file():
            # 本地没有该用户：直接用云端资料落盘创建（无 ui_state 可保留）。
            # 全新实例 / 初始同步备份后都要靠这条把远端用户补回本地。
            merged = {**data}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            report.pulled += 1
            continue
        try:
            local = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(local, dict):
            continue
        merged = {**data}
        ui_state = local.get("ui_state")
        if isinstance(ui_state, dict):
            merged["ui_state"] = ui_state
        if merged != local:
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            report.pulled += 1

    # 顺序落地：远端顺序为准，本地独有名字 append。本地刚排过序（签名存在且
    # 与当前名册不同）时以本地为准——云端已被本次推送改写。首次同步（无签名）
    # 与常规同步都要采用远端顺序，并把远端新增用户纳入本地。
    from ..user_config import is_valid_username
    remote_order: list[str] = []
    for row in remote_users:
        name = row.get("username")
        if isinstance(name, str) and is_valid_username(name) and name not in remote_order:
            remote_order.append(name)
    if signature is not None and order_changed:
        final_order = roster
    else:
        final_order = (remote_order + [name for name in local_roster
                                       if name not in remote_order]
                       ) if remote_order else local_roster
    if final_order and final_order != local_roster:
        if _set_roster(final_order):
            report.reordered = True
            if on_progress:
                # 名册一旦落盘就通知界面刷新用户选择器——不必等整轮四通道结束
                on_progress({"step": "roster", "detail": "完成", "reload_users": True})
            logger.info("[users sync] 名册已按云端顺序重排")
    if final_order and final_order != signature:
        state["roster"] = final_order
        _save_sync_state(state)
    # 头像同步暂时整体停用（读写都不再碰头像，数据保留）：后续单独设计。
    return report


@dataclass
class AvatarSyncReport:
    """一次性头像同步结果（手动按钮触发，不参与常规同步）。"""

    uploaded: int = 0
    downloaded: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if not (self.uploaded or self.downloaded or self.skipped or self.errors):
            return "名册里没有带头像的用户"
        parts = [f"上传 {self.uploaded}", f"下载 {self.downloaded}",
                 f"跳过 {self.skipped}"]
        if self.errors:
            parts.append(f"失败 {len(self.errors)}")
        return "，".join(parts)

    def distinct_errors(self) -> list[str]:
        seen: list[str] = []
        for error in self.errors:
            text = str(error).strip()
            if text and text not in seen:
                seen.append(text)
        return seen


#: 单个头像的大小上限（客户端与云端同口径）。
MAX_AVATAR_BYTES = 1_000_000
#: 批量下载时一次请求带多少个用户名。
AVATAR_BATCH = 10


def sync_all_avatars(token: str, users_dir: Path | None = None,
                     on_progress=None, stop=None) -> AvatarSyncReport:
    """手动触发的双向头像同步（**不加入常规同步**）。

    四种情况：
    - 本地有、云端无 → 上传
    - 本地无、云端有 → 下载
    - 两边都有且 sha 相同 → 跳过
    - 两边都有但不同 → 按时间裁决：本地文件 mtime 与云端 updated_at 比新

    只处理名册里的用户（与名册一致）；单个头像超过 1MB 跳过并计入失败。
    不做删除同步：本地清空 ``avatar`` 字段不会删云端那份。
    """
    import base64

    from ...constants import AVATARS_DIR

    users_dir = users_dir or _users_dir()
    report = AvatarSyncReport()

    def _prog(detail: str) -> None:
        if on_progress:
            on_progress({"detail": detail})

    names = _roster()
    if not names:
        return report

    # 云端现状：名册行带 avatar_sha256，但没有 updated_at，需要时按批补拉。
    try:
        remote_rows = post("/api/sync/users/pull", {}, token).get("users") or []
    except SyncError as error:
        report.errors.append(f"读取云端名册失败: {error}")
        return report
    cloud = {row.get("username"): row for row in remote_rows
             if isinstance(row, dict) and isinstance(row.get("username"), str)}

    # 1) 先收集本地缺失、云端有的头像，按批下载。
    missing: list[tuple[str, str]] = []
    for name in names:
        info = _local_avatar(users_dir, name)
        if info is None:
            continue                       # 该用户没有头像字段
        path, _sha = info
        if path.is_file():
            continue
        if (cloud.get(name) or {}).get("avatar_sha256"):
            missing.append((name, path.name))
    for start in range(0, len(missing), AVATAR_BATCH):
        if stop and stop():
            report.errors.append("已停止")
            return report
        chunk = missing[start:start + AVATAR_BATCH]
        _prog(f"下载头像 {start + 1}-{start + len(chunk)}/{len(missing)}")
        try:
            response = post("/api/sync/users/avatars/pull",
                            {"usernames": [name for name, _ in chunk]}, token)
        except SyncError as error:
            report.errors.append(f"头像批量下载失败: {error}")
            continue
        items = {item.get("username"): item for item in response.get("avatars") or []
                 if isinstance(item, dict) and isinstance(item.get("username"), str)}
        for name, filename in chunk:
            item = items.get(name)
            payload = item.get("payload_base64") if item else None
            if not isinstance(payload, str):
                continue
            try:
                raw = base64.b64decode(payload)
            except ValueError as error:
                report.errors.append(f"头像解码失败({name}): {error}")
                continue
            target = AVATARS_DIR / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            report.downloaded += 1

    # 2) 再逐用户处理「上传 / 冲突裁决」。
    done = 0
    for name in names:
        if stop and stop():
            report.errors.append("已停止")
            return report
        done += 1
        info = _local_avatar(users_dir, name)
        if info is None:
            continue
        path, _ = info
        remote_sha = (cloud.get(name) or {}).get("avatar_sha256")
        if not path.is_file():
            if not remote_sha:
                report.skipped += 1       # 双方都没有
            continue                      # 云端有但刚才下载失败：不重复上报
        local_bytes = path.read_bytes()
        local_sha = hashlib.sha256(local_bytes).hexdigest()
        if not remote_sha:
            _prog(f"上传头像 {done}/{len(names)}")
            if _upload_avatar(token, name, local_sha, local_bytes, report):
                report.uploaded += 1
            continue
        if local_sha == remote_sha:
            report.skipped += 1
            continue
        if _local_is_newer(path, token, name):
            _prog(f"上传头像 {done}/{len(names)}")
            if _upload_avatar(token, name, local_sha, local_bytes, report):
                report.uploaded += 1
        else:
            _prog(f"下载头像 {done}/{len(names)}")
            if _download_avatar(token, name, path, report):
                report.downloaded += 1
    return report


def _local_avatar(users_dir: Path, username: str) -> tuple[Path, str] | None:
    """用户资料里的头像路径；没有头像字段返回 None。"""
    from ...constants import AVATARS_DIR

    try:
        data = json.loads((users_dir / f"{username}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    filename = data.get("avatar") if isinstance(data, dict) else None
    if not isinstance(filename, str) or not filename:
        return None
    return AVATARS_DIR / filename, filename


def _local_is_newer(path: Path, token: str, username: str) -> bool:
    """本地文件 mtime 是否新于云端 updated_at；取不到云端时间时按本地赢。"""
    try:
        response = post("/api/sync/users/avatars/pull",
                        {"usernames": [username]}, token)
    except SyncError:
        return True
    items = [item for item in response.get("avatars") or []
             if isinstance(item, dict) and item.get("username") == username]
    if not items:
        return True                       # 云端查不到：按本地赢（与上传语义一致）
    stamp = items[0].get("updated_at")
    if not isinstance(stamp, str) or not stamp:
        return True
    try:
        if stamp.endswith("Z"):
            stamp = stamp[:-1] + "+00:00"
        cloud_at = datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return True
    return path.stat().st_mtime >= cloud_at


def _upload_avatar(token: str, name: str, sha: str, raw: bytes,
                   report: AvatarSyncReport) -> bool:
    import base64

    if len(raw) > MAX_AVATAR_BYTES:
        report.errors.append(f"头像超过 1MB 未上传({name})")
        return False
    try:
        post("/api/sync/users/avatar/put", {
            "username": name, "sha256": sha,
            "payload_base64": base64.b64encode(raw).decode()}, token)
        return True
    except SyncError as error:
        report.errors.append(f"头像上传失败({name}): {error}")
        return False


def _download_avatar(token: str, name: str, avatar_path: Path,
                     report: AvatarSyncReport) -> bool:
    import base64

    try:
        avatar = post("/api/sync/users/avatar/get", {"username": name}, token).get("avatar")
        if not isinstance(avatar, dict) or not isinstance(avatar.get("payload_base64"), str):
            return False
        raw = base64.b64decode(avatar["payload_base64"])
        avatar_path.parent.mkdir(parents=True, exist_ok=True)
        avatar_path.write_bytes(raw)
        return True
    except (SyncError, OSError, ValueError) as error:
        report.errors.append(f"头像下载失败({name}): {error}")
        return False
