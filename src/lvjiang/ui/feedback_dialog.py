"""反馈对话框 - 展示微信交流群二维码，提供 GitHub Issue 入口

从「帮助 → 反馈」打开，提供：
- 微信交流群二维码展示
- GitHub Issue 按钮跳转
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..core.update import GITHUB_REPO
from ..i18n import tr


def _resource_path(relative: str) -> Path:
    """获取资源文件路径，兼容 PyInstaller 打包与开发环境"""
    if getattr(sys, "frozen", False):
        # 打包后 data/image 与 exe 同级
        base = Path(sys.executable).parent
    else:
        # 开发环境：从项目根目录解析
        base = Path(__file__).resolve().parents[3]
    return base / relative


class FeedbackDialog(QDialog):
    """反馈对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("反馈与建议"))
        self.setFixedSize(420, 480)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ─── 说明文字 ───
        desc_label = QLabel(
            f"<p style='text-align: center; font-size: 14px; font-weight: 600;'>"
            f"{tr('欢迎加群反馈问题和提交建议')}"
            f"</p>"
        )
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc_label)

        # ─── 二维码图片 ───
        img_path = _resource_path("data/image/feedback-qrcode.jpg")
        pixmap = QPixmap(str(img_path))
        img_label = QLabel()
        img_label.setPixmap(pixmap.scaled(
            360, 360,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(img_label)

        layout.addStretch()

        # ─── GitHub Issue 按钮 ───
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._github_btn = QPushButton("GitHub Issue")
        self._github_btn.clicked.connect(self._open_github_issue)
        btn_layout.addWidget(self._github_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addSpacing(8)

    def _open_github_issue(self):
        """打开 GitHub Issue 页面"""
        QDesktopServices.openUrl(QUrl(f"https://github.com/{GITHUB_REPO}/issues"))
