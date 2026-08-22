"""版本检查与更新管理

提供：
- 版本号获取（打包/开发环境）
- GitHub Release 更新检查（后台线程）
- 跳过版本持久化（用户选择"此版本不再询问"后存储到 session.json 的 server_config 节点）
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ..i18n import tr

# GitHub 仓库信息
GITHUB_REPO = "wanda1416/lvjiang"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
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
    release_url = str(data.get("html_url") or GITHUB_RELEASES_URL)
    return ReleaseInfo(
        version=version,
        title=str(data.get("name") or "").strip() or tag_name,
        body=str(data.get("body") or "").strip(),
        published_at=str(data.get("published_at") or "").strip(),
        release_url=release_url,
        download_url=_select_download_url(data, platform or sys.platform),
    )


def get_version() -> str:
    """获取版本号

    优先级：
    1. _version.py（打包时注入）
    2. importlib.metadata（已安装包）
    3. pyproject.toml（开发环境）
    """
    # 1. 优先从 _version.py 读取（打包时注入）
    try:
        from .._version import __version__
        if __version__ and __version__ != "0.0.0.dev0":
            return __version__
    except Exception:
        pass

    # 2. 从 package metadata 读取（已安装时）
    try:
        from importlib.metadata import version
        return version("lvjiang")
    except Exception:
        pass

    # 3. 从 pyproject.toml 读取（开发环境）
    try:
        import tomllib
        # __file__ = src/lvjiang/core/update.py
        # 需要向上 4 级到项目根目录
        pyproject = Path(__file__).parent.parent.parent.parent / "pyproject.toml"
        if pyproject.exists():
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            return data.get("project", {}).get("version", "unknown")
    except Exception:
        pass

    return "unknown"


def parse_version(version: str) -> list[int]:
    """解析版本号为可比较的整数列表

    自动去除预发布后缀（如 0.5.0b0 → [0, 5, 0]）
    """
    try:
        # 去掉预发布后缀：a/b/rc/alpha/beta/dev 及其数字
        cleaned = re.sub(r'[.-]?(a|alpha|b|beta|rc|dev)\d*$', '', version, flags=re.IGNORECASE)
        return [int(x) for x in cleaned.split(".")]
    except (ValueError, AttributeError):
        return [0]


def is_newer_version(latest: str, current: str) -> bool:
    """判断 latest 是否比 current 更新"""
    return parse_version(latest) > parse_version(current)


# ─── 跳过版本管理（存储于 session.json 的 server_config 节点）────────────
#
# ⚠️ 警告：禁止添加旧配置迁移逻辑
# 本模块已从 config/local/update.json 迁移到 session.json。
# 禁止添加读取旧文件的兼容代码。旧配置直接丢弃，不兼容。


def get_skip_version() -> str:
    """获取用户选择跳过的版本号"""
    from .config.session import get_session_store
    node = get_session_store().get_node("server_config") or {}
    return node.get("skip_version", "")


def set_skip_version(version: str) -> None:
    """设置用户选择跳过的版本号"""
    from .config.session import get_session_store
    get_session_store().update_node("server_config", {"skip_version": version})


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


# ─── 更新检查线程 ─────────────────────────────────────────


class UpdateChecker(QThread):
    """后台线程检查 GitHub Release 更新"""

    finished = pyqtSignal(object)  # ReleaseInfo
    error = pyqtSignal(str)

    def run(self):
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
                self.finished.emit(release)
            else:
                self.error.emit(tr("无法获取版本信息"))
        except Exception as e:
            logger.exception("检查更新失败")
            self.error.emit(f"检查更新失败: {e}")
