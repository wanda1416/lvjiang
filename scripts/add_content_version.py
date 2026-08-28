#!/usr/bin/env python3
"""给参与 remote 下发的实体配置文件补 ``content_version`` 顶层字段。

哪些目录参与由 `lvjiang.core.config.versioning` 的注册表决定（core 声明
scenes/layouts，插件经 config_policy_modules 声明自己的，如燕云的
yysls/tuning_rules），本脚本不自己维护一份清单——两处清单迟早会不一致。

**幂等**：已有 ``content_version`` 的文件原样跳过，所以新增了配置文件之后
可以直接重跑一遍补齐。用 ``--check`` 只报告不写盘（CI 用）。

    python scripts/add_content_version.py            # 补齐
    python scripts/add_content_version.py --check    # 只检查

写盘时逐文件保持原有行尾（``config/system/layouts/桌面布局/*.json`` 是
CRLF，其余是 LF）与末尾换行的有无——否则会把整批文件刷成一个巨大的
无意义 diff（master 的 740c6a6 就是这么来的）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from lvjiang.apps import load_config_policies  # noqa: E402
from lvjiang.core.config.versioning import (  # noqa: E402
    CONTENT_VERSION_KEY,
    iter_versioned_files,
)

# 插件私有目录（燕云 yysls/tuning_rules）是插件自己注册的，不 import 就不在
# 注册表里，会被这个脚本静默漏掉
load_config_policies()

_INITIAL_VERSION = 1


def _split_eol(text: str) -> tuple[str, bool]:
    """返回 (行尾符, 是否以换行结尾)"""
    eol = "\r\n" if "\r\n" in text else "\n"
    return eol, text.endswith(("\n", "\r"))


def _add_to_yaml(text: str) -> str | None:
    """YAML：在顶部插一行。不走 yaml.load/dump——那会重排键、丢注释、
    把整个文件刷成一个无意义的大 diff。顶层是 mapping 时插一行合法。"""
    eol, _ = _split_eol(text)
    return f"{CONTENT_VERSION_KEY}: {_INITIAL_VERSION}{eol}{text}"


def _add_to_json(text: str) -> str | None:
    """JSON：重建 dict 把 content_version 放在最前，再按原格式序列化。"""
    data = json.loads(text)
    if not isinstance(data, dict):
        return None
    eol, trailing = _split_eol(text)
    merged = {CONTENT_VERSION_KEY: _INITIAL_VERSION, **data}
    out = json.dumps(merged, ensure_ascii=False, indent=2)
    if trailing:
        out += "\n"
    return out.replace("\n", eol) if eol != "\n" else out


def _has_version(path: Path, text: str) -> bool:
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
        return isinstance(data, dict) and CONTENT_VERSION_KEY in data
    # YAML：只看顶层是否已有该键，不整份解析（同样为了不碰格式）
    return any(line.startswith(f"{CONTENT_VERSION_KEY}:")
               for line in text.splitlines())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="只报告缺失，不写盘；有缺失时退出码 1（CI 用）")
    args = parser.parse_args()

    missing: list[Path] = []
    written: list[Path] = []
    for path in iter_versioned_files(_REPO_ROOT / "config" / "system"):
        text = path.read_bytes().decode("utf-8")  # newline= 是 3.13+
        if _has_version(path, text):
            continue
        missing.append(path)
        if args.check:
            continue
        out = _add_to_json(text) if path.suffix == ".json" else _add_to_yaml(text)
        if out is None:
            print(f"跳过（顶层不是 mapping）: {path}", file=sys.stderr)
            continue
        path.write_text(out, encoding="utf-8", newline="")
        written.append(path)

    if args.check:
        if missing:
            print(f"以下 {len(missing)} 个文件缺 {CONTENT_VERSION_KEY}，"
                  f"跑 `python scripts/add_content_version.py` 补齐：",
                  file=sys.stderr)
            for path in missing:
                print(f"  {path.relative_to(_REPO_ROOT)}", file=sys.stderr)
            return 1
        print(f"全部文件均带 {CONTENT_VERSION_KEY}", file=sys.stderr)
        return 0

    print(f"补齐 {len(written)} 个文件的 {CONTENT_VERSION_KEY}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
