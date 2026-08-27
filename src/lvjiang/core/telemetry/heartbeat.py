"""通道 A：心跳事件的 schema、payload 组装、节流。

字段全部来自公开的运行环境信息，不含任何游戏内数据——这是 core 层本就
该管的部分，与调律事件（apps/yysls/telemetry）分层对称。

唯一的例外是 ``plugin`` 的取值白名单里出现了具体插件名。取值本身来自
``AppHooks.id``（框架登记的稳定 ID，core 不猜也不认识它的含义），白名单
是隐私护栏而非领域耦合：放行自由文本会让自定义插件名成为指纹维度。
详见该字段处的说明。
"""
from __future__ import annotations

import platform
import re
from datetime import date, datetime, timezone
from typing import Any

from .registry import register_schema
from .schema import EventSchema, FieldSpec

_VERSION_PATTERN = r"^[0-9A-Za-z.\-+]{1,32}$"
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_UUID_HEX_PATTERN = r"^[0-9a-f]{32}$"
_OS_RELEASE_PATTERN = r"^[0-9A-Za-z._-]{1,16}$"
_ARCH_PATTERN = r"^[A-Za-z0-9_]{1,20}$"

HEARTBEAT_SCHEMA = EventSchema(
    name="heartbeat", version=1,
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
        # 取值来自 AppHooks.id（框架登记的稳定 ID），不是模块路径或用户文本。
        #
        # **保持这个字段名与单值形状**：服务端 ops/stats-worker 的 PLUGIN 枚举
        # 校验这个键，改名或改成列表会让整条心跳被 validateHeartbeat() 判空
        # 丢弃——而 worker 仍返回 200、客户端据此按成功记账且每 UTC 日只发
        # 一次，于是 DAU/WAU/留存全部静默归零且事后无法补报。字段形状是跨
        # 组件契约，要动必须连 worker 校验、D1 schema、stats-client 镜像一起
        # 动，见 tests/core/test_heartbeat_worker_contract.py。
        #
        # 白名单是隐私护栏：放行自由文本会让自定义插件名成为指纹维度。
        # 新增插件时**必须同步扩充这里与 worker 的 PLUGIN 两处**，否则拒收。
        FieldSpec("plugin", str, choices=("yysls", "none")),
    ),
)
register_schema(HEARTBEAT_SCHEMA)


def _os_release_major() -> str:
    """只取大版本号，绝不透传 build 号（build 号+arch+lang+版本组合起来
    可识别性显著上升）。"""
    raw = platform.release() or ""
    token = re.split(r"[.\-]", raw)[0][:16]
    return token or "unknown"


def _detect_plugin() -> str:
    """已装配插件的稳定 ID；未装配任何插件时为 "none"。

    读 ``AppHooks.id``（框架登记的稳定标识），**不再靠 grep 模块路径里的
    ``".yysls."`` 猜**——那种探测方式随插件增加就失效，这是本次解耦要保住
    的成果。

    多插件同时装配时只取第一个：字段是单值契约（理由见 HEARTBEAT_SCHEMA
    里的说明），真要上报多个得先改服务端。目前只有一个插件，取第一个与
    历史行为完全一致。
    """
    from ...apps import get_registered_app_ids
    ids = get_registered_app_ids()
    return ids[0] if ids else "none"


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
        "plugin": _detect_plugin(),
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
