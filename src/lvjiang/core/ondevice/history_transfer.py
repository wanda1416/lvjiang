"""手机历史导出 RPC：冻结包与分块读取，不删除运行原件。

导出只取终态记录（任务与批次均要求已结束），因此任务运行中也可以回传：
运行中的任务与未结束批次由 ``export_history`` 的查询自行跳过，结束后再回传
即可补齐。只读连接与 WAL 写库可以并发，这里不拿 ``CONTROL_LOCK``，避免导出
期间阻塞启动、下发或批量保存。

冻结包只活在一次回传里：``begin`` 在项目根下建一个隐藏临时暂存目录写 zip，
``chunk`` 分块读它，``end``（或下一次 ``begin``、导出失败）整目录删除。
不在 ``config/session`` 下留收发目录——那里没有它的读取方。
"""
from __future__ import annotations

import base64
import json
import shutil
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

from ... import constants
from ..history.transfer import export_history

_LOCK = threading.Lock()
_TOKEN = ""
_STAGE: Path | None = None


def _discard_stage() -> None:
    """删掉本次导出的暂存目录（幂等）；手机历史与附件原件不在其中。"""
    global _STAGE
    if _STAGE is not None:
        shutil.rmtree(_STAGE, ignore_errors=True)
        _STAGE = None


def request(operation: str, payload: str) -> str:
    global _TOKEN, _STAGE

    try:
        data = json.loads(payload)
        with _LOCK:
            if operation == "begin":
                from .plugins import ensure_loaded
                ensure_loaded()
                _TOKEN = ""  # 新导出失败也不能继续读取旧包。
                _discard_stage()
                # 暂存放在项目根下的隐藏目录，与 .offline-stage- 同口径：安卓端没有
                # 可靠的系统 temp，而项目根一定可写；导出结束即整目录删除。
                _STAGE = Path(tempfile.mkdtemp(prefix=".history-stage-", dir=constants.PROJECT_ROOT))
                try:
                    summary = export_history(
                        constants.PROJECT_ROOT, _STAGE / "outgoing.zip", data["selected"])
                except Exception:
                    _discard_stage()
                    raise
                _TOKEN = uuid4().hex
                return json.dumps({"ok": True, "token": _TOKEN, **summary}, ensure_ascii=False)
            if not _TOKEN or data.get("token") != _TOKEN:
                raise ValueError("历史快照已失效，请重新回传")
            archive = _STAGE / "outgoing.zip" if _STAGE is not None else None
            if archive is None or not archive.is_file():
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
                _discard_stage()
                _TOKEN = ""
                return json.dumps({"ok": True})
            raise ValueError("未知历史回传操作")
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
