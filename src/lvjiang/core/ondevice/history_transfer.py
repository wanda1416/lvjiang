"""手机历史导出 RPC：冻结包与分块读取，不删除运行原件。"""
from __future__ import annotations

import base64
import json
import threading
from uuid import uuid4

from ... import constants
from ..history_transfer import export_history

_LOCK = threading.Lock()
_TOKEN = ""


def request(operation: str, payload: str) -> str:
    global _TOKEN
    from .task_runner import CONTROL_LOCK, is_running

    try:
        data = json.loads(payload)
        with _LOCK:
            directory = constants.PROJECT_ROOT / "config/session/history-transfer"
            archive = directory / "outgoing.zip"
            if operation == "begin":
                with CONTROL_LOCK:
                    if is_running():
                        raise ValueError("请先结束手机任务再回传历史")
                    from .plugins import ensure_loaded
                    ensure_loaded()
                    directory.mkdir(parents=True, exist_ok=True)
                    _TOKEN = ""  # 新导出失败也不能继续读取旧包。
                    summary = export_history(constants.PROJECT_ROOT, archive, data["selected"])
                    _TOKEN = uuid4().hex
                    return json.dumps({"ok": True, "token": _TOKEN, **summary}, ensure_ascii=False)
            if not _TOKEN or data.get("token") != _TOKEN:
                raise ValueError("历史快照已失效，请重新回传")
            if operation == "chunk":
                offset = data["offset"]
                if not isinstance(offset, int) or not 0 <= offset < archive.stat().st_size:
                    raise ValueError("历史分块位置非法")
                with archive.open("rb") as stream:
                    stream.seek(offset)
                    chunk = stream.read(192 << 10)
                return json.dumps({"ok": True, "offset": offset,
                                   "data": base64.b64encode(chunk).decode("ascii")})
            if operation == "end":
                archive.unlink(missing_ok=True)
                _TOKEN = ""
                return json.dumps({"ok": True})
            raise ValueError("未知历史回传操作")
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
