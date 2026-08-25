"""反馈与建议对话框。

主对话框说明问题受理范围和提交前必须准备的信息，并提供详细规范、
GitHub Issue 与交流群入口。二维码仅在用户主动点击“扫码加群”后展示。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..core import group_qrcode
from ..core.update import GITHUB_REPO
from ..i18n import tr

ISSUE_GUIDE_URL = (
    f"https://github.com/{GITHUB_REPO}/blob/master/"
    "docs/60-userguide/08-feedback-and-issues.md"
)
NEW_ISSUE_URL = f"https://github.com/{GITHUB_REPO}/issues/new"


class GroupQrDialog(QDialog):
    """按需展示交流群二维码的子对话框；支持从 GitHub Pages 刷新最新二维码。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("扫码加群"))
        self.setFixedSize(420, 500)
        self._refresher: group_qrcode.QrCodeRefresher | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        desc_label = QLabel(tr("使用交流和一般咨询请优先在群内讨论"))
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._reload_pixmap()
        layout.addWidget(self._image_label, 1)

        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._refresh_btn = QPushButton(tr("刷新二维码"))
        self._refresh_btn.clicked.connect(self._refresh_qrcode)
        btn_layout.addWidget(self._refresh_btn)
        close_btn = QPushButton(tr("关闭"))
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _reload_pixmap(self) -> None:
        """从本地磁盘（重新）加载二维码图片。"""
        pixmap = QPixmap(str(group_qrcode.QRCODE_PATH))
        if pixmap.isNull():
            self._image_label.setText(tr("交流群二维码加载失败"))
        else:
            self._image_label.setPixmap(pixmap.scaled(
                360,
                360,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def _refresh_qrcode(self) -> None:
        """从 GitHub Pages 拉取最新二维码并覆盖本地文件；失败保留原图。"""
        if self._refresher is not None and self._refresher.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText(tr("刷新中..."))
        self._status_label.setText(tr("正在获取最新二维码..."))

        refresher = group_qrcode.QrCodeRefresher(self)
        refresher.finished.connect(self._on_refresh_finished)
        refresher.error.connect(self._on_refresh_error)
        refresher.finished.connect(refresher.deleteLater)
        refresher.error.connect(refresher.deleteLater)
        self._refresher = refresher
        refresher.start()

    def _on_refresh_finished(self, _data: bytes) -> None:
        self._refresher = None
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText(tr("刷新二维码"))
        self._status_label.setText(tr("二维码已更新"))
        self._reload_pixmap()

    def _on_refresh_error(self, message: str) -> None:
        self._refresher = None
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText(tr("刷新二维码"))
        self._status_label.setText(
            tr("刷新失败，已保留当前二维码：{error}").format(error=message))


class FeedbackDialog(QDialog):
    """展示反馈规范摘要与反馈渠道。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("反馈与建议"))
        self.setFixedSize(600, 460)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title_label = QLabel(tr("提交问题前请准备完整的定位信息"))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title_label)

        self._summary_label = QLabel(
            tr("只有信息足以复现或定位的问题才会进入开发者排查；"
               "受理不代表承诺修复时间。")
        )
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        required_group = QGroupBox(tr("提交 Bug 时至少提供"))
        required_layout = QVBoxLayout(required_group)
        required_label = QLabel(
            "• " + tr("律匠版本；源码版同时提供 commit hash") + "\n"
            "• " + tr("运行环境、布局、设备分辨率与横竖屏方向") + "\n"
            "• " + tr("截图方式、输入方式、工作流名称与全部参数") + "\n"
            "• " + tr("复现步骤、预期结果、实际结果和出现频率") + "\n"
            "• " + tr("异常前后的连续日志；识别或点击问题附截图或录屏")
        )
        required_label.setWordWrap(True)
        required_layout.addWidget(required_label)
        layout.addWidget(required_group)

        scope_group = QGroupBox(tr("受理范围"))
        scope_layout = QVBoxLayout(scope_group)
        self._scope_label = QLabel(
            tr("受理：可复现的 Bug、功能建议、过时或无法执行的文档。")
            + "\n"
            + tr("不受理：一对一教学、远程配置、信息不足、明确不支持的功能、"
                 "第三方修改导致的问题及商业用途。")
        )
        self._scope_label.setWordWrap(True)
        scope_layout.addWidget(self._scope_label)
        layout.addWidget(scope_group)

        privacy_label = QLabel(
            tr("提交日志和截图前，请遮盖账号、角色名、ADB 序列号、Token 等隐私信息。")
        )
        privacy_label.setWordWrap(True)
        layout.addWidget(privacy_label)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        self._group_btn = QPushButton(tr("扫码加群"))
        self._group_btn.clicked.connect(self._open_group_qr)
        btn_layout.addWidget(self._group_btn)
        btn_layout.addStretch()

        self._details_btn = QPushButton(tr("查看详细规范"))
        self._details_btn.clicked.connect(self._open_issue_guide)
        btn_layout.addWidget(self._details_btn)

        self._github_btn = QPushButton(tr("提交 GitHub Issue"))
        self._github_btn.clicked.connect(self._open_github_issue)
        btn_layout.addWidget(self._github_btn)

        self._close_btn = QPushButton(tr("关闭"))
        self._close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self._close_btn)
        layout.addLayout(btn_layout)

    def _open_group_qr(self):
        """用户主动请求时展示交流群二维码。"""
        GroupQrDialog(self).exec()

    @staticmethod
    def _open_issue_guide():
        """打开用户指南中的问题反馈详细规范。"""
        QDesktopServices.openUrl(QUrl(ISSUE_GUIDE_URL))

    @staticmethod
    def _open_github_issue():
        """打开 GitHub 新建 Issue 页面。"""
        QDesktopServices.openUrl(QUrl(NEW_ISSUE_URL))
