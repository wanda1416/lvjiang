"""一次性核对脚本的共用准备：从签发台账取一张绑定本机序列号的激活码。

真实激活码、序列号、回执一律不打印、不写入仓库文件；启用后授权状态写回临时目录，
本机 ``config/local`` 里的授权记录不会被读改。

台账默认取 ``~/.lvjiang/license_ledger.db``，可用 ``--ledger`` 或
``LVJIANG_LICENSE_LEDGER`` 覆盖。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path


def default_ledger() -> Path:
    override = os.environ.get("LVJIANG_LICENSE_LEDGER")
    return Path(override) if override else Path.home() / ".lvjiang" / "license_ledger.db"


def find_serial_bound_code(ledger: Path) -> tuple[str | None, str, str]:
    """找一张绑定本机序列号的已签发激活码。

    返回 ``(码, bind 模式, 结论)``。优先取 ``bind=claim``（本机已在线认领）的码——
    只有它会走 ``nonce + 服务端签名回执``分支；``bind=serial`` 的码把序列号写进码里，
    客户端离线验签，正常运行时不会请求回执。
    """
    from lvjiang.core.license.hardware import current_serial, normalize_serial

    serial = current_serial()
    if not serial:
        return None, "", "读不到本机序列号（非 Windows 或缺少硬件标识）"
    if not ledger.is_file():
        return None, "", f"台账不存在：{ledger}"
    wanted = normalize_serial(serial)
    with sqlite3.connect(f"file:{ledger.as_posix()}?mode=ro", uri=True) as con:
        issued = {code: bind for code, bind in
                  con.execute("select code, bind from issued").fetchall()}
        cloud = con.execute(
            "select code, bind, bound_serial, enabled from cloud_state").fetchall()
    claim_bound = [
        code for code, bind, bound, enabled in cloud
        if bind == "claim" and str(enabled) == "1"
        and normalize_serial(str(bound or "")) == wanted
        and issued.get(code) == "claim"
    ]
    if claim_bound:
        return claim_bound[0], "claim", f"台账共 {len(issued)} 条，本机已认领的 claim 码 1 张"
    serial_bound = [
        code for code, bind in issued.items()
        if bind == "serial" and normalize_serial(str(
            next((bound for c, _b, bound, _e in cloud if c == code), "") or "")) == wanted
    ]
    if serial_bound:
        return serial_bound[0], "serial", f"台账共 {len(issued)} 条，仅有绑定本机序列号的 serial 码 1 张"
    return None, "", f"台账共 {len(issued)} 条，没有绑定本机序列号的 claim 码"


def with_serial(access):
    """把授权快照换成「带本机序列号」的形态。

    台账里已签发的码可能是 ``bind=serial``（序列号写进码里，客户端离线验签），
    这种码正常运行时不会请求回执。要验证「暂存会员包安装时重新取一次授权并验回执」
    这条分支，就得显式补上序列号，模拟 ``bind=claim`` 的客户端行为。
    """
    from lvjiang.core.license.hardware import current_serial, normalize_serial

    if access is None:
        return None
    serial = current_serial()
    if not serial:
        return access
    return type(access)(access.service, access.code, access.expires,
                        normalize_serial(serial), access.expires_at)


def ensure_claim_activated(code: str) -> str:
    """让 ``bind=claim`` 的码取得本机回执（已认领的码走同一接口，幂等）。

    这一步等价于用户在设置里激活：请求 ``/v1/activate`` 并把回执存进**临时**目录，
    不动本机 ``config/local`` 的授权记录。
    """
    from lvjiang.core import license as license_pkg

    entitlement = license_pkg.try_activate(code)
    if entitlement.active:
        return "已取得回执"
    return f"激活失败：{entitlement.reason or '未知原因'}"


@contextmanager
def use_code(code: str):
    """只在本次进程内把当前激活码换成 ``code``。

    授权读写路径全部指向临时目录：既不改用户现用的授权记录，也不会把码落盘。
    """
    from lvjiang.core import license as license_pkg
    from lvjiang.core.license import activation as license_activation
    from lvjiang.core.license import store as license_store

    scratch = tempfile.TemporaryDirectory(prefix="lvjiang-license-")
    target = Path(scratch.name) / "license.txt"
    original = {
        "license_path": license_store.license_path,
        "activation_path": license_store.activation_path,
        "store_load_code": license_store.load_code,
        "activation_load_code": license_activation.load_code,
        "pkg_load_code": license_pkg.load_code,
    }
    license_store.license_path = lambda: target
    license_store.activation_path = lambda: target.with_name("license-activation.json")
    license_store.load_code = lambda: code
    license_activation.load_code = lambda: code
    license_pkg.load_code = lambda: code
    license_pkg._cached = None  # noqa: SLF001 — 丢弃上一张码的授权结论
    try:
        yield
    finally:
        license_store.license_path = original["license_path"]
        license_store.activation_path = original["activation_path"]
        license_store.load_code = original["store_load_code"]
        license_activation.load_code = original["activation_load_code"]
        license_pkg.load_code = original["pkg_load_code"]
        license_pkg._cached = None  # noqa: SLF001
        scratch.cleanup()
