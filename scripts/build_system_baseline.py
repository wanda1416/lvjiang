"""复制统一官方预置并建立可信基底；不读取 local/session。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvjiang._version import __version__  # noqa: E402
from lvjiang.core.system_preset import write_baseline_identity  # noqa: E402
from lvjiang.core.update_bundle import read_tree  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    system = args.destination / "system"
    if system.exists() and any(system.iterdir()):
        raise ValueError("请使用空的打包目标，不能覆盖用户 system")
    for name, data in read_tree(ROOT / "config/system").items():
        path = system / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    identity = write_baseline_identity(system, __version__)
    print(f"预置基底 {identity['app_series']}: {identity['sha256']}")


if __name__ == "__main__":
    main()
