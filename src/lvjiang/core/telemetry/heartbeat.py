"""通道 A：心跳事件的 schema、payload 组装、节流。

字段全部来自公开的运行环境信息，不涉及任何燕云领域词汇——这是 core 层
本就该管的部分，与调律事件（apps/yysls/telemetry）分层对称。
"""
from __future__ import annotations

import platform
import re
from datetime import date, datetime, timezone
from typing import Any

from .registry import register_schema
from .schema import EventSchema, FieldSpec, ListSpec

_VERSION_PATTERN = r"^[0-9A-Za-z.\-+]{1,32}$"
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_UUID_HEX_PATTERN = r"^[0-9a-f]{32}$"
_OS_RELEASE_PATTERN = r"^[0-9A-Za-z._-]{1,16}$"
_ARCH_PATTERN = r"^[A-Za-z0-9_]{1,20}$"

HEARTBEAT_SCHEMA = EventSchema(
    name="heartbeat", version=2,
    fields=(
        FieldSpec("install_id", str, pattern=_UUID_HEX_PATTERN, example="0" * 32),
        FieldSpec("first_seen", str, pattern=_DATE_PATTERN, example="2026-01-01"),
        FieldSpec("day", str, pattern=_DATE_PATTERN, example="2026-01-01"),
        FieldSpec("app_version", str, pattern=_VERSION_PATTERN, example="0.7.0"),
        FieldSpec("run_env", str, choices=("desktop", "android")),
        FieldSpec("os_name", str, choices=("Windows", "Darwin", "Linux", "other")),
        FieldSpec("os_release", str, pattern=_OS_RELEASE_PATTERN, required=False,
                  example="11"),
        FieldSpec("arch", str, pattern=_ARCH_PATTERN, example="amd64"),
        FieldSpec("ui_language", str, pattern=r"^[a-z]{2}_[A-Z]{2}$", example="zh_CN"),
        ListSpec(
            "apps", max_items=16,
            item_fields=(FieldSpec(
                "id", str, pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$",
                example="yysls",
            ),),
        ),
    ),
)
register_schema(HEARTBEAT_SCHEMA)


def _os_release_major() -> str:
    """只取大版本号，绝不透传 build 号（build 号+arch+lang+版本组合起来
    可识别性显著上升）。"""
    raw = platform.release() or ""
    token = re.split(r"[.\-]", raw)[0][:16]
    return token or "unknown"


def _registered_apps() -> list[dict[str, str]]:
    """只上报框架登记的稳定 ID，不读取模块路径或用户文本。"""
    from ...apps import get_registered_app_ids
    ids = get_registered_app_ids()
    return [{"id": app_id} for app_id in ids] or [{"id": "none"}]


def normalized_app_version() -> str:
    """规整后的版本号，不满足 ``_VERSION_PATTERN`` 时回退 ``"unknown"``。

    信封（reporter._envelope）也要带这个值：心跳每 UTC 日只发一次，同一天
    第 2..N 次上报没有心跳可取，服务端就只能把批次记成 unknown。
    """
    from ..update import get_version

    version = get_version()
    if not re.fullmatch(_VERSION_PATTERN, version or ""):
        return "unknown"
    return version


def build_heartbeat_payload(*, install_id: str, first_seen: str) -> dict:
    from ...i18n import current_language
    from ..config.session import load_env

    os_name = platform.system() or "other"
    if os_name not in ("Windows", "Darwin", "Linux"):
        os_name = "other"

    version = normalized_app_version()

    run_env = load_env()
    if run_env not in ("desktop", "android"):
        run_env = "desktop"

    payload: dict[str, Any] = {
        "install_id": install_id,
        "first_seen": first_seen,
        "day": date.today().isoformat(),
        "app_version": version,
        "run_env": run_env,
        "os_name": os_name,
        "os_release": _os_release_major(),
        "arch": (platform.machine() or "unknown")[:20],
        "ui_language": current_language(),
        "apps": _registered_apps(),
    }
    return payload


# ─── 节流：每 UTC 日最多一次 ─────────────────────────────────

def _state() -> dict:
    from ..config.session import get_session_store
    server = get_session_store().get_node("server_config", {})
    if not isinstance(server, dict):
        return {}
    node = server.get("telemetry")
    return node if isinstance(node, dict) else {}


def should_send_heartbeat() -> bool:
    """今天（UTC）是否还没成功上报过，且上次失败距今超过 1 小时。"""
    state = _state()
    today = date.today().isoformat()
    if state.get("last_report_date") == today:
        return False
    last_attempt = state.get("last_attempt_at")
    if isinstance(last_attempt, str):
        try:
            dt = datetime.fromisoformat(last_attempt)
            if (datetime.now(timezone.utc) - dt).total_seconds() < 3600:
                return False
        except ValueError:
            pass
    return True


def mark_attempt(*, success: bool) -> None:
    """必须在主线程调用（写 SessionStore）。``last_report_date`` 只在
    成功后写——失败的一天不能算已上报。"""
    from ..config.session import get_session_store

    def _merge(existing):
        existing = existing if isinstance(existing, dict) else {}
        node = dict(existing.get("telemetry") or {})
        node["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
        if success:
            node["last_report_date"] = date.today().isoformat()
        existing["telemetry"] = node
        return existing

    get_session_store().mutate_node("server_config", _merge)
