#!/usr/bin/env python3
"""生成签发密钥对（命令行；签发本身用 issuer.py 那个 GUI）

    .venv\\Scripts\\python.exe ops\\license-issuer\\keygen.py     # Windows
    .venv/bin/python ops/license-issuer/keygen.py               # macOS / Linux

一次性操作：私钥写到 ``~/.lvjiang/license_signing_key.txt``，公钥打印出来供粘贴到
``src/lvjiang/core/license/code.py`` 的 ``PUBLIC_KEY_B32``。

**目标文件已存在时直接拒绝，没有 --force 之类的逃生口。** 覆盖私钥会让所有已签发的
激活码一起失效且不可逆；真要轮换就自己把旧文件备份移走，让这一步的分量由人来掂。
这也是它留在命令行、没做成 GUI 按钮的原因。

**不要复用 Android 的 lvjiang.jks。** 那把钥匙丢了或泄漏，你就无法给已安装用户发布
升级包（见 android/README-signing.md）；签发许可要在日常办公机上反复动用私钥，
暴露面完全不同。一把钥匙只干一件事。
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from license_issuer.signing import DEFAULT_KEY_PATH  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成激活码签发密钥对（私钥已存在时拒绝执行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--key", default=str(DEFAULT_KEY_PATH),
        help=f"私钥落点（默认 {DEFAULT_KEY_PATH}）")
    args = parser.parse_args()

    path = Path(args.key)
    if path.exists():
        print(f"[!] 私钥已存在，拒绝覆盖: {path}\n"
              f"    覆盖会让所有已签发的激活码失效，且无法恢复。\n"
              f"    确实要换钥匙：先手动备份并移走这个文件，再重新执行。",
              file=sys.stderr)
        return 1

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    def b32(raw: bytes) -> str:
        return base64.b32encode(raw).decode("ascii").rstrip("=")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(b32(raw_private) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows 上无所谓

    print(f"私钥已写入: {path}")
    print("  ⚠ 备份它。丢了就得换公钥重新发版，所有已签发的码一起失效。")
    print("  ⚠ 不要入库，不要进发行包。")
    print()
    print("把下面这行填进 src/lvjiang/core/license/code.py 的 PUBLIC_KEY_B32：")
    print()
    print(f'PUBLIC_KEY_B32 = "{b32(raw_public)}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
