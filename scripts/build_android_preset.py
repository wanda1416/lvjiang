"""为 Gradle 构建官方预置配置；宿主需标准库、packaging 和 core.system_preset。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from preset_sources import report_excluded, tracked_files  # noqa: E402

from lvjiang.core.paths import get_config_system_dir  # noqa: E402
from lvjiang.core.system_preset import build_system_preset  # noqa: E402
from lvjiang.core.update_bundle import read_tree  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    system = get_config_system_dir()
    include = tracked_files(ROOT, "config/system")
    if include is None:
        print("非 git 工作区：按磁盘内容打包预置，未启用跟踪文件过滤")
    else:
        report_excluded("config/system", list(read_tree(system)), include)
    manifest = build_system_preset(system, args.destination, include=include)
    print(f"官方预置包：{len(manifest['files'])} 个文件，内容摘要 {manifest['preset_id']}")


if __name__ == "__main__":
    main()
