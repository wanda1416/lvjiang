#!/usr/bin/env python
"""补齐统一心法配置中已登记心法的一至六重骨架。

名册以 inner_way.yaml 为唯一来源，内置名册及满重基线来自 DIY 计算器。
不再维护另一份社区名称列表；新心法通过属性配置界面新增。
仅为已有 group 补齐缺失重数，不改已填数据，不重新引入旧名称。

用法::

    .venv/bin/python scripts/gen_attr_model_entries.py
    .venv/bin/python scripts/gen_attr_model_entries.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "config" / "system" / "yysls" / "attr_model"

#: 心法重数。游戏里固定六重，不随等级变化。
TIERS = ("一重", "二重", "三重", "四重", "五重", "六重")


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _merge(path: Path, kind: str, wanted: list[str], *, dry_run: bool) -> int:
    data = _load(path)
    if data.get("kind") not in (None, kind):
        raise SystemExit(f"{path.name} 的 kind 是 {data.get('kind')}，期望 {kind}")
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    added = [name for name in wanted if name not in entries]
    if not added:
        return 0
    if dry_run:
        print(f"  {path.name}: 将新增 {len(added)} 条，例如 {added[:3]}")
        return len(added)
    for name in added:
        group, tier = name.rsplit("·", 1)
        entries[name] = {"modeled": False, "group": group, "tier": TIERS.index(tier) + 1}
    data["kind"] = kind
    data["entries"] = entries
    header = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    ) if path.exists() else ""
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)
    path.write_text(f"{header}\n{body}" if header else body, encoding="utf-8")
    print(f"  {path.name}: 新增 {len(added)} 条")
    return len(added)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    args = parser.parse_args()

    path = TARGET_DIR / "inner_way.yaml"
    entries = _load(path).get("entries") or {}
    names = dict.fromkeys(entry["group"] for entry in entries.values() if entry.get("group"))
    inner_way_ids = [f"{name}·{tier}" for name in names for tier in TIERS]
    print(f"目标目录：{TARGET_DIR}")
    total = _merge(
        TARGET_DIR / "inner_way.yaml", "inner_way", inner_way_ids,
        dry_run=args.dry_run,
    )
    if not total:
        print("没有缺失条目")
    return 0


if __name__ == "__main__":
    sys.exit(main())
