"""更新提示对话框

启动时发现有新版本时弹出，展示 GitHub Release 标题与发布说明，并提供：
- 该版本不再提醒：跳过该版本，直到有更新版本发布
- 前往发布声明：打开 GitHub Release 页面
- 下载该版本：在浏览器中打开 Windows 安装程序地址
- 忽略该提示：关闭对话框，继续使用当前版本
"""
from __future__ import annotations

from datetime import datetime
from html import escape

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
)

from ...core.update import ReleaseInfo, get_version, set_skip_version
from ...i18n import tr
from ..button_styles import apply_button_style


class UpdateDialog(QDialog):
    """更新提示对话框"""

    # 返回值常量
    ACTION_DOWNLOAD = "download"
    ACTION_RELEASE_NOTES = "release_notes"
    ACTION_SKIP = "skip"

    def __init__(self, release: ReleaseInfo, parent=None):
        super().__init__(parent)
        self._release = release
        self._action = ""
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(tr("发现新版本"))
        self.setMinimumSize(560, 440)
        self.resize(620, 500)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ─── 版本信息 ───
        current_version = get_version()
        info_label = QLabel(
            f"<p style='font-size: 14px;'>"
            f"<b>{escape(self._release.title)}</b><br>"
            f"<span style='color: gray;'>"
            f"{tr('最新版本')}: v{escape(self._release.version)} &nbsp;·&nbsp; "
            f"{tr('当前版本')}: v{escape(current_version)}"
            f"</p>"
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        published_date = self._format_published_date(self._release.published_at)
        if published_date:
            date_label = QLabel(tr("发布于 {date}").format(date=published_date))
            date_label.setStyleSheet("color: gray;")
            layout.addWidget(date_label)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(self._release.body or tr("本次发布暂无详细说明。"))
        layout.addWidget(notes, 1)

        # ─── 底部操作：四个等宽按钮 ───
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._skip_btn = QPushButton(tr("该版本不再提醒"))
        self._skip_btn.clicked.connect(self._on_skip)
        btn_layout.addWidget(self._skip_btn, 1)

        self._release_notes_btn = QPushButton(tr("前往发布声明"))
        self._release_notes_btn.clicked.connect(self._on_release_notes)
        btn_layout.addWidget(self._release_notes_btn, 1)

        self._download_btn = QPushButton(tr("下载该版本"))
        self._download_btn.clicked.connect(self._on_download)
        btn_layout.addWidget(self._download_btn, 1)

        self._close_btn = QPushButton(tr("忽略该提示"))
        self._close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._close_btn, 1)

        for button in (
            self._skip_btn,
            self._release_notes_btn,
            self._download_btn,
            self._close_btn,
        ):
            button.setMinimumHeight(36)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        apply_button_style(
            self._release_notes_btn,
            self._download_btn,
        )
        apply_button_style(
            self._skip_btn,
            self._close_btn,
            variant="neutral",
        )

        layout.addLayout(btn_layout)

    def _on_download(self):
        """在浏览器中打开 Release 安装程序地址。"""
        QDesktopServices.openUrl(QUrl(self._release.download_url))
        self._action = self.ACTION_DOWNLOAD
        self.accept()

    def _on_release_notes(self):
        """在浏览器中查看完整发布声明。"""
        QDesktopServices.openUrl(QUrl(self._release.release_url))
        self._action = self.ACTION_RELEASE_NOTES

    def _on_skip(self):
        """此版本不再询问"""
        set_skip_version(self._release.version)
        self._action = self.ACTION_SKIP
        self.accept()

    @staticmethod
    def _format_published_date(value: str) -> str:
        if not value:
            return ""
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
        except ValueError:
            return value

    @property
    def action(self) -> str:
        """获取用户选择的操作"""
        return self._action
