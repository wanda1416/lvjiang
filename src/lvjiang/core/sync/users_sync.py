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
        return False
    if not isinstance(data, dict):
        return False
    data["users"] = names
    try:
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


def sync_users(token: str) -> UsersSyncReport:
    """一轮名册同步：先推本地（资料 LWW + 顺序仅在本地变过时），再拉远端。

    顺序语义：本地名册与「上次同步签名」一致 → 常规推送（``order_changed=false``，
    服务端不动顺序）；不一致 → 本地排过序，``order_changed=true`` 让本次顺序生效。
    下行按云端顺序重排本地名册（本地独有名字 append）。
    """
    report = UsersSyncReport()
    roster = _roster()
    users_dir = _users_dir()
    state = _sync_state()
    signature = state.get("roster") if isinstance(state.get("roster"), list) else None
    order_changed = roster != signature

    if roster:
        payload = []
        for position, name in enumerate(roster):
            path = users_dir / f"{name}.json"
            data: dict = {}
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    data.pop("ui_state", None)      # 界面偏好永不上云
                except (OSError, ValueError):
                    data = {}
            payload.append({"username": name, "position": position,
                            "updated_at": _mtime_iso(path) if path.is_file()
                              else datetime.now().isoformat(timespec="seconds"),
                            "data": data})
        # 内容签名：名册顺序 + 各用户资料都没变 → 本次推送整个跳过。
        # 否则每次同步都全量发 19 行、D1 白写几十行配额，界面还恒显「上行 19」。
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
            except SyncError as error:
                report.errors.append(f"上行失败: {error}")

    try:
        response = post("/api/sync/users/pull", {}, token)
    except SyncError as error:
        report.errors.append(f"下行失败: {error}")
        return report
    remote_users = response.get("users") or []

    # 资料落地：合并回本地 json（保留本机 ui_state）
    local_roster = _roster()
    for row in remote_users:
        name = row.get("username")
        data = row.get("data")
        if not isinstance(name, str) or not isinstance(data, dict) or not data:
            continue
        path = users_dir / f"{name}.json"
        if not path.is_file():
            continue                          # 本地没有该用户：资料等名册引入后再落
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

    # 顺序落地：远端顺序重排本地名册，本地独有名字 append。
    # 本地刚排过序（order_changed）时以本地为准：云端已被本次推送改写。
    remote_order = [row.get("username") for row in remote_users
                    if isinstance(row.get("username"), str)]
    if order_changed:
        final_order = roster
    else:
        final_order = ([name for name in remote_order if name in local_roster]
                       + [name for name in local_roster if name not in remote_order]
                       ) if remote_order and local_roster else local_roster
    if final_order and final_order != local_roster:
        if _set_roster(final_order):
            report.reordered = True
            logger.info("[users sync] 名册已按云端顺序重排")
    if final_order and final_order != signature:
        state["roster"] = final_order
        _save_sync_state(state)
    _sync_avatars(token, remote_users, users_dir, report)
    return report


def _sync_avatars(token: str, remote_users: list[dict], users_dir: Path,
                  report: UsersSyncReport) -> None:
    """头像按用户归属增量同步：sha 一致不动，变化上传、缺失下载。

    本地头像文件名在用户资料的 ``avatar`` 字段里；名册行带云端
    ``avatar_sha256``。只处理差异项——19 个头像不重复收发。
    """
    import base64

    from ...constants import AVATARS_DIR

    remote_by_name = {row.get("username"): row for row in remote_users
                      if isinstance(row.get("username"), str)}
    for name in _roster():
        path = users_dir / f"{name}.json"
        try:
            local = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        filename = local.get("avatar")
        if not isinstance(filename, str) or not filename:
            continue                          # 该用户未设置头像
        avatar_path = AVATARS_DIR / filename
        remote_row = remote_by_name.get(name) or {}
        remote_sha = remote_row.get("avatar_sha256")
        if not avatar_path.is_file():
            if not remote_sha:
                continue                      # 双方都没有：无事
            _download_avatar(token, name, avatar_path, report)   # 本地缺：下载
            continue
        local_sha = hashlib.sha256(avatar_path.read_bytes()).hexdigest()
        if local_sha == remote_sha:
            continue                          # 一致：跳过
        try:
            post("/api/sync/users/avatar/put", {
                "username": name, "sha256": local_sha,
                "payload_base64": base64.b64encode(avatar_path.read_bytes()).decode()},
                token)
            report.avatars += 1
        except SyncError as error:
            report.errors.append(f"头像上传失败({name}): {error}")


def _download_avatar(token: str, name: str, avatar_path: Path,
                     report: UsersSyncReport) -> None:
    import base64

    try:
        avatar = post("/api/sync/users/avatar/get", {"username": name}, token).get("avatar")
        if not isinstance(avatar, dict):
            return
        raw = base64.b64decode(avatar["payload_base64"])
        avatar_path.parent.mkdir(parents=True, exist_ok=True)
        avatar_path.write_bytes(raw)
        report.avatars += 1
    except (SyncError, OSError, ValueError) as error:
        report.errors.append(f"头像下载失败({name}): {error}")
