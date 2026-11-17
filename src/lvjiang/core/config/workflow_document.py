"""任务参数的唯一来源规则与两文件升级提交；路径显式，打包读取不做迁移。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..fs_util import atomic_write_bytes, locked_paths
from ..json_files import formatted_json_bytes


@dataclass
class WorkflowSource:
    session_path: Path
    workflow_path: Path
    originals: dict[Path, bytes | None]
    session: dict[str, Any]
    document: dict[str, Any] | None

    @property
    def params(self) -> dict | None:
        return (
            None
            if self.document is None
            else deepcopy(self.document.get("workflow_params", {}))
        )


def _object(raw: bytes | None, path: Path) -> dict:
    if raw is None:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 顶层必须为对象，已停止参数覆盖")
    return data


def validate_workflow_document(raw: bytes, path: Path) -> dict:
    document = _object(raw, path)
    params = document.get("workflow_params", {})
    if not isinstance(params, dict) or any(not isinstance(value, dict) for value in params.values()):
        raise ValueError("任务参数必须为按 wf_id 登记的对象，已停止参数覆盖")
    return document


def read_workflow_source(session_path: Path, workflow_path: Path) -> WorkflowSource:
    originals = {
        p: p.read_bytes() if p.exists() else None for p in (session_path, workflow_path)
    }
    session = _object(originals[session_path], session_path)
    if originals[workflow_path] is not None:
        document = validate_workflow_document(originals[workflow_path] or b"{}", workflow_path)
        params = document.get("workflow_params", {})
    elif "wf_configs" in session:
        params = session["wf_configs"]
        document = {"workflow_params": deepcopy(params)}
    else:
        document, params = None, {}
    if not isinstance(params, dict) or any(
        not isinstance(value, dict) for value in params.values()
    ):
        raise ValueError("任务参数必须为按 wf_id 登记的对象，已停止参数覆盖")
    return WorkflowSource(session_path, workflow_path, originals, session, document)


def _current(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def commit_workflow_state(
    source: WorkflowSource, document: dict | None, session: dict | None = None
) -> None:
    """调用者持有两路径锁；先保存原字节，再写新文档，最后删旧节点。

    失败仅还原仍等于本次输出的文件；后续编辑不覆盖。原件留在本机供核对恢复。
    None 文档表示包内缺文档，不等于显式空文档。
    """
    writes: dict[Path, bytes] = {}
    if document is not None:
        writes[source.workflow_path] = formatted_json_bytes(document)
        if session is None and "wf_configs" in source.session:
            session = source.session
    if session is not None:
        session = deepcopy(session)
        if document is not None:
            session.pop("wf_configs", None)
        elif "wf_configs" in source.session:
            session["wf_configs"] = deepcopy(source.session["wf_configs"])
        writes[source.session_path] = formatted_json_bytes(session)
    # 相同值保留原字节；所有源都验证，避免覆盖快照之后的编辑。
    writes = {
        p: raw
        for p, raw in writes.items()
        if source.originals[p] is None
        or json.loads(source.originals[p] or b"{}") != json.loads(raw)
    }
    changed = [path for path, old in source.originals.items() if _current(path) != old]
    if not writes and not changed:
        return
    for path, old in source.originals.items():
        if old is not None:
            archive = (
                source.session_path.parent
                / ".migrations/workflow"
                / (hashlib.sha256(old).hexdigest() + "-" + path.name)
            )
            if not archive.exists():
                atomic_write_bytes(archive, old, prefix=".workflow-backup-")
            elif archive.read_bytes() != old:
                raise RuntimeError("任务参数原件存档冲突，停止写入")
    if changed:
        raise RuntimeError(f"{changed[0].name} 已在读取后修改；旧快照已留档，未覆盖后续编辑")
    written: dict[Path, bytes] = {}
    try:
        for path, raw in writes.items():
            if _current(path) != source.originals[path]:
                raise RuntimeError(f"{path.name} 已在读取后修改，请重试")
            atomic_write_bytes(path, raw, prefix=".workflow-state-")
            written[path] = raw
        if any(_current(path) != raw for path, raw in written.items()):
            raise RuntimeError("参数提交期间发生编辑，保留编辑及原件，请重试")
    except BaseException:
        for path, raw in reversed(list(written.items())):
            if _current(path) != raw:
                continue
            old = source.originals[path]
            if old is None:
                path.unlink()
            else:
                atomic_write_bytes(path, old, prefix=".workflow-restore-")
        raise


def migrate_workflow_document(session_path: Path, workflow_path: Path) -> None:
    with locked_paths(session_path, workflow_path):
        source = read_workflow_source(session_path, workflow_path)
        if "wf_configs" in source.session:
            commit_workflow_state(source, source.document)
