"""校验 YAML 文件是否为 canonical（`save_yaml()` 输出）形式。

背景：`config/session/profile.yaml` 是运行期可编辑的用户定义。应用内只有一条
序列化路径（``core/profile/schema.py:save_profile_config`` → ``core/config:save_yaml``，
PyYAML + ``sort_keys=False`` + ``default_flow_style=False``），但该文件还会被
「云端定义下行」与「离线包安装」以原始字节整体覆盖。一旦外部工具用了别的
序列化器（js-yaml / ruamel / 编辑器格式化会把 block sequence 缩进），文件就会
在两种排版之间来回翻，每次本地保存都产生整篇 diff——内容没变，diff 却有两千行。

本脚本只做一件事：确认磁盘上的字节等于 `load_yaml()` 再 `save_yaml()` 的结果。
纯 YAML round-trip，**不经过 profile schema**，因此未注册周期、未知节点等
「核心看不懂但必须留着」的条目会原样保留；它也绝不删除或改写任何定义。

目录不存在时跳过（CI 不检出 `config/session`），不会把缺失当失败。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from lvjiang.core.config import dump_yaml, load_yaml  # noqa: E402


def canonical_bytes(path: Path) -> bytes:
    """返回 `path` 经 `load_yaml` → `dump_yaml` 之后应有的字节。"""
    return dump_yaml(load_yaml(path)).encode("utf-8")


def _iter_yaml(target: Path):
    if target.is_file():
        yield target
        return
    if not target.is_dir():
        return
    for path in sorted(target.rglob("*.yaml")):
        if path.is_file():
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 YAML 是否为 canonical 形式")
    parser.add_argument("paths", nargs="*", default=["config/session"],
                        help="要检查的文件或目录（默认 config/session）")
    parser.add_argument("--write", action="store_true",
                        help="按 canonical 形式原地改写不一致的文件（只改排版，不动定义）")
    args = parser.parse_args(argv)

    checked = 0
    offenders: list[Path] = []
    skipped: list[Path] = []
    for raw in args.paths:
        target = Path(raw)
        if not target.exists():
            skipped.append(target)
            continue
        for path in _iter_yaml(target):
            original = path.read_bytes()
            if not original.strip():
                continue
            checked += 1
            if original == canonical_bytes(path):
                continue
            offenders.append(path)

    for path in offenders:
        if args.write:
            path.write_bytes(canonical_bytes(path))
            print(f"已规范化 {path}")
        else:
            print(f"排版不是 canonical 形式 {path}")

    for path in skipped:
        print(f"跳过（不存在）{path}")
    print(f"检查 {checked} 个 YAML 文件，问题 {0 if args.write else len(offenders)} 处")
    if args.write:
        return 0
    return 1 if offenders else 0


if __name__ == "__main__":
    raise SystemExit(main())
