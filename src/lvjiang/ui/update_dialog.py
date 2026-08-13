"""更新提示对话框

启动时发现有新版本时弹出，提供三个选项：
- 前往下载：打开 GitHub Releases 页面
- 此版本不再询问：跳过该版本，直到有更新版本发布
- 继续使用：关闭对话框，继续使用当前版本
"""
from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from ..core.update import get_version, set_skip_version
from ..i18n import tr


class UpdateDialog(QDialog):
    """更新提示对话框"""

    # 返回值常量
    ACTION_DOWNLOAD = "download"
    ACTION_SKIP = "skip"

    def __init__(self, latest_version: str, download_url: str, parent=None):
        super().__init__(parent)
        self._latest_version = latest_version
        self._download_url = download_url
        self._action = ""
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(tr("发现新版本"))
        self.setFixedSize(380, 200)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # ─── 版本信息 ───
        current_version = get_version()
        info_label = QLabel(
            f"<p style='text-align: center; font-size: 13px;'>"
            f"发现新版本 <b>v{self._latest_version}</b><br>"
            f"<span style='color: gray;'>当前版本: v{current_version}</span>"
            f"</p>"
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        layout.addStretch()

        # ─── 按钮 ───
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(8)

        # 前往下载
        self._download_btn = QPushButton(tr("前往下载"))
        self._download_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; "
            "padding: 8px 16px; font-weight: bold; }"
        )
        self._download_btn.clicked.connect(self._on_download)
        btn_layout.addWidget(self._download_btn)

        # 此版本不再询问
        self._skip_btn = QPushButton(tr("此版本不再询问"))
        self._skip_btn.clicked.connect(self._on_skip)
        btn_layout.addWidget(self._skip_btn)

        # 继续使用
        self._close_btn = QPushButton(tr("继续使用"))
        self._close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._close_btn)

        layout.addLayout(btn_layout)

    def _on_download(self):
        """前往下载"""
        QDesktopServices.openUrl(QUrl(self._download_url))
        self._action = self.ACTION_DOWNLOAD
        self.accept()

    def _on_skip(self):
        """此版本不再询问"""
        set_skip_version(self._latest_version)
        self._action = self.ACTION_SKIP
        self.accept()

    @property
    def action(self) -> str:
        """获取用户选择的操作"""
        return self._action
