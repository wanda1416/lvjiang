"""版本检查与更新管理

提供：
- 版本号获取（打包/开发环境）
- GitHub Release 更新检查（后台线程）
- 跳过版本持久化（用户选择"此版本不再询问"后存储到 session.json 的 server_config 节点）
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

# GitHub 仓库信息
GITHUB_REPO = "wanda1416/lvjiang"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


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
    """解析版本号为可比较的整数列表"""
    try:
        return [int(x) for x in version.split(".")]
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

    finished = pyqtSignal(str, str)  # (latest_version, download_url)
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

            latest_version = data.get("tag_name", "").lstrip("v")
            download_url = data.get("html_url", GITHUB_RELEASES_URL)

            if latest_version:
                self.finished.emit(latest_version, download_url)
            else:
                self.error.emit("无法获取版本信息")
        except Exception as e:
            logger.exception("检查更新失败")
            self.error.emit(f"检查更新失败: {e}")
