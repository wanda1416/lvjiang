"""为 Gradle 构建官方预置配置；仅依赖标准库和 core.system_preset。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvjiang.core.system_preset import build_system_preset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    manifest = build_system_preset(ROOT / "config/system", args.destination)
    print(f"官方预置包：{len(manifest['files'])} 个文件，内容摘要 {manifest['preset_id']}")


if __name__ == "__main__":
    main()
