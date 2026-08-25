"""匿名安装标识（install_id）的生成、读取、重置、清除。

不含机器指纹的硬约束（本模块唯一职责，测试兜底）：
只允许 ``uuid.uuid4()``。**禁止** ``uuid.uuid1()``（含 MAC 地址）、
``uuid.getnode()``、``platform.node()``、``socket.gethostname()``、
``getpass.getuser()``、任何路径 hash——这些都会让"匿名 ID"退化成设备指纹。

存放位置见 :mod:`lvjiang.core.telemetry.paths` 的模块 docstring：
``config/local/telemetry/identity.json``，绝不能是 ``config/session/``。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger

from ..fs_util import atomic_write_text
from .paths import identity_path, telemetry_dir


@dataclass(frozen=True)
class Identity:
    install_id: str
    first_seen: str  # "YYYY-MM-DD"，UTC，仅到天——精确到秒是白送的指纹位
    dropped_events: int = 0  # 本地缓冲超限时累计丢弃的事件数，随下批信封上报


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _generate() -> Identity:
    return Identity(install_id=uuid.uuid4().hex, first_seen=_today_utc())


def _write(identity: Identity) -> None:
    path = identity_path()
    payload = json.dumps(
        {"install_id": identity.install_id, "first_seen": identity.first_seen,
         "dropped_events": identity.dropped_events},
        ensure_ascii=False)
    atomic_write_text(path, payload, prefix=".identity_")


def _read() -> Identity | None:
    path = identity_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 损坏文件按缺失处理，不阻断启动
        logger.warning(f"[telemetry] identity.json 解析失败，重新生成: {e}")
        return None
    install_id = data.get("install_id")
    first_seen = data.get("first_seen")
    if not isinstance(install_id, str) or not isinstance(first_seen, str):
        return None
    if len(install_id) != 32:
        return None
    dropped = data.get("dropped_events", 0)
    dropped = dropped if isinstance(dropped, int) and not isinstance(dropped, bool) else 0
    return Identity(install_id=install_id, first_seen=first_seen, dropped_events=dropped)


def get_identity() -> Identity:
    """读取本机 install_id；缺失或损坏则生成一份新的并落盘。"""
    existing = _read()
    if existing is not None:
        return existing
    identity = _generate()
    _write(identity)
    logger.info("[telemetry] 已生成新的匿名安装标识")
    return identity


def reset_identity() -> Identity:
    """换一个新 install_id，且清空本地缓冲——旧 ID 缓冲的事件不能被
    新 ID 的批次带出去。"""
    from . import spool
    identity = _generate()
    _write(identity)
    spool.purge()
    logger.info("[telemetry] 匿名安装标识已重置")
    return identity


def purge_identity() -> None:
    """删除本地整个 telemetry 目录（identity + 缓冲），不留任何标识。"""
    import shutil
    d = telemetry_dir()
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def bump_dropped_events(n: int) -> None:
    """本地缓冲超限丢弃事件后累加计数，随下一批信封上报。"""
    current = _read()
    if current is None:
        return  # 未同意/无标识时不产生任何本地状态
    updated = Identity(
        install_id=current.install_id, first_seen=current.first_seen,
        dropped_events=current.dropped_events + n)
    _write(updated)


def take_and_reset_dropped_events() -> int:
    """取出并清零 dropped_events，供上报信封使用（成功发送后才清零）。"""
    current = _read()
    if current is None or current.dropped_events == 0:
        return 0
    _write(Identity(install_id=current.install_id, first_seen=current.first_seen,
                     dropped_events=0))
    return current.dropped_events
