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

from ...core.update import (
    GITHUB_REPO,
    ReleaseInfo,
    UpdateChecker,
    get_version,
    is_newer_version,
)
from ...i18n import tr
from ..button_styles import apply_button_style, exec_styled_message_box

# 导出供外部使用
__all__ = ["AboutDialog", "GITHUB_REPO"]


class AboutDialog(QDialog):
    """关于对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("关于律匠"))
        self.setFixedSize(420, 520)
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

        # ─── 免费声明与许可证 ───
        # 「完全开源」这个说法不准确：PolyForm Noncommercial 限制商业用途，
        # 不符合 OSI 的开源定义。写成"源码公开、免费使用"才是事实。
        opensource_label = QLabel(
            f"<p style='text-align: center; font-size: 15px; font-weight: 600;'>"
            f"{tr('本项目源码公开、免费使用')}"
            f"</p>"
        )
        opensource_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(opensource_label)

        license_label = QLabel(
            "<p style='text-align: center; font-size: 11px;'>"
            f"{tr('许可证')}：PolyForm Noncommercial License 1.0.0"
            f"<br><span style='color: gray;'>{tr('仅限非商业使用')}</span>"
            "</p>"
        )
        license_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        license_label.setWordWrap(True)
        layout.addWidget(license_label)

        # ─── 免责声明 ───
        # 措辞与发布说明页脚保持一致，避免同一件事两种口径。
        # 提示语先算好再拼：f-string 替换字段里换行要 Python 3.12，本项目下限是 3.11
        usage_notice = tr(
            "本项目仅供学习与技术研究使用。使用自动化工具操作游戏可能违反相关游戏的"
            "用户协议，由此产生的后果由使用者自行承担。")
        warranty_notice = tr(
            "软件按现状提供，不附带任何担保，作者不对使用本软件造成的任何损失负责。")
        disclaimer_label = QLabel(
            "<p style='font-size: 11px;'>"
            f"<b>{tr('免责声明')}</b>：{usage_notice}{warranty_notice}"
            "</p>"
        )
        disclaimer_label.setWordWrap(True)
        disclaimer_label.setStyleSheet(
            "color: palette(mid); border: 1px solid palette(mid); "
            "border-radius: 4px; padding: 6px;")
        layout.addWidget(disclaimer_label)

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

        self._license_btn = QPushButton(tr("许可证"))
        self._license_btn.clicked.connect(self._open_license)
        btn_layout.addWidget(self._license_btn)
        apply_button_style(
            self._check_update_btn, self._github_btn, self._license_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addSpacing(8)

        # ─── 版权信息 ───
        copyright_label = QLabel(
            "<p style='text-align: center; color: gray; font-size: 10px;'>"
            "Copyright © 2024-2026 wanda1416"
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
            box = QMessageBox(
                QMessageBox.Icon.Information,
                tr("已是最新版本"),
                tr("当前版本 v{current} 已是最新版本").format(
                    current=current_version),
                QMessageBox.StandardButton.Ok,
                self,
            )
            exec_styled_message_box(box)

    def _on_update_error(self, error_msg: str):
        """检查更新失败"""
        self._check_update_btn.setEnabled(True)
        self._check_update_btn.setText(tr("检查更新"))
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            tr("检查更新失败"),
            error_msg,
            QMessageBox.StandardButton.Ok,
            self,
        )
        exec_styled_message_box(box)

    def _open_github(self):
        """打开 GitHub 仓库页面"""
        QDesktopServices.openUrl(QUrl(f"https://github.com/{GITHUB_REPO}"))

    def _open_license(self):
        """打开随包分发的许可证全文；发行包里没有时退到官方条款页。

        发行包由 package.bat 拷一份 LICENSE.txt 到 exe 旁；源码运行时则是仓库根的
        LICENSE。两处都找不到才走网页——离线用户也该看得到条款。
        """
        from ...constants import PROJECT_ROOT
        for candidate in (PROJECT_ROOT / "LICENSE.txt", PROJECT_ROOT / "LICENSE"):
            if candidate.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate)))
                return
        QDesktopServices.openUrl(QUrl(
            "https://polyformproject.org/licenses/noncommercial/1.0.0/"))
