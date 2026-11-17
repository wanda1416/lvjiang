"""核对错误旧迁移后，显式选择一份 local 坐标；默认只显示，不落盘。"""

import argparse
from pathlib import Path

from lvjiang.core.config.content_split import repair_misplaced_coordinate
from lvjiang.core.config.resolver import ConfigResolver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--source", required=True, help="local 内原坐标相对路径")
    parser.add_argument("--target", required=True, help="local 内正确分组的相对路径")
    parser.add_argument(
        "--apply", action="store_true", help="确认来源后执行；不同目标内容绝不覆盖"
    )
    args = parser.parse_args()
    print(f"local: {args.source} -> {args.target}")
    if args.apply:
        repair_misplaced_coordinate(
            ConfigResolver(config_dir=args.config_dir, dev_mode=False),
            args.source,
            args.target,
        )
    else:
        print("未修改；核对原件与目标后，添加 --apply 执行。原字节将保存在迁移记录中。")


if __name__ == "__main__":
    main()
