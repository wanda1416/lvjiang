#!/usr/bin/env python3
"""激活码签发工具（签发者本地用，私钥永不入库）

    # 一次性：生成签发密钥对
    python scripts/issue_license.py keygen

    # 绑机码：用户报来序列号，签一张只在他那台机器生效的
    python scripts/issue_license.py issue --id L0042 --serial ABCDE-FGHJK-MNPQR-STVWX-Y \\
        --feature bg_capture

    # 免绑定码：不看序列号，拿到即可用。发给特别用户，建议一律带有效期
    python scripts/issue_license.py issue --id L0043 --no-bind \\
        --feature bg_capture --expires 2027-01-01

    # 自检：把刚签出来的码验一遍
    python scripts/issue_license.py verify --code LVJ1.xxx.yyy

**不要复用 Android 的 lvjiang.jks。** 那把钥匙丢了或泄漏，你就无法给已安装用户发布
升级包（见 android/README-signing.md）。签发许可要在日常办公机上反复动用私钥，
暴露面完全不同——密钥分离，一把钥匙只干一件事。
"""
from __future__ import annotations

import argparse
import base64
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from lvjiang.core.license.code import (  # noqa: E402
    build_payload,
    format_code,
    verify_code,
)

#: 私钥默认落点：**仓库之外**的用户目录。
#: 不放 config/local——那是被跟踪的开发版配置仓库，密钥搁进去迟早会被误提交。
DEFAULT_KEY_PATH = Path.home() / ".lvjiang" / "license_signing_key.txt"


def _b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _unb32(text: str) -> bytes:
    return base64.b32decode(text + "=" * (-len(text) % 8), casefold=True)


def cmd_keygen(args: argparse.Namespace) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    path = Path(args.key)
    if path.exists() and not args.force:
        print(f"[!] 私钥已存在: {path}\n    覆盖会让所有已签发的激活码失效。"
              f"确实要换钥匙请加 --force", file=sys.stderr)
        return 1

    private = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    raw_private = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_b32(raw_private) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows 上无所谓

    print(f"私钥已写入: {path}")
    print("  ⚠ 备份它。丢了就得换公钥重新发版，所有已签发的码一起失效。")
    print("  ⚠ 不要入库，不要进发行包。")
    print()
    print("把下面这行公钥填进 src/lvjiang/core/license/code.py 的 PUBLIC_KEY_B32：")
    print()
    print(f'PUBLIC_KEY_B32 = "{_b32(raw_public)}"')
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    path = Path(args.key)
    if not path.is_file():
        print(f"[!] 找不到私钥: {path}\n    先跑一次 keygen", file=sys.stderr)
        return 1
    private = Ed25519PrivateKey.from_private_bytes(
        _unb32(path.read_text(encoding="utf-8").strip()))

    bind = "none" if args.no_bind else "serial"
    if bind == "serial" and not args.serial:
        print("[!] 绑机码需要 --serial；要发免绑定码请加 --no-bind", file=sys.stderr)
        return 1
    if bind == "serial":
        from lvjiang.core.license.hardware import (
            normalize_serial,
            serial_is_wellformed,
        )
        if not serial_is_wellformed(args.serial):
            print(f"[!] 序列号校验位不对: {args.serial}\n"
                  f"    多半是抄错了一位，让用户重新复制一次", file=sys.stderr)
            return 1
        serial = normalize_serial(args.serial)
    else:
        serial = None
        if not args.expires:
            print("[提示] 免绑定码没有设有效期。它是谁拿到谁能用，"
                  "离线方案又没有吊销手段——建议加 --expires。")

    expires = date.fromisoformat(args.expires) if args.expires else None
    payload = build_payload(
        code_id=args.id,
        bind=bind,
        features=args.feature,
        serial=serial,
        expires=expires,
    )
    code = format_code(payload, private.sign(payload))

    print(f"编号   : {args.id}")
    print(f"绑定   : {'免绑定（谁拿到谁能用）' if bind == 'none' else serial}")
    print(f"功能   : {'、'.join(args.feature)}")
    print(f"有效期 : {expires or '永久'}")
    print(f"长度   : {len(code)} 字符")
    print()
    print(code)
    print()
    print("[记账] 建议把「编号 → 发给谁 → 日期」记一笔：离线撤不掉，"
          "但下次可以不续。")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from lvjiang.core.license.code import PUBLIC_KEY_B32, LicenseError

    key = args.public_key or PUBLIC_KEY_B32
    if not key:
        print("[!] 代码里还没填公钥，也没传 --public-key", file=sys.stderr)
        return 1
    try:
        license_ = verify_code(args.code, key)
    except LicenseError as exc:
        print(f"[✗] {exc}", file=sys.stderr)
        return 1
    print("[✓] 签名有效")
    print(f"编号   : {license_.code_id}")
    print(f"绑定   : {license_.serial if license_.is_bound else '免绑定'}")
    print(f"功能   : {'、'.join(license_.features) or '无'}")
    print(f"有效期 : {license_.expires or '永久'}"
          + ("（已过期）" if license_.expired() else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="高级功能激活码签发工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--key", default=str(DEFAULT_KEY_PATH),
                        help=f"私钥路径（默认 {DEFAULT_KEY_PATH}）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_keygen = sub.add_parser("keygen", help="生成签发密钥对")
    p_keygen.add_argument("--force", action="store_true",
                          help="覆盖已存在的私钥（会让已签发的码全部失效）")
    p_keygen.set_defaults(func=cmd_keygen)

    p_issue = sub.add_parser("issue", help="签发一张激活码")
    p_issue.add_argument("--id", required=True, help="码编号，记进你的台账")
    p_issue.add_argument("--serial", help="目标机器序列号（绑机码必填）")
    p_issue.add_argument("--no-bind", action="store_true",
                         help="签发免绑定码：不校验序列号，谁拿到谁能用")
    p_issue.add_argument("--feature", action="append", default=[],
                         required=True, help="开放的功能，可重复")
    p_issue.add_argument("--expires", help="有效期 YYYY-MM-DD，省略为永久")
    p_issue.set_defaults(func=cmd_issue)

    p_verify = sub.add_parser("verify", help="校验一张激活码")
    p_verify.add_argument("--code", required=True)
    p_verify.add_argument("--public-key", help="覆盖代码里内置的公钥")
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
