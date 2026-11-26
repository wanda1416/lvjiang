"""跨平台本地控制面发现文件（control.json）与权限隔离。

Windows / Linux / macOS 统一使用 127.0.0.1 随机空闲端口 + Bearer Token：
- 发现文件落在 session_directory 下（与 instance 锁同目录），同机多套独立目录互不干扰；
- 写入使用临时文件 + 原子 os.replace()，避免薄壳 CLI 读到半写 JSON；
- POSIX（Linux / macOS）以 0o600 创建，阻止同机其他用户读取 token；
- 删除时校验 token，避免旧进程退出时误删新实例刚写出的 control.json。
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..paths import config_directory, session_directory

CONTROL_FILENAME = "control.json"


@dataclass(frozen=True)
class ControlEndpoint:
    """本地控制面连接元数据。"""

    pid: int
    instance_id: str
    port: int
    token: str
    mode: str = "gui"  # "gui" | "headless"
    started_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> ControlEndpoint | None:
        if not isinstance(data, dict):
            return None
        try:
            pid = int(data["pid"])
            port = int(data["port"])
            instance_id = str(data.get("instance_id") or "").strip()
            token = str(data.get("token") or "").strip()
            mode = str(data.get("mode") or "gui").strip() or "gui"
            started_at = str(data.get("started_at") or "").strip()
        except (KeyError, TypeError, ValueError):
            return None
        if pid <= 0 or port <= 0 or port > 65535 or not token:
            return None
        return cls(
            pid=pid,
            instance_id=instance_id,
            port=port,
            token=token,
            mode=mode,
            started_at=started_at,
        )


def generate_token() -> str:
    """生成 32 字节密码学随机 Bearer Token。"""
    return secrets.token_urlsafe(32)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def control_file_path(root: Path | None = None) -> Path:
    """返回指定项目根目录下控制面发现文件的路径。"""
    if root is None:
        from ...constants import PROJECT_ROOT
        root = PROJECT_ROOT
    return session_directory(config_directory(Path(root))) / CONTROL_FILENAME


def write_control_file(endpoint: ControlEndpoint, path: Path) -> None:
    """原子写入 control.json；POSIX 下强制 0o600 仅当前用户可读。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    payload = (json.dumps(endpoint.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        if os.name != "nt":
            fd = os.open(
                str(tmp_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.chmod(tmp_path, 0o600)
        else:
            with open(tmp_path, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def read_control_file(path: Path) -> ControlEndpoint | None:
    """读取 control.json；不存在或损坏时返回 None。"""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return ControlEndpoint.from_dict(raw)


def remove_control_file(path: Path, *, expected_token: str = "") -> None:
    """清理 control.json；若传入 expected_token，则仅在 token 匹配时删除。"""
    if not path.is_file():
        return
    if expected_token:
        current = read_control_file(path)
        if current is not None and current.token != expected_token:
            return
    try:
        path.unlink()
    except OSError:
        pass
