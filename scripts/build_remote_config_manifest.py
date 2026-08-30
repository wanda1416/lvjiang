#!/usr/bin/env python3
"""从一个目录生成在线配置的 ``config.json`` manifest（发布端用）。

手写 manifest 必然出错——sha256 要算、``content_version`` 要跟文件里写的
对上，漏一步客户端就整份拒绝（`core/config/remote.py` 会校验这两项）。
所以这里从文件本身**推导**出来，作者只需要摆好目录：

    lvjiang.github.io/lvjiang/config/
      config.json                       ← 本脚本生成
      remote/
        scenes/xxx.yaml
        layouts/桌面布局/xxx.json
        yysls/tuning_rules/xxx.yaml

用法：

    python scripts/build_remote_config_manifest.py <发布目录> \\
        --base-url https://wanda1416.github.io/lvjiang/config \\
        --config-version 12 \\
        [--min-app-version 0.7.0] [--max-app-version-exclusive 0.9.0]

``--config-version`` 必须**单调递增**（客户端靠它和 ETag 判断要不要重拉）。
中文目录名会自动 percent-encode 进 url——客户端不自己拼路径，不同平台的
编码行为不一致。

**撤回**：manifest 是全量声明，从发布目录里删掉某个文件重新生成即可，
客户端下次同步会删掉本地副本。把 content_version 改小是**没用的**
（闸门要求远程严格大于 system），必须从 manifest 移除。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from lvjiang.apps import load_config_policies  # noqa: E402
from lvjiang.core.config import versioning  # noqa: E402
from lvjiang.core.config.remote import (  # noqa: E402
    REMOTE_CONFIG_SCHEMA_VERSION,
    is_safe_rel_path,
)

# 插件私有目录（燕云 yysls/tuning_rules）由插件自己注册，不 import 就会漏
load_config_policies()


def build_manifest(root: Path, *, base_url: str, config_version: int,
                   updated_at: str, min_app_version: str = "",
                   max_app_version_exclusive: str = "") -> dict:
    """扫描 root/remote/ 下所有参与下发的文件，生成 manifest dict。"""
    remote_root = root / "remote"
    if not remote_root.is_dir():
        raise SystemExit(f"发布目录里没有 remote/ 子目录: {remote_root}")

    files: list[dict] = []
    for path in sorted(remote_root.rglob("*")):
        if not path.is_file() or path.name.startswith("_"):
            continue
        rel_path = path.relative_to(remote_root).as_posix()
        if not is_safe_rel_path(rel_path):
            print(f"跳过（未参与在线下发的路径）: {rel_path}", file=sys.stderr)
            continue

        payload = path.read_bytes()
        version = versioning.version_from_text(
            payload.decode("utf-8"), path.suffix)
        if version is None:
            print(f"跳过（缺 content_version）: {rel_path}", file=sys.stderr)
            continue

        entry = {
            "rel_path": rel_path,
            # 逐段编码：中文目录名必须 percent-encode，斜杠要保留
            "url": f"{base_url.rstrip('/')}/remote/" + "/".join(
                quote(part, safe="") for part in rel_path.split("/")),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "content_version": version,
        }
        if min_app_version:
            entry["min_app_version"] = min_app_version
        if max_app_version_exclusive:
            entry["max_app_version_exclusive"] = max_app_version_exclusive
        files.append(entry)

    return {
        "schema_version": REMOTE_CONFIG_SCHEMA_VERSION,
        "config_version": config_version,
        "updated_at": updated_at,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="发布目录（其下应有 remote/）")
    parser.add_argument("--base-url", required=True,
                        help="config.json 所在的 URL 前缀")
    parser.add_argument("--config-version", type=int, required=True,
                        help="必须单调递增")
    parser.add_argument("--updated-at", default="",
                        help="ISO 时间戳，默认留空")
    parser.add_argument("--min-app-version", default="",
                        help="给全部条目统一加客户端版本下限")
    parser.add_argument("--max-app-version-exclusive", default="",
                        help="给全部条目统一加客户端版本上限（不含）")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="输出路径，默认 <root>/config.json")
    args = parser.parse_args()

    manifest = build_manifest(
        args.root,
        base_url=args.base_url,
        config_version=args.config_version,
        updated_at=args.updated_at,
        min_app_version=args.min_app_version,
        max_app_version_exclusive=args.max_app_version_exclusive,
    )
    out = args.output or (args.root / "config.json")
    out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"写入 {out}：{len(manifest['files'])} 个文件，"
          f"config_version={args.config_version}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
