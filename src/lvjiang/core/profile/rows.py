"""Profile 记录的数据结构。

以前 entries / history / 同步 op 全都用 dict 在层与层之间传，字段名只存在于
字符串里：改一列名（``id → local_id``、删 ``origin_local_id``）编译器与静态
检查完全看不见，只能在运行时炸成 ``KeyError``/``AttributeError``。
这里把它们固定成 dataclass：**边界只转换一次**（SQLite 元组 ↔ dataclass、
JSON ↔ dataclass），中间一律按属性访问。
"""

from __future__ import annotations

from dataclasses import dataclass

#: ``profile_entries`` 的 SELECT 列顺序；与 :meth:`Entry.from_row` 一一对应。
#: 读条目时一律引用这个常量，别处再抄一遍字面量就会在加列时静默错位。
ENTRY_COLUMNS = (
    "username, type, key, value, value_text, event_at, created_at, op_id, locked"
)


@dataclass(slots=True)
class Entry:
    """``profile_entries`` 的一行：某个用户的某个键的当前值。"""

    username: str
    type: str
    key: str
    value: float
    value_text: str = ""
    event_at: str = ""
    created_at: str = ""
    op_id: str = ""
    locked: bool = False

    @classmethod
    def from_row(cls, row) -> "Entry":
        """列顺序见 :data:`ENTRY_COLUMNS`。"""
        return cls(
            username=str(row[0]), type=str(row[1]), key=str(row[2]),
            value=float(row[3] or 0), value_text=str(row[4] or ""),
            event_at=str(row[5] or ""), created_at=str(row[6] or ""),
            op_id=str(row[7] or ""), locked=bool(row[8]),
        )


@dataclass(slots=True)
class HistoryRow:
    """``profile_history`` 的一行账本记录。"""

    local_id: int
    username: str
    type: str
    key: str
    old_value: float | None
    new_value: float | None
    op_type: str
    event_at: str
    source: str = ""
    old_value_text: str = ""
    value_text: str = ""
    delta_value: float | None = None
    sync_from: str = ""
    instance_id: str = ""
    op_id: str = ""
    remote_id: int | None = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row) -> "HistoryRow":
        """列顺序见 :data:`.history_sql.HISTORY_COLUMNS`。"""
        return cls(
            local_id=int(row[0]), event_at=str(row[1] or ""),
            username=str(row[2]), type=str(row[3]), key=str(row[4]),
            old_value=row[5], new_value=row[6],
            old_value_text=str(row[7] or ""), value_text=str(row[8] or ""),
            op_type=str(row[9] or ""), delta_value=row[10],
            source=str(row[11] or ""), sync_from=str(row[12] or ""),
            instance_id=str(row[13] or ""), op_id=str(row[14] or ""),
            remote_id=row[15], created_at=str(row[16] or "") if len(row) > 16 else "",
        )


@dataclass(slots=True)
class PendingOp:
    """本机待上传的一条操作（``remote_id IS NULL`` 的账本行）。"""

    local_id: int
    op_id: str
    username: str
    type: str
    key: str
    value: float
    value_text: str = ""
    op_type: str = "set"
    event_at: str = ""
    source: str = ""
    old_value_text: str = ""
    sync_from: str = ""

    @classmethod
    def from_row(cls, row) -> "PendingOp":
        return cls(
            local_id=int(row[0]), op_id=str(row[1] or ""),
            username=str(row[2]), type=str(row[3]), key=str(row[4]),
            value=float(row[5] or 0), value_text=str(row[6] or ""),
            op_type=str(row[7] or "set"), event_at=str(row[8] or ""),
            source=str(row[9] or ""), old_value_text=str(row[10] or ""),
            sync_from=str(row[11] or ""),
        )

    def payload(self, happened_at: str) -> dict:
        """上行载荷（字段名与云端协议一致）。"""
        return {
            "op_id": self.op_id,
            "local_id": self.local_id,
            "username": self.username,
            "type": self.type,
            "key": self.key,
            "op": self.op_type,
            "value": self.value,
            "value_text": self.value_text,
            "source": self.source,
            "old_value_text": self.old_value_text,
            "sync_from": self.sync_from,
            "happened_at": happened_at,
        }


@dataclass(slots=True)
class RemoteOp:
    """云端 pull 下发的一条操作。"""

    remote_id: int | None
    instance_id: str
    local_id: int | None
    username: str
    type: str
    key: str
    op: str
    value: float | None
    delta: float | None = None
    value_text: str = ""
    source: str = ""
    happened_at: str = ""
    old_value: float | None = None
    op_id: str = ""
    old_value_text: str = ""
    sync_from: str = ""

    @classmethod
    def from_json(cls, raw: dict) -> "RemoteOp":
        def _float(name: str) -> float | None:
            value = raw.get(name)
            return float(value) if isinstance(value, (int, float)) else None

        def _int(name: str) -> int | None:
            value = raw.get(name)
            return int(value) if isinstance(value, int) else None

        return cls(
            remote_id=_int("remote_id"),
            instance_id=str(raw.get("instance_id") or ""),
            local_id=_int("local_id"),
            username=str(raw.get("username") or ""),
            type=str(raw.get("type") or ""),
            key=str(raw.get("key") or ""),
            op=str(raw.get("op") or "set"),
            value=_float("value"),
            delta=_float("delta"),
            value_text=str(raw.get("value_text") or ""),
            source=str(raw.get("source") or ""),
            happened_at=str(raw.get("happened_at") or ""),
            old_value=_float("old_value"),
            op_id=str(raw.get("op_id") or ""),
            old_value_text=str(raw.get("old_value_text") or ""),
            sync_from=str(raw.get("sync_from") or ""),
        )


@dataclass(slots=True)
class SnapshotSummary:
    """云端快照清单的一项。"""

    snapshot_id: int
    watermark_remote_id: int
    entry_count: int = 0
    device_name: str = ""
    created_at: str = ""

    @classmethod
    def from_json(cls, raw: dict) -> "SnapshotSummary":
        return cls(
            snapshot_id=int(raw.get("id") or 0),
            watermark_remote_id=int(raw.get("snapshot_remote_id") or 0),
            entry_count=int(raw.get("entry_count") or 0),
            device_name=str(raw.get("origin_name") or ""),
            created_at=str(raw.get("created_at") or ""),
        )
