"""公告中心：展示缓存/远端公告，并支持手动刷新。"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.announcement import (
    Announcement,
    AnnouncementChecker,
    AnnouncementFetchResult,
    AnnouncementManifest,
    cache_manifest,
)
from ..i18n import tr

_LEVEL_LABELS = {
    "critical": "严重",
    "warning": "警告",
    "info": "通知",
}


class AnnouncementDialog(QDialog):
    """专用公告查看窗口；手动入口可刷新，启动提示只展示匹配项。"""

    def __init__(
        self,
        manifest: AnnouncementManifest | None = None,
        notices: tuple[Announcement, ...] | None = None,
        parent=None,
        *,
        allow_refresh: bool = True,
    ):
        super().__init__(parent)
        self._manifest = manifest
        self._notices_override = notices
        self._checker: AnnouncementChecker | None = None
        self._setup_ui(allow_refresh)
        self.set_manifest(manifest, notices)

    @property
    def manifest(self) -> AnnouncementManifest | None:
        return self._manifest

    def _setup_ui(self, allow_refresh: bool) -> None:
        self.setWindowTitle(tr("公告"))
        self.setMinimumSize(700, 460)
        self.resize(820, 540)

        layout = QVBoxLayout(self)
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: gray;")
        layout.addWidget(self._status_label)

        splitter = QSplitter()
        self._list = QListWidget()
        self._list.setMinimumWidth(230)
        self._list.currentRowChanged.connect(self._show_notice)
        splitter.addWidget(self._list)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(8, 0, 0, 0)
        self._title_label = QLabel()
        self._title_label.setWordWrap(True)
        self._title_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        detail_layout.addWidget(self._title_label)
        self._meta_label = QLabel()
        self._meta_label.setStyleSheet("color: gray;")
        detail_layout.addWidget(self._meta_label)
        self._body = QTextBrowser()
        self._body.setOpenExternalLinks(False)
        self._body.anchorClicked.connect(self._open_body_link)
        detail_layout.addWidget(self._body, 1)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self._refresh_btn = QPushButton(tr("重新获取"))
        self._refresh_btn.clicked.connect(self.refresh)
        self._refresh_btn.setVisible(allow_refresh)
        buttons.addWidget(self._refresh_btn)
        self._details_btn = QPushButton(tr("查看详情"))
        self._details_btn.clicked.connect(self._open_details)
        buttons.addWidget(self._details_btn)
        buttons.addStretch()
        close_btn = QPushButton(tr("关闭"))
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def set_manifest(
        self,
        manifest: AnnouncementManifest | None,
        notices: tuple[Announcement, ...] | None = None,
    ) -> None:
        """替换窗口内容；``notices`` 用于启动时只展示匹配公告。"""
        self._manifest = manifest
        self._notices_override = notices
        self._list.clear()

        if manifest is None:
            self._status_label.setText(tr("尚未获取到公告，点击“重新获取”进行同步。"))
            self._clear_detail(tr("暂无公告"))
            return

        shown = notices if notices is not None else tuple(
            notice for notice in manifest.notices if notice.active)
        updated = self._format_date(manifest.updated_at)
        status = tr("公告版本 {version}").format(version=manifest.notice_version)
        if updated:
            status += " · " + tr("更新于 {date}").format(date=updated)
        self._status_label.setText(status)

        for notice in shown:
            level = tr(_LEVEL_LABELS.get(notice.level, "通知"))
            item = QListWidgetItem(f"[{level}] {notice.title}")
            item.setData(0x0100, notice)
            self._list.addItem(item)

        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._clear_detail(tr("暂无公告"))

    def _clear_detail(self, message: str) -> None:
        self._title_label.setText(message)
        self._meta_label.clear()
        self._body.clear()
        self._details_btn.setEnabled(False)

    def _show_notice(self, row: int) -> None:
        item = self._list.item(row)
        if item is None:
            self._clear_detail(tr("暂无公告"))
            return
        notice = item.data(0x0100)
        if not isinstance(notice, Announcement):
            self._clear_detail(tr("暂无公告"))
            return
        self._title_label.setText(notice.title)
        level = tr(_LEVEL_LABELS.get(notice.level, "通知"))
        date = self._format_date(notice.published_at)
        self._meta_label.setText(" · ".join(part for part in (level, date) if part))
        self._body.setMarkdown(notice.body)
        self._details_btn.setEnabled(bool(notice.url))

    def _selected_notice(self) -> Announcement | None:
        item = self._list.currentItem()
        if item is None:
            return None
        notice = item.data(0x0100)
        return notice if isinstance(notice, Announcement) else None

    def _open_details(self) -> None:
        notice = self._selected_notice()
        if notice is not None and notice.url:
            QDesktopServices.openUrl(QUrl(notice.url))

    @staticmethod
    def _open_body_link(url: QUrl) -> None:
        """正文中的链接也只允许用户主动打开 HTTPS 地址。"""
        if url.scheme().lower() == "https" and url.host():
            QDesktopServices.openUrl(url)

    def refresh(self) -> None:
        """异步刷新公告；失败时保留现有缓存内容。"""
        if self._checker is not None and self._checker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText(tr("获取中..."))
        self._status_label.setText(tr("正在获取公告..."))
        checker = AnnouncementChecker(self)
        checker.finished.connect(self._on_refresh_finished)
        checker.error.connect(self._on_refresh_error)
        checker.finished.connect(checker.deleteLater)
        checker.error.connect(checker.deleteLater)
        self._checker = checker
        checker.start()

    def _on_refresh_finished(self, result: AnnouncementFetchResult) -> None:
        self._checker = None
        cache_manifest(result.manifest, result.etag)
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText(tr("重新获取"))
        self.set_manifest(result.manifest)

    def _on_refresh_error(self, message: str) -> None:
        self._checker = None
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText(tr("重新获取"))
        if self._manifest is None:
            self._status_label.setText(message)
        else:
            self._status_label.setText(tr("获取失败，当前显示上次缓存：{error}").format(
                error=message))

    @staticmethod
    def _format_date(value: str) -> str:
        if not value:
            return ""
        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value
