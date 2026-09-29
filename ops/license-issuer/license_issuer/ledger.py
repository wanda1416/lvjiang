"""签发台账（SQLite）

离线授权撤不掉一张已经发出去的码，唯一的把手是**下次不续**——前提是你知道那张码
是谁的、什么时候发的、什么时候到期。所以每次签发都落一条记录。

库落在 ``~/.lvjiang/license_ledger.db``，和私钥同目录、同在仓库之外：它记录的是
「哪张码发给了谁」，属于签发者的私人资产，不该进版本库。

**流水号 seq 才是编号的真身**：绑机码与免绑定码共用同一条序列，所以数字全局唯一，
不会出现「B0003 和 F0003 是两张不同的码」这种查之前还得先问是哪一类的情况。展示用
的 ``code_id`` 只是 ``前缀 + 流水号`` 的渲染，前缀让你一眼看出这张码要不要序列号。

存了码本身（``code`` 字段）：用户把码弄丢时可以直接重发，不必重签——重签会多一个
流水号，台账就对不上了。
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
    seq         INTEGER NOT NULL,         -- 签发流水号，两类码共用一条序列
    code_id     TEXT    NOT NULL,         -- 展示编号 = 前缀 + 流水号
    bind        TEXT    NOT NULL,         -- serial / none
    serial      TEXT,                     -- 绑机码的目标序列号
    levels      TEXT    NOT NULL,         -- 逗号分隔的等级名
    expires     TEXT,                     -- ISO 日期；NULL 表示永久
    issued_at   TEXT    NOT NULL,         -- ISO 时间戳
    note        TEXT    NOT NULL DEFAULT '',
    code        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issued_seq ON issued(seq);
CREATE INDEX IF NOT EXISTS idx_issued_code_id ON issued(code_id);
"""

#: 编号前缀：一眼看出这张码是哪一种，B 要序列号、F 不要
PREFIX_BY_BIND = {"serial": "B", "none": "F"}

#: 位数只补不截，B9999 之后自然进位到 B10000
_CODE_ID_WIDTH = 4


@dataclass(frozen=True)
class Record:
    """一条签发记录"""

    row_id: int
    seq: int
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


def make_code_id(bind: str, seq: int) -> str:
    """``(绑定方式, 流水号)`` → 展示编号，如 ``B0001`` / ``F0002``"""
    return f"{PREFIX_BY_BIND.get(bind, 'X')}{seq:0{_CODE_ID_WIDTH}d}"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def next_seq(path: Path | None = None) -> int:
    """下一个签发流水号；空台账从 1 开始。

    取最大值而不是记录条数：删过行之后条数会和流水号对不上，而编号一旦重复，
    「用户报编号查去向」这件事就失效了——台账的价值正在于此。
    """
    with closing(_connect(path or DEFAULT_LEDGER_PATH)) as conn:
        row = conn.execute("SELECT MAX(seq) FROM issued").fetchone()
    return int(row[0]) + 1 if row and row[0] is not None else 1


def next_code_id(bind: str, path: Path | None = None) -> str:
    """下一个可用的展示编号"""
    return make_code_id(bind, next_seq(path))


def record_issue(
    seq: int,
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
            " (seq, code_id, bind, serial, levels, expires, issued_at,"
            "  note, code)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                seq,
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


_SELECT = (
    "SELECT id, seq, code_id, bind, serial, levels, expires, issued_at,"
    " note, code FROM issued"
)


def list_records(limit: int = 200, path: Path | None = None) -> list[Record]:
    """最近的签发记录，新的在前"""
    with closing(_connect(path or DEFAULT_LEDGER_PATH)) as conn:
        rows = conn.execute(
            f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_to_record(row) for row in rows]


def find_by_code_id(code_id: str, path: Path | None = None) -> list[Record]:
    """按码编号查——用户报编号，你就能查到这张码是谁的"""
    with closing(_connect(path or DEFAULT_LEDGER_PATH)) as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE code_id = ? ORDER BY id DESC",
            (code_id.strip(),)).fetchall()
    return [_to_record(row) for row in rows]


def code_id_exists(code_id: str, path: Path | None = None) -> bool:
    """该编号是否已经用过——重复编号会让台账查不准"""
    return bool(find_by_code_id(code_id, path))


def update_note(row_id: int, note: str, path: Path | None = None) -> None:
    """补一条备注：发给谁，多半是签完之后才想起来写"""
    with closing(_connect(path or DEFAULT_LEDGER_PATH)) as conn, conn:
        conn.execute("UPDATE issued SET note = ? WHERE id = ?", (note, row_id))


def _to_record(row) -> Record:
    return Record(
        row_id=int(row[0]),
        seq=int(row[1]),
        code_id=str(row[2]),
        bind=str(row[3]),
        serial=row[4],
        levels=tuple(x for x in str(row[5]).split(",") if x),
        expires=date.fromisoformat(row[6]) if row[6] else None,
        issued_at=str(row[7]),
        note=str(row[8] or ""),
        code=str(row[9]),
    )
