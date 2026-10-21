"""复制统一官方预置并建立可信基底；不读取 local/session。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvjiang._version import __version__  # noqa: E402
from lvjiang.core.paths import get_config_system_dir  # noqa: E402
from lvjiang.core.preset_sources import (  # noqa: E402
    excluded_files,
    format_excluded,
    normalize_tree,
    present_files,
    tracked_files,
)
from lvjiang.core.system_preset import write_baseline_identity  # noqa: E402
from lvjiang.core.update_bundle import read_tree  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    system = get_config_system_dir(args.destination)
    if system.exists() and any(system.iterdir()):
        raise ValueError("请使用空的打包目标，不能覆盖用户 system")
    source = get_config_system_dir()
    include = tracked_files(ROOT, "config/system")
    if include is None:
        print("非 git 工作区：按磁盘内容建立预置基底，未启用跟踪文件过滤")
        files = read_tree(source)
    else:
        message = format_excluded(
            "config/system", excluded_files(present_files(source), include))
        if message:
            print(message)
        files = read_tree(source, only=include)
        missing = sorted(set(include) - files.keys())
        if missing:
            raise ValueError(f"官方预置缺少文件: {missing[0]}")
    for name, data in normalize_tree(files).items():
        path = system / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    identity = write_baseline_identity(system, __version__, config_dir=args.destination)
    print(f"预置基底 {identity['app_series']}: {identity['sha256']}")


if __name__ == "__main__":
    main()
