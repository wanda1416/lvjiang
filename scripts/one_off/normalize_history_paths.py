"""一次性规范化：把历史库里的反斜杠路径统一成正斜杠。

背景
    历史记录里的路径早期用 ``str(path.relative_to(PROJECT_ROOT))`` 生成，Windows 下
    天然是反斜杠（``logs\\daily\\用户\\2026-09\\02\\xxx.log``）；2026-10-14 起生成端
    改成 ``.as_posix()``，新记录都是正斜杠。存量记录因此两种写法混在一起：

    - Windows 上两种分隔符 ``Path`` 都认，本机打开没问题；
    - 但回传包是跨端协议，``core.history_transfer`` 明确拒绝含 ``\\`` 的名字，
      手机端也按 posix 解析——这些行一旦进入回传范围就会让整包失败。

    本脚本只改 ``config/session/*.db`` 里以 ``_path`` 结尾的文本列，把路径中的
    ``\\`` 换成 ``/``。**不动任何文件**，也不改目录结构。

安全性
    - 默认干跑，只打印计划；加 ``--apply`` 才写库。
    - 改写前把涉及的数据库备份到 ``logs/one_off/normalize-paths-<时间戳>/``。
    - 只做分隔符替换，不改变路径指向：相对路径仍是相对路径，绝对路径仍是绝对路径。
    - UNC 路径（``\\\\server\\share``）跳过：Windows 不认正斜杠形式的 UNC。
    - 可重复执行：第二次跑只会报「没有需要规范化的记录」。

用法
    python normalize_history_paths.py                 # 干跑，只打印计划
    python normalize_history_paths.py --apply         # 执行
    python normalize_history_paths.py --root D:\\lvjiang
    python normalize_history_paths.py --apply --verbose
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PATH_COLUMN_SUFFIX = "_path"
BACKSLASH = "\\"
UNC_PREFIX = "\\\\"
PREVIEW = 20


@dataclass(frozen=True)
class RowPlan:
    """一条待规范化的记录。``kind`` 为 relative / absolute。"""

    database: Path
    table: str
    column: str
    rowid: int
    old: str
    new: str
    kind: str

    def describe(self) -> str:
        return f"{self.database.name} {self.table}.{self.column}: {self.old} → {self.new}"


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


def _plan_rows(root: Path) -> tuple[list[RowPlan], list[str], list[str]]:
    """返回 (待改记录, 跳过的 UNC 样例, 告警)。"""
    plans: list[RowPlan] = []
    unc: list[str] = []
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
                    if not isinstance(value, str) or not value or BACKSLASH not in value:
                        continue
                    if value.startswith(UNC_PREFIX):
                        unc.append(f"{database.name} {table}.{column}: {value}")
                        continue
                    kind = "absolute" if Path(value).is_absolute() else "relative"
                    plans.append(RowPlan(database, table, column, rowid, value,
                                         value.replace(BACKSLASH, "/"), kind))
        finally:
            conn.close()
    return plans, unc, warnings


def _print_plan(root: Path, plans: list[RowPlan], unc: list[str], verbose: bool) -> None:
    print(f"客户端目录: {root}")
    session = root / "config" / "session"
    print(f"数据库目录: {session.relative_to(root).as_posix()}")
    if not plans:
        print()
        print("没有需要规范化的记录（路径已全是正斜杠）")
        if unc:
            print(f"UNC 路径跳过 {len(unc)} 条（Windows 不认正斜杠形式的 UNC）:")
            for line in unc[:PREVIEW]:
                print(f"  {line}")
        return
    groups: dict[tuple[str, str], list[RowPlan]] = {}
    for plan in plans:
        groups.setdefault((plan.database.name, plan.column), []).append(plan)
    print()
    print("待规范化记录（按 库.列 统计）:")
    for (database, column), items in sorted(groups.items()):
        relatives = sum(1 for item in items if item.kind == "relative")
        absolutes = len(items) - relatives
        detail = f"相对 {relatives} 条" + (f"，绝对 {absolutes} 条" if absolutes else "")
        print(f"  {database}.{column}: {len(items)} 条（{detail}）")
    print(f"合计 {len(plans)} 条")
    if unc:
        print(f"UNC 路径跳过 {len(unc)} 条（Windows 不认正斜杠形式的 UNC）")
        for line in unc[:PREVIEW]:
            print(f"  {line}")
    shown = plans if verbose else plans[:PREVIEW]
    print("样例:" if not verbose else "明细:")
    for plan in shown:
        print(f"  {plan.describe()}")
    if len(shown) < len(plans):
        print(f"  ……共 {len(plans)} 条（--verbose 查看全部）")


def _apply(root: Path, plans: list[RowPlan]) -> Path:
    databases = sorted({plan.database for plan in plans})
    backup_dir = root / "logs" / "one_off" / f"normalize-paths-{datetime.now():%Y%m%d-%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for database in databases:
        source = sqlite3.connect(database, timeout=10)
        target = sqlite3.connect(backup_dir / database.name)
        try:
            with target:
                source.backup(target)
        finally:
            source.close()
            target.close()
    for database in databases:
        selected = [plan for plan in plans if plan.database == database]
        conn = sqlite3.connect(database, timeout=10)
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                for plan in selected:
                    conn.execute(
                        f'UPDATE "{plan.table}" SET "{plan.column}"=? WHERE rowid=?',
                        (plan.new, plan.rowid))
        finally:
            conn.close()
    return backup_dir


def _resolve_root(argument: str | None) -> Path:
    if argument:
        return Path(argument).expanduser().resolve()
    override = os.environ.get("LVJIANG_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把历史库里反斜杠写法的路径统一成正斜杠（只改库，不动文件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="先干跑确认清单，再加 --apply 执行。",
    )
    parser.add_argument("--apply", action="store_true", help="真正写库")
    parser.add_argument("--root", help="客户端目录（默认脚本所在仓库根，或 $LVJIANG_ROOT）")
    parser.add_argument("--verbose", action="store_true", help="逐条打印所有明细")
    args = parser.parse_args(argv)

    root = _resolve_root(args.root)
    if not (root / "config" / "session").is_dir():
        print(f"不是客户端目录（缺少 config/session）: {root}")
        return 1

    plans, unc, warnings = _plan_rows(root)
    for warning in warnings:
        print(f"警告: {warning}")
    _print_plan(root, plans, unc, args.verbose)
    if not plans:
        return 0
    if not args.apply:
        print()
        print("干跑结束：以上为计划，未做任何修改。确认后加 --apply 执行。")
        return 0

    try:
        backup = _apply(root, plans)
    except (OSError, sqlite3.Error) as exc:
        print()
        print(f"规范化中断: {exc}")
        print("请退出律匠（含只读实例）后重跑本脚本：已完成的部分不会重复处理。")
        return 1

    print()
    print(f"已规范化 {len(plans)} 条记录，数据库备份: {backup.relative_to(root).as_posix()}")
    left, left_unc, _ = _plan_rows(root)
    print("复核:")
    print(f"  仍含反斜杠的记录 {len(left)} 条")
    if left_unc:
        print(f"  UNC 路径保持原样 {len(left_unc)} 条")
    for name in sorted({plan.database.name for plan in plans}):
        database = root / "config" / "session" / name
        try:
            with sqlite3.connect(database) as conn:
                print(f"  {name} integrity_check: "
                      f"{conn.execute('PRAGMA integrity_check').fetchone()[0]}")
        except sqlite3.Error as exc:
            print(f"  {name} 校验失败: {exc}")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
