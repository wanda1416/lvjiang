"""版本检查与更新管理

提供：
- 版本号获取（打包/开发环境）
- GitHub Release 更新检查（后台线程）
- 跳过版本持久化（用户选择"此版本不再询问"后存储到 interface.json 的 server_config 节点）
"""
from __future__ import annotations

import json
import ssl
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from loguru import logger
from packaging.version import InvalidVersion

from ..i18n import tr

# GitHub 仓库信息
from .public_links import GITHUB_REPO
from .public_links import RELEASES_URL as GITHUB_RELEASES_URL
from .version_info import (
    get_version as get_version,
)
from .version_info import (
    network_access_error_message as network_access_error_message,
)
from .version_info import (
    parse_version as parse_version,
)
from .versioning import application_version

GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


@dataclass(frozen=True)
class ReleaseInfo:
    """GitHub Release 中供更新界面使用的信息。"""

    version: str
    title: str
    body: str
    published_at: str
    release_url: str
    download_url: str


def _select_download_url(data: dict[str, Any], platform: str) -> str:
    """优先选择适合当前平台的 Release 附件。"""
    release_url = str(data.get("html_url") or GITHUB_RELEASES_URL)
    assets = data.get("assets")
    if platform != "win32" or not isinstance(assets, list):
        return release_url

    candidates: list[tuple[int, str]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").lower()
        url = str(asset.get("browser_download_url") or "")
        if not name or not url:
            continue
        if name.endswith(("-setup.exe", "_setup.exe", "setup.exe")):
            priority = 0
        elif name.endswith((".exe", ".msi")):
            priority = 1
        elif "win64" in name and name.endswith(".zip"):
            priority = 2
        elif name.endswith(".zip"):
            priority = 3
        else:
            continue
        candidates.append((priority, url))

    return min(candidates, default=(99, release_url))[1]


def parse_release_info(data: dict[str, Any], platform: str | None = None) -> ReleaseInfo | None:
    """从 GitHub Releases API 响应提取稳定的界面模型。"""
    tag_name = str(data.get("tag_name") or "").strip()
    if not tag_name:
        return None

    version = tag_name[1:] if tag_name[:1].lower() == "v" else tag_name
    if data.get("draft") or data.get("prerelease"):
        return None
    try:
        if application_version(version).is_prerelease:
            return None
    except InvalidVersion:
        return None
    release_url = str(data.get("html_url") or GITHUB_RELEASES_URL)
    return ReleaseInfo(
        version=version,
        title=str(data.get("name") or "").strip() or tag_name,
        body=str(data.get("body") or "").strip(),
        published_at=str(data.get("published_at") or "").strip(),
        release_url=release_url,
        download_url=_select_download_url(data, platform or sys.platform),
    )


def is_newer_version(latest: str, current: str) -> bool:
    """判断 latest 是否比 current 更新"""
    try:
        return application_version(latest) > application_version(current)
    except InvalidVersion as error:
        logger.warning("无法比较升级版本：{}", error)
        return False


# ─── 跳过版本管理（存储于 interface.json 的 server_config 节点）────────────
#
# ⚠️ 警告：禁止添加旧配置迁移逻辑
# 本模块只使用 interface.json。
# 禁止添加读取旧文件的兼容代码。旧配置直接丢弃，不兼容。


def get_skip_version() -> str:
    """获取用户选择跳过的版本号"""
    from .config.interface import get_interface_store
    node = get_interface_store().get_node("server_config") or {}
    return node.get("skip_version", "")


def set_skip_version(version: str) -> None:
    """设置用户选择跳过的版本号"""
    from .config.interface import get_interface_store
    get_interface_store().update_node("server_config", {"skip_version": version})


def should_prompt_update(latest_version: str) -> bool:
    """判断是否应该提示用户更新

    条件：
    1. latest_version > current_version（有新版本）
    2. latest_version > skip_version（未跳过该版本）
    """
    current_version = get_version()
    # 1. 必须比当前版本新
    if not is_newer_version(latest_version, current_version):
        return False
    # 2. 必须超过跳过的版本
    skip_version = get_skip_version()
    if skip_version and not is_newer_version(latest_version, skip_version):
        return False
    return True


# ─── 更新检查服务 ─────────────────────────────────────────


class UpdateCheckError(RuntimeError):
    """更新检查失败；界面或无 GUI 调用方自行展示。"""


def fetch_latest_release() -> ReleaseInfo:
    """检查正式发行版本，不依赖线程或 GUI。"""
    from .telemetry.consent import is_offline_mode
    if is_offline_mode():
        raise UpdateCheckError(tr("当前为离线模式，请关闭离线模式后检查更新"))
    try:
        req = Request(GITHUB_API_URL)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        # 添加 User-Agent 避免被 GitHub 拒绝
        req.add_header("User-Agent", "lvjiang-update-checker")

        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())

        release = parse_release_info(data)
        if release:
            return release
        else:
            raise UpdateCheckError(tr("无法获取版本信息"))
    except HTTPError as e:
        # HTTP 状态是明确的外部响应，不需要打印 urllib 调用栈。
        message = tr("无法访问 {url} 获取更新：HTTP {code}").format(
            url=GITHUB_API_URL, code=e.code)
        logger.warning(message)
        raise UpdateCheckError(message) from e
    except (URLError, TimeoutError, ssl.SSLError, ConnectionError) as error:
        # DNS、连接、超时和 TLS 故障都是常见网络环境问题。调用栈对普通
        # 用户没有帮助，只保留可操作的一行说明。
        message = network_access_error_message(GITHUB_API_URL, "update")
        logger.warning(message)
        raise UpdateCheckError(message) from error
    except UpdateCheckError:
        raise
    except Exception as e:
        logger.exception("检查更新失败")
        raise UpdateCheckError(f"检查更新失败: {e}") from e
