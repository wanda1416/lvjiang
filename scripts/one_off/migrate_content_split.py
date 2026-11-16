"""一次性迁移：把仓库 config/{system,local} 的 scenes.yaml / layouts.yaml
拆分为目录化结构（分组即目录 + _meta.json）。

用法：.venv\\Scripts\\python.exe scripts\\one_off\\migrate_content_split.py [--dry-run]

dry-run 只检查并打印将要发生的移动/新建，不写盘。迁移成功后
config/system 与 config/local 下不应再存在 scenes.yaml / layouts.yaml。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lvjiang.core.config.content_split import needs_migration  # noqa: E402


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    config = PROJECT_ROOT / "config"
    targets = [config / "system", config / "local"]
    pending = [root for root in targets if needs_migration(root)]
    if not pending:
        print("无需迁移：聚合文件不存在")
        return 0
    if dry_run:
        for root in pending:
            print(f"[dry-run] 将迁移: {root}")
        return 0
    from lvjiang.core.config.content_split import migrate_layer
    for root in pending:
        migrate_layer(root)
        print(f"已迁移: {root}")
    leftover = [
        path for root in targets
        for path in (root / "scenes.yaml", root / "layouts.yaml")
        if path.exists()
    ]
    if leftover:
        print(f"迁移后仍残留聚合文件: {leftover}")
        return 1
    print("迁移完成：system/local 下已无 scenes.yaml 与 layouts.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
