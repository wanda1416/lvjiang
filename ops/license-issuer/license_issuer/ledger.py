"""签发台账（SQLite）

离线授权撤不掉一张已经发出去的码，唯一的把手是**下次不续**——前提是你知道那张码
是谁的、什么时候发的、什么时候到期。所以每次签发都落一条记录。

库落在 ``~/.lvjiang/license_ledger.db``，和私钥同目录、同在仓库之外：它记录的是
「哪张码发给了谁」，属于签发者的私人资产，不该进版本库。

存了码本身（``code`` 字段）：用户把码弄丢时可以直接重发，不必重签——重签会多一个
编号，台账就对不上了。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

#: 与私钥同目录：都是签发者的本地资产，不进版本库
DEFAULT_LEDGER_PATH = Path.home() / ".lvjiang" / "license_ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS issued (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code_id     TEXT    NOT NULL,
    bind        TEXT    NOT NULL,         -- serial / none
    serial      TEXT,                     -- 绑机码的目标序列号
    levels      TEXT    NOT NULL,         -- 逗号分隔的等级名
    expires     TEXT,                     -- ISO 日期；NULL 表示永久
    issued_at   TEXT    NOT NULL,         -- ISO 时间戳
    note        TEXT    NOT NULL DEFAULT '',
    code        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issued_code_id ON issued(code_id);
"""


@dataclass(frozen=True)
class Record:
    """一条签发记录"""

    row_id: int
    code_id: str
    bind: str
    serial: str | None
    levels: tuple[str, ...]
    expires: date | None
    issued_at: str
    note: str
    code: str

    @property
    def is_bound(self) -> bool:
        return self.bind == "serial"

    @property
    def expired(self) -> bool:
        return self.expires is not None and self.expires < date.today()

    @property
    def target(self) -> str:
        return self.serial or "免绑定"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def record_issue(
    code_id: str,
    bind: str,
    serial: str | None,
    levels: tuple[str, ...] | list[str],
    expires: date | None,
    code: str,
    note: str = "",
    path: Path | None = None,
) -> int:
    """记一条签发记录，返回行号"""
    with closing(_connect(path or DEFAULT_LEDGER_PATH)) as conn, conn:
        cursor = conn.execute(
            "INSERT INTO issued"
            " (code_id, bind, serial, levels, expires, issued_at, note, code)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                code_id,
                bind,
                serial,
                ",".join(levels),
                expires.isoformat() if expires else None,
                datetime.now().isoformat(timespec="seconds"),
                note,
                code,
            ),
        )
        return int(cursor.lastrowid or 0)


def list_records(limit: int = 200, path: Path | None = None) -> list[Record]:
    """最近的签发记录，新的在前"""
    with closing(_connect(path or DEFAULT_LEDGER_PATH)) as conn:
        rows = conn.execute(
            "SELECT id, code_id, bind, serial, levels, expires, issued_at,"
            " note, code FROM issued ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_to_record(row) for row in rows]


def find_by_code_id(code_id: str, path: Path | None = None) -> list[Record]:
    """按码编号查——用户报编号，你就能查到这张码是谁的"""
    with closing(_connect(path or DEFAULT_LEDGER_PATH)) as conn:
        rows = conn.execute(
            "SELECT id, code_id, bind, serial, levels, expires, issued_at,"
            " note, code FROM issued WHERE code_id = ? ORDER BY id DESC",
            (code_id,),
        ).fetchall()
    return [_to_record(row) for row in rows]


def update_note(row_id: int, note: str, path: Path | None = None) -> None:
    """补一条备注：发给谁，多半是签完之后才想起来写"""
    with closing(_connect(path or DEFAULT_LEDGER_PATH)) as conn, conn:
        conn.execute("UPDATE issued SET note = ? WHERE id = ?", (note, row_id))


def _to_record(row) -> Record:
    expires = date.fromisoformat(row[5]) if row[5] else None
    return Record(
        row_id=int(row[0]),
        code_id=str(row[1]),
        bind=str(row[2]),
        serial=row[3],
        levels=tuple(x for x in str(row[4]).split(",") if x),
        expires=expires,
        issued_at=str(row[6]),
        note=str(row[7] or ""),
        code=str(row[8]),
    )
