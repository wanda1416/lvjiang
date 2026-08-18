"""Migrate legacy equipped/bag_items/mock_items into *.loadouts.json.

The legacy file is intentionally left untouched for rollback. Existing non-empty
loadout files are skipped unless --force is supplied. Every overwritten target
gets a timestamped backup.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SLOTS = (
    "main_weapon", "sub_weapon", "ring", "pendant",
    "head", "chest", "leg", "wrist",
)
SLOT_TYPES = {
    "ring": "环", "pendant": "佩", "head": "冠胄", "chest": "胸甲",
    "leg": "胫甲", "wrist": "腕甲",
}


def _read_json(path: Path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.stem}_", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _fingerprint(equip: dict, fallback: str = "") -> str:
    fp = str(equip.get("_fp") or fallback)
    if fp:
        return fp
    from lvjiang.apps.yysls.core.equip_parser.models import make_fingerprint
    return make_fingerprint(
        equip, is_mock=bool(equip.get("_extra", {}).get("is_mock")))


def _put(items: dict[str, dict], equip: dict, fallback: str = "") -> str:
    value = copy.deepcopy(equip)
    fp = _fingerprint(value, fallback)
    if not fp:
        raise ValueError(f"装备缺少有效指纹: {value.get('name', '未知装备')}")
    value["_fp"] = fp
    previous = items.get(fp)
    if previous is not None and previous != value:
        raise ValueError(f"指纹 {fp} 对应两份不同装备数据，已停止以避免覆盖")
    items[fp] = value
    return fp


def _school_arts(root: Path, username: str) -> tuple[str, str]:
    settings = _read_json(root / "config/session/session.json", {}) or {}
    selection = (
        settings.get("settings", settings)
        .get("combat_attrs_selections", {})
        .get(username, {})
    )
    school = selection.get("school") if isinstance(selection, dict) else ""
    if not school:
        return "", ""
    import yaml
    config = yaml.safe_load(
        (root / "config/system/yysls/game_config.yaml").read_text(encoding="utf-8"))
    school_config = (config.get("schools") or {}).get(school, {})
    return (
        str((school_config.get("main") or {}).get("martial_art") or ""),
        str((school_config.get("sub") or {}).get("martial_art") or ""),
    )


def build_loadout(legacy: dict, username: str, root: Path) -> dict:
    items: dict[str, dict] = {}
    for node in ("bag_items", "mock_items"):
        groups = legacy.get(node, {})
        if not isinstance(groups, dict):
            continue
        for group in groups.values():
            if not isinstance(group, dict):
                continue
            for key, equip in group.items():
                if isinstance(equip, dict):
                    _put(items, equip, str(key))

    slots = {slot: None for slot in SLOTS}
    equipped = legacy.get("equipped", {})
    if isinstance(equipped, dict):
        for slot in SLOTS:
            equip = equipped.get(slot)
            if isinstance(equip, dict) and equip:
                value = copy.deepcopy(equip)
                if not value.get("type") and slot in SLOT_TYPES:
                    value["type"] = SLOT_TYPES[slot]
                slots[slot] = _put(items, value)

    main_art, sub_art = _school_arts(root, username)
    plan_id = uuid4().hex
    return {
        "revision": 1,
        "active_plan_id": plan_id,
        "plans": {
            plan_id: {
                "name": "默认方案",
                "main_martial_art": main_art,
                "sub_martial_art": sub_art,
                "equipment": slots,
            }
        },
        "equipment_items": items,
    }


def _target_is_empty(data: dict | None) -> bool:
    if not data:
        return True
    return not data.get("equipment_items") and all(
        not any((plan.get("equipment") or {}).values())
        for plan in (data.get("plans") or {}).values()
        if isinstance(plan, dict)
    )


def migrate_user(source: Path, root: Path, *, dry_run: bool, force: bool) -> str:
    legacy = _read_json(source, {}) or {}
    if not any(legacy.get(key) for key in ("equipped", "bag_items", "mock_items")):
        return "skip:no-legacy-data"
    target = source.with_name(f"{source.stem}.loadouts.json")
    existing = _read_json(target, None)
    if existing is not None and not _target_is_empty(existing) and not force:
        return "skip:target-not-empty"
    migrated = build_loadout(legacy, source.stem, root)
    if dry_run:
        return f"dry-run:{len(migrated['equipment_items'])}"
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(target, target.with_suffix(f".json.bak-{stamp}"))
    _atomic_json(target, migrated)
    return f"migrated:{len(migrated['equipment_items'])}"


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移旧装备数据到多备战方案")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--users-dir", type=Path)
    parser.add_argument("--user", action="append", help="仅迁移指定用户，可重复")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="覆盖非空目标（自动备份）")
    args = parser.parse_args()
    root = args.root.resolve()
    users_dir = (args.users_dir or root / "config/session/users").resolve()
    selected = set(args.user or [])
    sources = sorted(
        path for path in users_dir.glob("*.json")
        if not path.name.endswith(".loadouts.json")
        and (not selected or path.stem in selected)
    )
    failed = False
    for source in sources:
        try:
            result = migrate_user(
                source, root, dry_run=args.dry_run, force=args.force)
            print(f"{source.stem}: {result}")
        except Exception as exc:
            failed = True
            print(f"{source.stem}: error:{exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
