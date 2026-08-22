"""关于对话框 - 展示版本信息、检查更新、版权信息

从「帮助 → 关于」打开，提供：
- 应用名称与版本号
- 功能简介
- 检查更新按钮（基于 GitHub Release）
- 版权信息
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..core.update import (
    GITHUB_REPO,
    ReleaseInfo,
    UpdateChecker,
    get_version,
    is_newer_version,
)
from ..i18n import tr

# 导出供外部使用
__all__ = ["AboutDialog", "GITHUB_REPO"]


class AboutDialog(QDialog):
    """关于对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("关于律匠"))
        self.setFixedSize(400, 350)
        self._update_checker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ─── 标题与版本 ───
        version = get_version()
        title_label = QLabel(f"<h2>{tr('律匠')}</h2>")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        version_label = QLabel(f"{tr('版本')} {version}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet("color: gray; font-size: 12px;")
        layout.addWidget(version_label)

        # ─── 功能简介 ───
        desc_label = QLabel(
            "<p style='text-align: center;'>"
            f"{tr('通用视觉 RPA 引擎')}<br>"
            f"<small>{tr('窗口定位截屏 → 区域标注 → OCR识别 → 工作流执行')}</small>"
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

        # ─── 开源声明 ───
        opensource_label = QLabel(
            f"<p style='text-align: center; font-size: 15px; font-weight: 600;'>"
            f"{tr('本项目完全开源免费')}"
            f"</p>"
        )
        opensource_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(opensource_label)

        layout.addStretch()

        # ─── 检查更新按钮 ───
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._check_update_btn = QPushButton(tr("检查更新"))
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
            "Copyright © 2024-2026 wanda1416<br>"
            f"{tr('本项目仅供学习交流使用')}"
            "</p>"
        )
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(copyright_label)

    def _check_update(self):
        """检查 GitHub Release 更新"""
        self._check_update_btn.setEnabled(False)
        self._check_update_btn.setText(tr("检查中..."))

        self._update_checker = UpdateChecker()
        self._update_checker.finished.connect(self._on_update_available)
        self._update_checker.error.connect(self._on_update_error)
        self._update_checker.start()

    def _on_update_available(self, release: ReleaseInfo):
        """发现新版本"""
        self._check_update_btn.setEnabled(True)
        self._check_update_btn.setText(tr("检查更新"))

        current_version = get_version()

        if is_newer_version(release.version, current_version):
            from .update_dialog import UpdateDialog
            UpdateDialog(release, self).exec()
        else:
            QMessageBox.information(
                self,
                tr("已是最新版本"),
                tr("当前版本 v{current} 已是最新版本").format(current=current_version),
            )

    def _on_update_error(self, error_msg: str):
        """检查更新失败"""
        self._check_update_btn.setEnabled(True)
        self._check_update_btn.setText(tr("检查更新"))
        QMessageBox.warning(self, tr("检查更新失败"), error_msg)

    def _open_github(self):
        """打开 GitHub 仓库页面"""
        QDesktopServices.openUrl(QUrl(f"https://github.com/{GITHUB_REPO}"))
