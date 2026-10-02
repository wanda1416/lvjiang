"""安卓 APK 的发布资产定位、下载与校验。

二维码要指向**和当前 PC 版本一致**的 APK，而不是"最新版"——PC 0.13.9 配手机
0.13.5 是最难查的错配：协议不匹配只表现为"连不上设备"。

资产名由发布流水线固定（`release.yml` 的 publish 作业要求三个产物齐全），所以
下载地址可以纯拼接、**不需要联网**，二维码离线也能出。校验和在 `SHA256SUMS.txt`
里，取它才需要网络——取不到就只显示本地算出的哈希，不拦着用户装。
"""
from __future__ import annotations

import hashlib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from loguru import logger

from ...i18n import tr

#: 与 release.yml 的产物名一致；改名要两边一起改
_APK_NAME = "lvjiang-v{version}.apk"
_SUMS_NAME = "SHA256SUMS.txt"
_DOWNLOAD_BASE = "https://github.com/{repo}/releases/download/{version}"
_USER_AGENT = "lvjiang-mobile-tool"
_CHUNK = 256 * 1024


def _repo() -> str:
    from ..update import GITHUB_REPO

    return GITHUB_REPO


def apk_asset_name(version: str) -> str:
    return _APK_NAME.format(version=version)


def apk_download_url(version: str) -> str:
    """APK 的直链。纯拼接，不联网——离线也要能出二维码。"""
    base = _DOWNLOAD_BASE.format(repo=_repo(), version=version)
    return f"{base}/{apk_asset_name(version)}"


def checksums_url(version: str) -> str:
    base = _DOWNLOAD_BASE.format(repo=_repo(), version=version)
    return f"{base}/{_SUMS_NAME}"


def default_download_dir() -> Path:
    """APK 的下载目录。

    放 data/apk：它是随手就能删的下载缓存，不属于会话数据，也不进仓库
    （见 .gitignore）。下载和"启动时找回已下载的包"必须用同一个函数，
    否则路径在两处各写一遍，改一边就会出现"下载完重启又不认了"。
    """
    from ... import constants

    return Path(constants.DATA_DIR) / "apk"


def find_local_apk(version: str, dest_dir: Path | None = None) -> Path | None:
    """找回之前下载的、与该版本**完全对应**的 APK。

    只认文件名精确匹配的那一个：版本不符的包不该被当成"手上已有"——两边版本
    不一致本身就是这个工具要帮用户发现的问题。
    """
    directory = dest_dir or default_download_dir()
    candidate = directory / apk_asset_name(version)
    return candidate if candidate.is_file() else None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_expected_sha256(version: str, timeout: float = 10) -> str | None:
    """从 SHA256SUMS.txt 取该 APK 的期望校验和；取不到返回 None。

    取不到不是错误：离线、代理、资产还没传完都可能。调用方据此退化为"只显示
    本地哈希"，不该因此拦住安装。
    """
    url = checksums_url(version)
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except (HTTPError, URLError, TimeoutError, ssl.SSLError,
            ConnectionError, OSError) as exc:
        logger.warning(f"读取 {_SUMS_NAME} 失败，跳过校验和比对: {exc}")
        return None
    wanted = apk_asset_name(version)
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == wanted:
            return parts[0].strip().lower()
    logger.warning(f"{_SUMS_NAME} 里没有 {wanted} 的校验和")
    return None


@dataclass(frozen=True)
class ApkFile:
    """已经落到本地的 APK。"""

    path: Path
    version: str
    sha256: str
    #: None = 没取到期望值（离线等）；True/False = 比对结果
    verified: bool | None

    @property
    def size(self) -> int:
        return self.path.stat().st_size


class ApkDownloadError(Exception):
    pass


def download_apk(
    version: str,
    dest_dir: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = 30,
) -> ApkFile:
    """下载 APK 到 dest_dir 并校验。

    先写 ``.part`` 再改名：中断留下的半截文件不会被当成可安装的包。
    ``progress(已下载, 总长)``，总长未知时第二个参数为 0。
    """
    url = apk_download_url(version)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / apk_asset_name(version)
    partial = target.with_suffix(target.suffix + ".part")
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(partial, "wb") as stream:
                while True:
                    if cancelled is not None and cancelled():
                        raise ApkDownloadError(tr("下载已取消"))
                    block = response.read(_CHUNK)
                    if not block:
                        break
                    stream.write(block)
                    done += len(block)
                    if progress is not None:
                        progress(done, total)
    except ApkDownloadError:
        partial.unlink(missing_ok=True)
        raise
    except (HTTPError, URLError, TimeoutError, ssl.SSLError,
            ConnectionError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise ApkDownloadError(
            tr("下载 {url} 失败：{err}").format(url=url, err=exc)) from exc

    digest = file_sha256(partial)
    expected = fetch_expected_sha256(version, timeout=timeout)
    if expected is not None and digest != expected:
        partial.unlink(missing_ok=True)
        raise ApkDownloadError(
            tr("校验和不匹配，文件可能损坏或被篡改\n期望 {want}\n实际 {got}")
            .format(want=expected, got=digest))
    partial.replace(target)
    logger.info(f"APK 已下载: {target}（sha256={digest[:16]}…，"
                f"校验={'通过' if expected else '未比对'}）")
    return ApkFile(path=target, version=version, sha256=digest,
                   verified=None if expected is None else True)


def inspect_local_apk(path: Path, version: str) -> ApkFile:
    """接受用户手选的本地 APK：算哈希，能联网就比对。"""
    digest = file_sha256(path)
    expected = fetch_expected_sha256(version)
    return ApkFile(
        path=path, version=version, sha256=digest,
        verified=None if expected is None else digest == expected)
