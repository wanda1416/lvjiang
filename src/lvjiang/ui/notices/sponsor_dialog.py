"""赞助对话框：展示微信 / 支付宝收款码。

入口是「帮助 → 赞助」，与「帮助 → 反馈」并列。文案只说自愿赞助：收款码
不换取功能、授权或优先支持，任何暗示对价的措辞都不该出现在这里——既有
激活码/会员体系已经承担了功能授权，两者混在一起会让用户误以为赞助是第二
套收费。
"""
from __future__ import annotations

from functools import partial

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ...core import support_qrcode
from ...core.public_links import SUPPORT_PAGE_URL
from ...i18n import tr
from ..button_styles import apply_button_style
from .qrcode_refresher import ImageRefreshThread

#: 收款码显示边长。图片按 2x 生成（512px），这里缩到 240 既不糊也不占屏。
IMAGE_SIZE = 240

KIND_LABELS = {"wechat": "微信", "alipay": "支付宝"}


class SponsorDialog(QDialog):
    """自愿赞助入口：两张收款码 + 站点赞助页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("赞助律匠"))
        self.setFixedSize(620, 486)
        self._labels: dict[str, QLabel] = {}
        # 对话框关闭不销毁尚在下载的线程，线程结束后自行释放（同 GroupQrDialog）。
        self._threads: dict[str, ImageRefreshThread] = {}
        self._setup_ui()
        self._load_cached()

    # ─── 界面 ────────────────────────────────────────────────
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title_label = QLabel(tr("支持律匠"))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title_label)

        intro_label = QLabel(
            tr("律匠免费使用、源码公开。如果它帮你省下了时间，"
               "可以自愿请作者喝杯咖啡。")
        )
        intro_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        scope_label = QLabel(
            tr("赞助完全自愿，不换取功能、授权或优先支持，也不影响任何既有功能。")
        )
        scope_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scope_label.setWordWrap(True)
        scope_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(scope_label)

        images = QHBoxLayout()
        images.setSpacing(24)
        images.addStretch()
        for kind in support_qrcode.KINDS:
            column = QVBoxLayout()
            column.setSpacing(6)
            name = QLabel(KIND_LABELS.get(kind, kind))
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name.setStyleSheet("font-weight: 600;")
            column.addWidget(name)
            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setFixedSize(IMAGE_SIZE, IMAGE_SIZE)
            # 收款码必须"深色模块 + 浅色底"，深色主题下直接贴到窗口底色上
            # 会吃掉静默区对比度，扫不出来。
            image.setStyleSheet(
                "background: #ffffff; border-radius: 10px; padding: 6px;")
            self._labels[kind] = image
            column.addWidget(image)
            images.addLayout(column)
        images.addStretch()
        layout.addLayout(images)

        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._status_label)
        layout.addStretch()

        buttons = QHBoxLayout()
        self._page_btn = QPushButton(tr("打开赞助页面"))
        self._page_btn.setToolTip(SUPPORT_PAGE_URL)
        self._page_btn.clicked.connect(self._open_page)
        buttons.addWidget(self._page_btn)
        buttons.addStretch()
        self._refresh_btn = QPushButton(tr("刷新二维码"))
        self._refresh_btn.clicked.connect(self._refresh_all)
        buttons.addWidget(self._refresh_btn)
        close_btn = QPushButton(tr("关闭"))
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        apply_button_style(self._page_btn, self._refresh_btn)
        apply_button_style(close_btn, variant="neutral")
        layout.addLayout(buttons)

    # ─── 图片 ────────────────────────────────────────────────
    def _load_cached(self) -> None:
        """先显示已缓存的图；缺哪张才联网，缺两张就取两张。"""
        missing = []
        for kind in support_qrcode.KINDS:
            data = support_qrcode.cached_qrcode(kind)
            if data is None:
                missing.append(kind)
                self._labels[kind].setText(tr("正在获取…"))
            else:
                self._show(kind, data)
        if missing:
            self._start(missing)

    def _show(self, kind: str, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self._labels[kind].setText(tr("图片无法显示，请刷新或打开赞助页面"))
            return
        self._labels[kind].setPixmap(pixmap.scaled(
            IMAGE_SIZE,
            IMAGE_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def _start(self, kinds) -> None:
        for kind in kinds:
            if kind in self._threads:
                continue
            thread = ImageRefreshThread(
                kind,
                partial(support_qrcode.refresh, kind),
                QApplication.instance(),
            )
            thread.succeeded.connect(self._on_refreshed)
            thread.error.connect(self._on_error)
            # deleteLater 只挂在 QThread.finished（线程真正退出后）上。
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(partial(self._threads.pop, kind, None))
            self._threads[kind] = thread
            thread.start()

    def _refresh_all(self) -> None:
        self._status_label.setText(tr("正在获取最新二维码…"))
        self._start(support_qrcode.KINDS)

    def _on_refreshed(self, kind: str, data: bytes) -> None:
        self._show(kind, data)
        self._status_label.setText(tr("二维码已更新"))

    def _on_error(self, kind: str, message: str) -> None:
        name = KIND_LABELS.get(kind, kind)
        if support_qrcode.cached_qrcode(kind) is not None:
            message = tr("刷新{name}二维码失败，已保留当前二维码：{error}").format(
                name=name, error=message)
        else:
            # 一张都拿不到时给出可行动的出口：直接打开赞助页，而不是停在错误上。
            message = tr("{name}二维码获取失败：{error}；可点「打开赞助页面」").format(
                name=name, error=message)
        self._status_label.setText(message)

    # ─── 动作 ────────────────────────────────────────────────
    @staticmethod
    def _open_page():
        """在浏览器里打开站点赞助页（与二维码同一处图源）。"""
        QDesktopServices.openUrl(QUrl(SUPPORT_PAGE_URL))
