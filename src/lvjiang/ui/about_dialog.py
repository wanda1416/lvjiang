"""关于对话框 - 展示版本信息、检查更新、版权信息

从「帮助 → 关于」打开，提供：
- 应用名称与版本号
- 功能简介
- 检查更新按钮（基于 GitHub Release）
- 版权信息
"""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

from loguru import logger
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

# GitHub 仓库信息
GITHUB_REPO = "wanda1416/lvjiang"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _get_version() -> str:
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
        from pathlib import Path
        import tomllib
        # __file__ = src/lvjiang/ui/about_dialog.py
        # 需要向上 4 级到项目根目录
        pyproject = Path(__file__).parent.parent.parent.parent / "pyproject.toml"
        if pyproject.exists():
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            return data.get("project", {}).get("version", "unknown")
    except Exception:
        pass

    return "unknown"


class _UpdateChecker(QThread):
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


class AboutDialog(QDialog):
    """关于对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于律匠")
        self.setFixedSize(400, 320)
        self._update_checker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ─── 标题与版本 ───
        version = _get_version()
        title_label = QLabel(f"<h2>律匠</h2>")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        version_label = QLabel(f"版本 {version}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet("color: gray; font-size: 12px;")
        layout.addWidget(version_label)

        # ─── 功能简介 ───
        desc_label = QLabel(
            "<p style='text-align: center;'>"
            "通用视觉 RPA 引擎<br>"
            "<small>窗口定位截屏 → 区域标注 → OCR识别 → 工作流执行</small>"
            "</p>"
        )
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addSpacing(8)

        # ─── 技术栈 ───
        tech_label = QLabel(
            "<p style='text-align: center; color: gray; font-size: 11px;'>"
            "基于 PyQt6 · RapidOCR · OpenCV"
            "</p>"
        )
        tech_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tech_label)

        layout.addStretch()

        # ─── 检查更新按钮 ───
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._check_update_btn = QPushButton("检查更新")
        self._check_update_btn.clicked.connect(self._check_update)
        btn_layout.addWidget(self._check_update_btn)

        self._github_btn = QPushButton("GitHub")
        self._github_btn.clicked.connect(self._open_github)
        btn_layout.addWidget(self._github_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addSpacing(8)

        # ─── 版权信息 ───
        copyright_label = QLabel(
            "<p style='text-align: center; color: gray; font-size: 10px;'>"
            "Copyright © 2024-2026 律匠团队<br>"
            "本项目仅供学习交流使用"
            "</p>"
        )
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(copyright_label)

    def _check_update(self):
        """检查 GitHub Release 更新"""
        self._check_update_btn.setEnabled(False)
        self._check_update_btn.setText("检查中...")

        self._update_checker = _UpdateChecker()
        self._update_checker.finished.connect(self._on_update_available)
        self._update_checker.error.connect(self._on_update_error)
        self._update_checker.start()

    def _on_update_available(self, latest_version: str, download_url: str):
        """发现新版本"""
        self._check_update_btn.setEnabled(True)
        self._check_update_btn.setText("检查更新")

        current_version = _get_version()

        # 简单版本比较
        try:
            current_parts = [int(x) for x in current_version.split(".")]
            latest_parts = [int(x) for x in latest_version.split(".")]
            is_newer = latest_parts > current_parts
        except (ValueError, AttributeError):
            is_newer = latest_version != current_version

        if is_newer:
            result = QMessageBox.information(
                self,
                "发现新版本",
                f"发现新版本 v{latest_version}\n"
                f"当前版本: v{current_version}\n\n"
                "是否前往下载？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if result == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(download_url)
        else:
            QMessageBox.information(
                self,
                "已是最新版本",
                f"当前版本 v{current_version} 已是最新版本",
            )

    def _on_update_error(self, error_msg: str):
        """检查更新失败"""
        self._check_update_btn.setEnabled(True)
        self._check_update_btn.setText("检查更新")
        QMessageBox.warning(self, "检查更新失败", error_msg)

    def _open_github(self):
        """打开 GitHub 仓库页面"""
        QDesktopServices.openUrl(f"https://github.com/{GITHUB_REPO}")
