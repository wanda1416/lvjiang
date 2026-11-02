"""一次性迁移：把工作流产出从 config/session/output 收拢到 logs/output。

背景
    0.15.8 及更早，工作流结果 JSON 落在
    ``config/session/output/{用户}/{年-月}/{日}/``；之后改到 ``logs/output/``，
    与任务日志（``logs/daily``）、批量报告（``logs/batch``）、调律说明
    （``logs/tuning``）同根。

    程序本身不需要这个脚本：历史库记录的是相对客户端根目录的路径，新版本写新
    目录、旧记录继续指向旧目录，两边都打得开。本脚本只是把旧文件和旧记录彻底
    搬到新位置，供用户自行决定是否执行，客户端不会自动运行它。

安全性
    - 默认干跑，只打印计划；加 ``--apply`` 才真正移动文件并改写记录。
    - 先移动文件、后改写数据库：中途中断时文件已到位、记录仍指旧目录，重跑一次
      即可收敛，不会出现「记录指向一个空位置」。
    - 目标位置已有同名但内容不同的文件时不覆盖：源文件留在原处，对应记录也不改，
      保证记录指向的文件一定还在原路径。
    - 改写前把涉及的数据库备份到 ``logs/one_off/migrate-output-<时间戳>/``。
    - 客户端的 ``config/session/output`` 目录本身不删，空了之后由用户自行删除。

用法
    python migrate_output_to_logs.py                      # 干跑，只打印计划
    python migrate_output_to_logs.py --apply              # 执行迁移
    python migrate_output_to_logs.py --root D:\\lvjiang     # 指定客户端目录
    python migrate_output_to_logs.py --apply --verbose    # 逐条打印处理明细
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 与 src/lvjiang/constants.py 的 OUTPUT_DIR（新）和旧版 SESSION_CONFIG_DIR/"output"
# 保持同步；脚本不 import lvjiang，便于直接拷到任意客户端目录旁边运行。
OLD_REL = Path("config") / "session" / "output"
NEW_REL = Path("logs") / "output"
OLD_PREFIX = "config/session/output/"
NEW_PREFIX = "logs/output/"
PATH_COLUMN_SUFFIX = "_path"
PREVIEW = 20


@dataclass(frozen=True)
class MovePlan:
    """一个待处理的产出文件。``action`` 为 move / same / conflict。"""

    source: Path
    target: Path
    relative: str
    action: str


@dataclass(frozen=True)
class RowPlan:
    """一条待改写的路径记录。``kind`` 为 moved / missing。"""

    database: Path
    table: str
    column: str
    rowid: int
    old: str
    new: str
    kind: str


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plan_files(old_root: Path, new_root: Path) -> list[MovePlan]:
    if not old_root.is_dir():
        return []
    plans: list[MovePlan] = []
    for source in sorted(p for p in old_root.rglob("*") if p.is_file()):
        relative = source.relative_to(old_root).as_posix()
        target = new_root.joinpath(*relative.split("/"))
        if not target.exists():
            action = "move"
        elif target.is_file() and target.stat().st_size == source.stat().st_size \
                and _digest(target) == _digest(source):
            action = "same"
        else:
            action = "conflict"
        plans.append(MovePlan(source, target, relative, action))
    return plans


def _databases(root: Path) -> list[Path]:
    session = root / "config" / "session"
    if not session.is_dir():
        return []
    return sorted(p for p in session.glob("*.db") if p.is_file())


def _path_columns(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """所有以 ``_path`` 结尾的文本列：result_path / log_path / report_path / markdown_path…"""
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    columns: list[tuple[str, str]] = []
    for table in tables:
        for row in conn.execute(f'PRAGMA table_info("{table}")'):
            name, declared = row[1], (row[2] or "").upper()
            if isinstance(name, str) and name.endswith(PATH_COLUMN_SUFFIX) \
                    and "TEXT" in declared:
                columns.append((table, name))
    return columns


def _rewrite(value: str, old_root: Path) -> str | None:
    """把一条旧产出路径换算成新格式；不属于旧产出的返回 None。"""
    text = value.replace("\\", "/")
    if text.startswith(OLD_PREFIX):
        return NEW_PREFIX + text[len(OLD_PREFIX):]
    path = Path(value)
    if not path.is_absolute():
        return None
    try:
        relative = path.resolve().relative_to(old_root.resolve())
    except (OSError, ValueError):
        return None
    return NEW_PREFIX + relative.as_posix()


def _plan_rows(
    root: Path, old_root: Path, relocated: set[str], kept: set[str],
) -> tuple[list[RowPlan], list[tuple[Path, str, str, str]], list[str]]:
    plans: list[RowPlan] = []
    skipped: list[tuple[Path, str, str, str]] = []
    warnings: list[str] = []
    for database in _databases(root):
        try:
            conn = sqlite3.connect(database, timeout=5)
        except sqlite3.Error as exc:
            warnings.append(f"跳过 {database.name}: {exc}")
            continue
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            for table, column in _path_columns(conn):
                try:
                    rows = conn.execute(
                        f'SELECT rowid, "{column}" FROM "{table}"').fetchall()
                except sqlite3.OperationalError as exc:
                    warnings.append(f"跳过 {database.name}.{table}: {exc}")
                    continue
                for rowid, value in rows:
                    if not isinstance(value, str) or not value:
                        continue
                    new = _rewrite(value, old_root)
                    if new is None or new == value:
                        continue
                    relative = new[len(NEW_PREFIX):]
                    if relative in kept:
                        skipped.append((database, table, column, value))
                        continue
                    kind = "moved" if relative in relocated else "missing"
                    plans.append(RowPlan(database, table, column, rowid, value, new, kind))
        finally:
            conn.close()
    return plans, skipped, warnings


def _plan(root: Path):
    old_root = root / OLD_REL
    new_root = root / NEW_REL
    files = _plan_files(old_root, new_root)
    relocated = {plan.relative for plan in files if plan.action in {"move", "same"}}
    kept = {plan.relative for plan in files if plan.action == "conflict"}
    rows, skipped, warnings = _plan_rows(root, old_root, relocated, kept)
    return files, rows, skipped, warnings


def _print_preview(title: str, lines: list[str], verbose: bool) -> None:
    if not lines:
        return
    print(f"{title}:")
    shown = lines if verbose else lines[:PREVIEW]
    for line in shown:
        print(f"  {line}")
    if len(shown) < len(lines):
        print(f"  ……共 {len(lines)} 项（--verbose 查看全部）")


def _print_plan(root: Path, files: list[MovePlan], rows: list[RowPlan],
                skipped: list[tuple[Path, str, str, str]], verbose: bool) -> None:
    print(f"客户端目录: {root}")
    print(f"旧产出目录: {OLD_REL.as_posix()}"
          f"（{'存在' if (root / OLD_REL).is_dir() else '不存在'}）")
    print(f"新产出目录: {NEW_REL.as_posix()}"
          f"（{'存在' if (root / NEW_REL).is_dir() else '不存在'}）")
    moves = [p for p in files if p.action == "move"]
    sames = [p for p in files if p.action == "same"]
    conflicts = [p for p in files if p.action == "conflict"]
    moved_rows = [r for r in rows if r.kind == "moved"]
    missing_rows = [r for r in rows if r.kind == "missing"]
    print()
    print("文件:")
    print(f"  移动 {len(moves)} 个")
    print(f"  去重 {len(sames)} 个（目标已有同内容文件，删除源文件）")
    print(f"  冲突 {len(conflicts)} 个（目标有不同内容的同名文件，保持原样）")
    print("记录:")
    print(f"  改写 {len(moved_rows)} 条（文件随本次迁移到位）")
    print(f"  改写 {len(missing_rows)} 条（文件迁移前已不存在，只把指针改到新目录）")
    print(f"  跳过 {len(skipped)} 条（对应文件留在旧目录，记录一起保持原样）")
    _print_preview("待移动文件", [p.relative for p in moves], verbose)
    _print_preview("待去重文件", [p.relative for p in sames], verbose)
    _print_preview("冲突文件（不动）", [p.relative for p in conflicts], verbose)
    _print_preview(
        "待改写记录",
        [f"{r.database.name} {r.table}.{r.column}: {r.old} → {r.new}" for r in rows], verbose)
    _print_preview(
        "冲突记录（不动）",
        [f"{db.name} {table}.{column}: {value}" for db, table, column, value in skipped],
        verbose)


def _apply_files(files: list[MovePlan]) -> tuple[int, int]:
    moved = merged = 0
    total = len(files)
    for plan in files:
        if plan.action == "move":
            plan.target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(plan.source), str(plan.target))
            moved += 1
        elif plan.action == "same":
            plan.source.unlink()
            merged += 1
        else:
            continue
        if total > 2000 and (moved + merged) % 500 == 0:
            print(f"  …已处理 {moved + merged}/{total} 个文件")
    return moved, merged


def _apply_rows(rows: list[RowPlan], backup_dir: Path) -> Path:
    databases = sorted({row.database for row in rows})
    backup_dir.mkdir(parents=True, exist_ok=True)
    for database in databases:
        shutil.copy2(database, backup_dir / database.name)
    for database in databases:
        selected = [row for row in rows if row.database == database]
        conn = sqlite3.connect(database, timeout=10)
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                for row in selected:
                    conn.execute(
                        f'UPDATE "{row.table}" SET "{row.column}"=? WHERE rowid=?',
                        (row.new, row.rowid))
        finally:
            conn.close()
    return backup_dir


def _remove_empty_dirs(old_root: Path) -> int:
    if not old_root.is_dir():
        return 0
    removed = 0
    for path in sorted(old_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            removed += 1
    return removed


def _resolve_root(argument: str | None) -> Path:
    if argument:
        return Path(argument).expanduser().resolve()
    override = os.environ.get("LVJIANG_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把工作流产出从 config/session/output 迁移到 logs/output",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="先干跑确认清单，再加 --apply 执行。",
    )
    parser.add_argument("--apply", action="store_true", help="真正移动文件并改写记录")
    parser.add_argument("--root", help="客户端目录（默认脚本所在仓库根，或 $LVJIANG_ROOT）")
    parser.add_argument("--verbose", action="store_true", help="逐条打印所有明细")
    args = parser.parse_args(argv)

    root = _resolve_root(args.root)
    if not (root / "config" / "session").is_dir():
        print(f"不是客户端目录（缺少 config/session）: {root}")
        return 1

    files, rows, skipped, warnings = _plan(root)
    for warning in warnings:
        print(f"警告: {warning}")
    _print_plan(root, files, rows, skipped, args.verbose)

    if not files and not rows:
        print()
        print("没有需要迁移的内容（新版本已经在写 logs/output）")
        return 0
    if not args.apply:
        print()
        print("干跑结束：以上为计划，未做任何修改。确认后加 --apply 执行。")
        return 0

    backup_dir = root / "logs" / "one_off" / f"migrate-output-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        moved, merged = _apply_files(files)
        backup = _apply_rows(rows, backup_dir) if rows else None
    except (OSError, sqlite3.Error) as exc:
        print()
        print(f"迁移中断: {exc}")
        print("请退出律匠（含只读实例）后重跑本脚本：已完成的部分不会重复处理。")
        return 1
    removed = _remove_empty_dirs(root / OLD_REL)

    print()
    print(f"已移动 {moved} 个文件，去重 {merged} 个，清理空目录 {removed} 个")
    if backup is not None:
        print(f"已改写 {len(rows)} 条记录，数据库备份: {backup.relative_to(root).as_posix()}")
    else:
        print("没有需要改写的记录")

    left_files, left_rows, left_skipped, _ = _plan(root)
    remaining = [p for p in left_files if p.action != "conflict"]
    print("复核:")
    print(f"  旧目录剩余待迁移文件 {len(remaining)} 个")
    print(f"  仍指向旧目录的记录 {len(left_rows)} 条")
    if left_skipped:
        print(f"  冲突保留 {len(left_skipped)} 条（文件在旧目录，记录未改，两边一致）")
    missing = [row for row in rows if row.kind == "missing"]
    if missing:
        print(f"  其中 {len(missing)} 条记录的文件在迁移前就已不存在")
    old_root = root / OLD_REL
    if old_root.is_dir() and not any(old_root.iterdir()):
        print(f"  {OLD_REL.as_posix()} 已空，可手工删除")
    return 0 if not remaining and not left_rows else 1


if __name__ == "__main__":
    sys.exit(main())
