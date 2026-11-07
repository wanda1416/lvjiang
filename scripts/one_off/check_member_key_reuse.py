"""一次性核对：会员授权接口能否被同一激活码重复调用。

背景：在线配置改成「后台下载会员密文包、下次启动再取一次授权解密」后，
同一个激活码会在两次进程里各请求一次 ``/v1/key``。若服务端限制重复授权，
这条链路就会永远停在「已下载待安装」——这是纯桩测试看不出来的部分。

``--ledger`` 会改用签发台账里绑定**本机序列号**的激活码，走带序列号的
``nonce + 服务端签名回执``分支（默认用本机当前生效的那张码）。

只读：不写任何配置文件，不输出激活码、密钥、回执、摘要或服务地址。
用法：``python scripts/one_off/check_member_key_reuse.py [--attempts 3] [--ledger [PATH]]``
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import member_probe_support as support  # noqa: E402
from loguru import logger  # noqa: E402

from lvjiang.core.config import protected_remote, remote  # noqa: E402
from lvjiang.core.config.content_service import ServiceError  # noqa: E402
from lvjiang.core.config.protected_remote import (  # noqa: E402
    manifest_url,
    parse_manifest,
    select_package,
)
from lvjiang.core.update import get_version  # noqa: E402

logger.remove()
logger.add(sys.stdout, level="INFO", format="{message}")


def _request(access, package: dict) -> dict:
    """按产品代码同样的正文发一次授权请求（每次新 nonce）。"""
    import secrets

    from lvjiang.core.config.content_service import request_json
    from lvjiang.core.license.code import normalize_code

    body = {"code": normalize_code(access.code), "key_id": package["key_id"]}
    nonce = ""
    if access.serial:
        nonce = secrets.token_hex(16)
        body.update(serial=access.serial, nonce=nonce)
    grant = request_json(access.service, "/v1/key", body, timeout=15)
    return {"grant": grant, "nonce": nonce}


def _verify_receipt(access, grant: dict, nonce: str) -> str:
    """带序列号时走一遍签名回执校验，返回状态（不打印细节）。"""
    if not access.serial:
        return "skipped(无序列号绑定)"
    from lvjiang.core.license.activation import accept_status
    data = accept_status(access.code, access.serial, grant.get("receipt"), nonce)
    return str(data.get("status"))


def _run(attempts: int, gap: float, *, force_serial: bool = False) -> int:
    access = protected_remote.snapshot()
    if access is None:
        print("没有可用的本地会员授权（未填激活码或等级不足/已过期），无法核对")
        return 2
    if force_serial:
        access = support.with_serial(access)
    print(f"授权快照：等级可用；绑定序列号={'是' if access.serial else '否'}"
          + ("（脚本补的序列号）" if force_serial else ""))

    raw, _, _ = remote._fetch_bytes(manifest_url(), max_bytes=256 * 1024, timeout=15)
    index = parse_manifest(json.loads(raw))
    package = select_package(index, get_version())
    if package is None:
        print("当前客户端版本没有适用的会员包，无法核对")
        return 3
    print(f"会员包索引：config_version={index['config_version']}，"
          f"命中包 config_version={package['config_version']}")

    outcomes: list[str] = []
    failures: list[str] = []
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            result = _request(access, package)
        except ServiceError as error:
            elapsed = (time.monotonic() - started) * 1000
            outcomes.append("fail")
            failures.append(f"HTTP {error.status} {error.code or ''}".strip())
            print(f"第 {attempt} 次：HTTP {error.status} "
                  f"code={error.code or '-'} reason={error.reason or '-'} ({elapsed:.0f} ms)")
        except Exception as error:  # noqa: BLE001
            elapsed = (time.monotonic() - started) * 1000
            outcomes.append("fail")
            failures.append(type(error).__name__)
            print(f"第 {attempt} 次：异常 {type(error).__name__} ({elapsed:.0f} ms)")
        else:
            elapsed = (time.monotonic() - started) * 1000
            state = _verify_receipt(access, result["grant"], result["nonce"])
            outcomes.append("ok")
            print(f"第 {attempt} 次：通过，回执校验={state}，"
                  f"响应字段={len(result['grant'])} 个 ({elapsed:.0f} ms)")
        if attempt < attempts and gap:
            time.sleep(gap)

    ok = outcomes.count("ok")
    print(f"结论：{ok}/{len(outcomes)} 次授权请求通过")
    if ok == len(outcomes):
        print("判定：后台下载与下次启动安装各请求一次授权的设计可用")
        return 0
    if force_serial and failures and all("invalid_request" in item for item in failures):
        print("判定：该码不接受带序列号的授权请求（bind=serial），"
              "回执分支无法用它验证；这不是限流")
        return 5
    print("判定：授权请求被拒，需要复核服务端策略")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--gap", type=float, default=0.0, help="两次请求之间的间隔秒数")
    parser.add_argument("--ledger", nargs="?", const=str(support.default_ledger()),
                        default=None,
                        help="改用签发台账里绑定本机序列号的激活码（可给台账路径）")
    parser.add_argument("--force-serial", action="store_true",
                        help="补上本机序列号，模拟 bind=claim 的客户端去验服务端签名回执")
    args = parser.parse_args()
    if args.ledger is None:
        return _run(args.attempts, args.gap, force_serial=args.force_serial)
    code, bind, note = support.find_serial_bound_code(Path(args.ledger))
    print(f"台账：{note}；码类型 bind={bind or '-'}")
    if code is None:
        return 4
    with support.use_code(code):
        print("已启用台账中绑定本机序列号的激活码（内容不打印，授权记录写临时目录）")
        if bind == "claim":
            print(f"本机回执：{support.ensure_claim_activated(code)}")
        return _run(args.attempts, args.gap, force_serial=args.force_serial and bind != "claim")


if __name__ == "__main__":
    raise SystemExit(main())
