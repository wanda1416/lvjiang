"""一次性迁移：把旧的全局匹配阈值搬进每个自动剧情配置文件。

背景：0.15.7 及更早，自动剧情的匹配阈值存在 ``tools/auto_plot/_settings.json``
（全局一份）；0.15.8 起阈值随文件走（每个 ``<文件>.json`` 自带
``match_threshold``），``_settings.json`` 不再是产品的一部分。

本脚本把旧的全局值写进**所有缺该字段的文件**，然后提示可以删掉
``_settings.json``。已经自带阈值的文件一律不动。

迁移属于一次性动作，按仓库约定放在 ``scripts/one_off/``，不进 ``tests/``。

用法：
    python scripts/one_off/migrate_story_threshold.py            # 干跑，只打印
    python scripts/one_off/migrate_story_threshold.py --apply    # 真正写入
    python scripts/one_off/migrate_story_threshold.py --apply --delete-legacy
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_REL = Path("tools") / "auto_plot"
_LEGACY = "_settings.json"
_DEFAULT_THRESHOLD = 0.7


def _roots() -> list[Path]:
    return [_ROOT / "config" / layer / _REL for layer in ("local", "system")]


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  跳过 {path}: {exc}")
        return None
    return data if isinstance(data, dict) else None


def _legacy_threshold(roots: list[Path]) -> tuple[float, Path | None]:
    """旧的全局阈值：local 优先，其次 system；都没有就用默认值。"""
    for root in roots:
        path = root / _LEGACY
        if path.exists():
            data = _read(path) or {}
            value = data.get("match_threshold")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value), path
            return _DEFAULT_THRESHOLD, path
    return _DEFAULT_THRESHOLD, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正写入文件")
    parser.add_argument(
        "--delete-legacy", action="store_true",
        help="迁移完成后删除旧的 _settings.json（需同时给 --apply）")
    args = parser.parse_args()

    roots = _roots()
    threshold, legacy_path = _legacy_threshold(roots)
    print(f"旧全局阈值: {threshold}（来源: {legacy_path or '默认值'}）")

    changed = 0
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.json")):
            if path.name.startswith("_"):
                continue
            data = _read(path)
            if data is None or "match_threshold" in data:
                continue
            print(f"  迁移 {path.relative_to(_ROOT)} → match_threshold={threshold}")
            changed += 1
            if args.apply:
                data["match_threshold"] = threshold
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")

    if not changed:
        print("没有需要迁移的文件（都已自带 match_threshold）")
    elif not args.apply:
        print(f"干跑结束：{changed} 个文件待迁移，加 --apply 才会写入")

    if args.apply and args.delete_legacy and legacy_path is not None:
        legacy_path.unlink()
        print(f"已删除 {legacy_path.relative_to(_ROOT)}")
    elif legacy_path is not None:
        print(f"提示：迁移完成后可删除 {legacy_path.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
