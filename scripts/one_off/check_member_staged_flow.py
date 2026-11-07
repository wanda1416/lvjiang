"""一次性核对：真实服务器下「后台下载会员包 → 下次启动安装」的完整链路。

与 ``tests/core/test_member_staged_install.py`` 的区别是**不打桩**：真实激活码、
真实会员索引/密文包、真实授权接口，只把更新存储换到临时目录，绝不写入本机
``config/updates`` 或 ``config/system``。

只读本机凭据：激活码与授权状态由既有授权接口读取，脚本不打印、不复制、不落盘。
用法：``python scripts/one_off/check_member_staged_flow.py [--timeout 60]``
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import member_probe_support as support  # noqa: E402
from loguru import logger  # noqa: E402

from lvjiang.core.config import member_download, protected_remote, remote  # noqa: E402
from lvjiang.core.config import resolver as resolver_module  # noqa: E402
from lvjiang.core.config.resolver import ConfigResolver  # noqa: E402
from lvjiang.core.system_preset import write_baseline_identity  # noqa: E402
from lvjiang.core.update import get_version  # noqa: E402
from lvjiang.core.update_bundle import read_tree  # noqa: E402

logger.remove()
logger.add(sys.stdout, level="INFO", format="{message}")

_CALLS = {"get": 0, "grant": 0}


def _instrument(monkeypatch_free: dict) -> None:
    """计数但照常调用真实实现，用来证明第二次启动不再下载整包。"""
    real_fetch = remote._fetch_bytes
    real_request = member_download.request_json

    def counting_fetch(url, **kwargs):
        _CALLS["get"] += 1
        return real_fetch(url, **kwargs)

    def counting_request(*args, **kwargs):
        _CALLS["grant"] += 1
        return real_request(*args, **kwargs)

    remote._fetch_bytes = counting_fetch
    member_download.request_json = counting_request
    monkeypatch_free["fetch"] = real_fetch
    monkeypatch_free["request"] = real_request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--ledger", nargs="?", const=str(support.default_ledger()),
                        default=None,
                        help="改用签发台账里绑定本机序列号的激活码（可给台账路径）")
    args = parser.parse_args()

    if args.ledger is None:
        return _run(args)

    code, bind, note = support.find_serial_bound_code(Path(args.ledger))
    print(f"台账：{note}；码类型 bind={bind or '-'}")
    if code is None:
        return 4
    with support.use_code(code):
        if bind == "claim":
            print(f"本机回执：{support.ensure_claim_activated(code)}")
        return _run(args)


def _run(args) -> int:
    if protected_remote.snapshot() is None:
        print("没有可用的本地会员授权，无法核对真实链路")
        return 2

    with tempfile.TemporaryDirectory(prefix="lvjiang-staged-") as raw:
        root = Path(raw)
        system = root / "system"
        system.mkdir()
        (system / "opaque.bin").write_bytes(b"baseline")
        (root / "local").mkdir()
        version = get_version()
        write_baseline_identity(system, version, config_dir=root)
        resolver_module._resolver = ConfigResolver(
            system, root / "local", dev_mode=False, config_dir=root)

        originals: dict = {}
        _instrument(originals)
        try:
            job = remote.build_sync_job()
            if job.config is None:
                print("在线配置不可用（开发模式或缺少基底记录），无法核对")
                return 3
            store = remote._installed_store()
            assert store is not None
            print(f"暂存根目录：{root}（临时；不触碰本机 config）")

            started = time.monotonic()
            staged = remote.stage_outcome(remote.run_sync(job, timeout=args.timeout))
            first = (time.monotonic() - started,
                     _CALLS["get"], _CALLS["grant"])
            print(f"阶段一（后台下载）：{first[0]:.1f}s，GET={first[1]}，授权请求={first[2]}")
            print(f"暂存结果：{staged or '没有可用更新'}")
            assert not (system / "opaque.bin").read_bytes() != b"baseline", "下载阶段不得改动 system"
            plaintext = [p for p in (root / "updates").rglob("*")
                         if p.is_file() and p.suffix in {".bin", ".json", ".wf"} and "member" in p.name]
            print(f"暂存区明文检查：{len(plaintext)} 个可疑文件（member 归档只应有 manifest.json/package.bin）")

            _CALLS["get"] = 0
            _CALLS["grant"] = 0
            started = time.monotonic()
            changed = remote.install_pending(timeout=args.timeout)
            second = (time.monotonic() - started,
                      _CALLS["get"], _CALLS["grant"])
            print(f"阶段二（下次启动安装）：{second[0]:.1f}s，GET={second[1]}，授权请求={second[2]}")
            print(f"安装变更：{len(changed)} 个文件；暂存清空："
                  f"public={store.pending_public() is None} member={store.pending_member() is None}")
            print(f"system 文件数：{len(read_tree(system))}")
            print("判定：下载阶段不改 system、安装阶段不重新下载整包"
                  + ("，符合设计" if second[1] == 0 else f"，但仍有 {second[1]} 次下载，需要复核"))
            return 0 if second[1] == 0 else 1
        finally:
            remote._fetch_bytes = originals["fetch"]
            member_download.request_json = originals["request"]
            resolver_module._resolver = None


if __name__ == "__main__":
    raise SystemExit(main())
