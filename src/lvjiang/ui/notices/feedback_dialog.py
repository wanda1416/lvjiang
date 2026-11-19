"""反馈与建议对话框。

三件事一起做，缺一条这套渠道就不成立：

1. **反馈 ≠ Bug**：缺陷、功能建议、文档问题、性能、账号同步都走同一条通道，
   类型只是分类标签；类型专属的「复现细节」永远可选，且只对需要它的类型显示。
2. **门槛是邮箱**：回复走邮箱，所以邮箱必填；登录与否只影响作者能不能顺手在
   控制台里看到账号，不影响能不能提交。
3. **证据随手上传**：Ctrl+V 直接贴图（最多 10 张，自动缩边压缩），日志默认勾选
   并自动脱敏、可预览——「让用户自己去两千行日志里遮盖隐私」等于不遮盖。

提交流程两段式（正文 → 逐张附件），失败只影响那一张，见
``lvjiang.core.feedback.client``。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import QSize, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ...core.feedback import (
    DEFAULT_KIND,
    FEEDBACK_KINDS,
    KIND_BY_KEY,
    MAX_SCREENSHOTS,
    collect_environment,
    is_valid_email,
    logtail,
    normalize_email,
)
from ...core.feedback import client as feedback_client
from ...core.feedback import draft as feedback_draft
from ...core.feedback.client import OutgoingAttachment
from ...core.qrcode import group as group_qrcode
from ...core.updates.release import GITHUB_REPO
from ...i18n import tr
from ..widgets.button_styles import apply_button_style
from ..widgets.combo_box import AutoWidthComboBox, ComboWidthMode
from .feedback_attachments import (
    AttachmentError,
    attachment_from_image,
    image_from_mime,
)
from .qrcode_refresher import QrCodeRefresher

ISSUE_GUIDE_URL = (
    f"https://github.com/{GITHUB_REPO}/blob/master/"
    "docs/60-guide/04-desktop/09-faq-and-feedback.md"
)
NEW_ISSUE_URL = f"https://github.com/{GITHUB_REPO}/issues/new"

#: 出现频率的可选值；空串表示「没说」，不强制。
FREQUENCY_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "未说明"),
    ("must", "必现"),
    ("often", "经常"),
    ("rare", "偶发"),
    ("once", "仅一次"),
)

_BODY_PLACEHOLDER = (
    "发生了什么、你原本期望什么。\n"
    "如果是功能建议，直接写你的想法和使用场景就够了。"
)


class GroupQrDialog(QDialog):
    """按需展示交流群二维码的子对话框；支持从 GitHub Pages 刷新最新二维码。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("扫码加群"))
        self.setFixedSize(420, 500)
        self._refresher: QrCodeRefresher | None = None

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
        apply_button_style(self._refresh_btn)
        apply_button_style(close_btn, variant="neutral")
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        if not group_qrcode.qrcode_path().is_file():
            self._refresh_qrcode()

    def _reload_pixmap(self) -> None:
        """从本地磁盘（重新）加载二维码图片。"""
        pixmap = QPixmap(str(group_qrcode.qrcode_path()))
        if pixmap.isNull():
            self._image_label.setText(tr("暂无二维码，请获取最新二维码"))
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

        # 对话框关闭不销毁尚在下载的线程，线程结束后自行释放。
        refresher = QrCodeRefresher(QApplication.instance())
        refresher.succeeded.connect(self._on_refresh_finished)
        refresher.error.connect(self._on_refresh_error)
        # deleteLater 只挂在 QThread.finished（线程真正退出后）上，成功与
        # 失败两条路都会走到它；挂在 succeeded/error 上等于销毁还在跑的线程。
        refresher.finished.connect(refresher.deleteLater)
        refresher.finished.connect(self._on_worker_finished)
        self._refresher = refresher
        refresher.start()

    def _on_refresh_finished(self, _data: bytes) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText(tr("刷新二维码"))
        self._status_label.setText(tr("二维码已更新"))
        self._reload_pixmap()

    def _on_refresh_error(self, message: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText(tr("刷新二维码"))
        if group_qrcode.qrcode_path().is_file():
            message = tr("刷新失败，已保留当前二维码：{error}").format(error=message)
        self._status_label.setText(message)

    def _on_worker_finished(self) -> None:
        self._refresher = None


class FeedbackTextEdit(QPlainTextEdit):
    """描述框：Ctrl+V 贴图时把图片交给对话框当附件，而不是往正文里插一段乱码。"""

    imagePasted = pyqtSignal(QImage)

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802 - Qt API
        return image_from_mime(source) is not None or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt API
        image = image_from_mime(source)
        if image is not None:
            self.imagePasted.emit(image)
            return
        super().insertFromMimeData(source)


class FeedbackSubmitThread(QThread):
    """后台提交（正文 + 逐张附件），信号里不带任何 UI 对象。"""

    # 不能叫 finished：那会遮蔽 QThread.finished（线程真正退出唯一的可靠信号），
    # 而 deleteLater 必须挂在后者上，见 QrCodeRefresher 的同款注释。
    progress = pyqtSignal(int, int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, kwargs: dict, parent=None):
        super().__init__(parent)
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = feedback_client.submit_feedback(
                **self._kwargs, on_progress=self.progress.emit)
        except feedback_client.FeedbackError as error:
            logger.warning("[反馈] 提交失败：{}", error)
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - 线程边界统一转成信号
            logger.exception("[反馈] 提交异常")
            self.failed.emit(tr("提交失败：{error}").format(error=error))
        else:
            self.succeeded.emit(result)


class FeedbackAttachThread(QThread):
    """补传失败的附件（seq 幂等，重跑同一份序列不会堆行）。"""

    progress = pyqtSignal(int, int, str)
    succeeded = pyqtSignal(int, object)
    failed = pyqtSignal(str)

    def __init__(self, feedback_id: str, ticket: str,
                 attachments: list[OutgoingAttachment], parent=None):
        super().__init__(parent)
        self._feedback_id = feedback_id
        self._ticket = ticket
        self._attachments = attachments

    def run(self) -> None:
        try:
            uploaded, failures = feedback_client.upload_attachments(
                self._feedback_id, self._ticket, self._attachments,
                on_progress=self.progress.emit)
        except Exception as error:  # noqa: BLE001 - 线程边界统一转成信号
            logger.exception("[反馈] 附件补传异常")
            self.failed.emit(tr("补传失败：{error}").format(error=error))
        else:
            self.succeeded.emit(uploaded, failures)


class FeedbackDialog(QDialog):
    """提交一条反馈/建议。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("反馈与建议"))
        self.resize(780, 680)
        self.setMinimumSize(720, 600)

        self._shots: list[OutgoingAttachment] = []
        self._excerpt: logtail.LogExcerpt | None = None
        self._thread: QThread | None = None
        self._submitted: feedback_client.SubmitResult | None = None
        self._pending: list[OutgoingAttachment] = []
        self._dirty_hint = ""

        self._build_ui()
        self._load_draft()
        self._reload_log_days()
        self._on_kind_changed()

    # ─── 界面 ───────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel(tr("反馈与建议"))
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(tr(
            "缺陷、功能建议、文档问题、账号同步问题都可以在这里提交；"
            "回复会发到你填写的邮箱。"))
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(10)
        form.setContentsMargins(0, 4, 0, 0)

        self._kind_combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
        for kind in FEEDBACK_KINDS:
            self._kind_combo.addItem(tr(kind.label), kind.key)
        self._kind_combo.setCurrentIndex(
            max(0, self._kind_combo.findData(DEFAULT_KIND)))
        self._kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow(tr("类型"), self._kind_combo)

        self._email_edit = QLineEdit()
        self._email_edit.setPlaceholderText(tr("必填：回复会发到这个邮箱"))
        self._email_edit.setMaxLength(254)
        form.addRow(tr("邮箱"), self._email_edit)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText(tr("一句话说清问题或建议"))
        self._title_edit.setMaxLength(120)
        form.addRow(tr("标题"), self._title_edit)
        layout.addLayout(form)

        self._hint_label = QLabel()
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("color: palette(mid); font-size: 12px;")
        layout.addWidget(self._hint_label)

        body_group = QGroupBox(tr("详细描述"))
        body_layout = QVBoxLayout(body_group)
        self._body_edit = FeedbackTextEdit()
        self._body_edit.setPlaceholderText(_BODY_PLACEHOLDER)
        self._body_edit.setMinimumHeight(130)
        self._body_edit.imagePasted.connect(self._add_pasted_image)
        body_layout.addWidget(self._body_edit)
        layout.addWidget(body_group, 1)

        # 复现细节：只对需要它的类型显示，且**全部可选**——不填也能提交。
        self._detail_group = QGroupBox(tr("复现细节（可选，填了更容易定位）"))
        detail_form = QFormLayout(self._detail_group)
        detail_form.setSpacing(8)
        self._steps_edit = QPlainTextEdit()
        self._steps_edit.setPlaceholderText(tr("依次做了什么，点哪个按钮"))
        self._steps_edit.setFixedHeight(64)
        detail_form.addRow(tr("复现步骤"), self._steps_edit)
        self._expect_edit = QLineEdit()
        self._expect_edit.setPlaceholderText(tr("本来应该是什么样"))
        detail_form.addRow(tr("预期结果"), self._expect_edit)
        self._actual_edit = QLineEdit()
        self._actual_edit.setPlaceholderText(tr("实际是什么样"))
        detail_form.addRow(tr("实际结果"), self._actual_edit)
        self._freq_combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
        for value, label in FREQUENCY_CHOICES:
            self._freq_combo.addItem(tr(label), value)
        detail_form.addRow(tr("出现频率"), self._freq_combo)
        layout.addWidget(self._detail_group)

        layout.addWidget(self._build_attachment_group())
        layout.addWidget(self._build_privacy_label())

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setMinimumHeight(34)
        self._status_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._status_label)

        layout.addLayout(self._build_button_row())

    def _build_attachment_group(self) -> QGroupBox:
        group = QGroupBox(tr("截图与日志"))
        box = QVBoxLayout(group)
        box.setSpacing(8)

        shot_row = QHBoxLayout()
        self._paste_btn = QPushButton(tr("粘贴截图 (Ctrl+V)"))
        self._paste_btn.clicked.connect(self._paste_from_clipboard)
        self._pick_btn = QPushButton(tr("选择图片…"))
        self._pick_btn.clicked.connect(self._pick_images)
        self._remove_btn = QPushButton(tr("移除所选"))
        self._remove_btn.clicked.connect(self._remove_selected_shot)
        apply_button_style(self._paste_btn, self._pick_btn)
        apply_button_style(self._remove_btn, variant="neutral")
        shot_row.addWidget(self._paste_btn)
        shot_row.addWidget(self._pick_btn)
        shot_row.addWidget(self._remove_btn)
        shot_row.addStretch()
        self._shot_count_label = QLabel()
        self._shot_count_label.setStyleSheet("color: palette(mid); font-size: 12px;")
        shot_row.addWidget(self._shot_count_label)
        box.addLayout(shot_row)

        self._shot_list = QListWidget()
        self._shot_list.setViewMode(QListWidget.ViewMode.IconMode)
        self._shot_list.setIconSize(QSize(96, 54))
        self._shot_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._shot_list.setMovement(QListWidget.Movement.Static)
        self._shot_list.setFixedHeight(104)
        self._shot_list.itemDoubleClicked.connect(lambda _item: self._view_selected_shot())
        box.addWidget(self._shot_list)

        log_row = QHBoxLayout()
        self._log_check = QCheckBox(tr("附加最近日志"))
        self._log_check.setChecked(True)
        self._log_check.toggled.connect(self._refresh_log_state)
        self._log_day_combo = AutoWidthComboBox(width_mode=ComboWidthMode.POPUP)
        self._log_day_combo.currentIndexChanged.connect(self._refresh_log_state)
        self._log_preview_btn = QPushButton(tr("预览日志"))
        self._log_preview_btn.clicked.connect(self._preview_log)
        apply_button_style(self._log_preview_btn)
        self._log_size_label = QLabel()
        self._log_size_label.setStyleSheet("color: palette(mid); font-size: 12px;")
        log_row.addWidget(self._log_check)
        log_row.addWidget(self._log_day_combo)
        log_row.addWidget(self._log_preview_btn)
        log_row.addWidget(self._log_size_label)
        log_row.addStretch()
        box.addLayout(log_row)
        return group

    def _build_privacy_label(self) -> QLabel:
        label = QLabel(tr(
            "日志会自动遮盖本机用户目录、令牌、设备序号与匿名标识；截图里的账号、"
            "角色名、ADB 序列号需要你自己确认遮挡。提交前可以用「预览」核对全部内容。"))
        label.setWordWrap(True)
        label.setStyleSheet("color: #b06a00; font-size: 12px;")
        return label

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._guide_btn = QPushButton(tr("详细规范"))
        self._guide_btn.clicked.connect(self._open_issue_guide)
        self._group_btn = QPushButton(tr("扫码加群"))
        self._group_btn.clicked.connect(self._open_group_qr)
        self._github_btn = QPushButton(tr("GitHub 反馈"))
        self._github_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(NEW_ISSUE_URL)))
        apply_button_style(self._guide_btn, self._group_btn, self._github_btn,
                           variant="neutral")
        row.addWidget(self._guide_btn)
        row.addWidget(self._group_btn)
        row.addWidget(self._github_btn)
        row.addStretch()

        self._preview_btn = QPushButton(tr("预览"))
        self._preview_btn.clicked.connect(self._preview_payload)
        self._retry_btn = QPushButton(tr("补传附件"))
        self._retry_btn.clicked.connect(self._retry_attachments)
        self._retry_btn.setVisible(False)
        self._submit_btn = QPushButton(tr("提交"))
        self._submit_btn.setDefault(True)
        self._submit_btn.clicked.connect(self._submit)
        self._close_btn = QPushButton(tr("关闭"))
        self._close_btn.clicked.connect(self.reject)
        apply_button_style(self._preview_btn, self._retry_btn, self._submit_btn)
        apply_button_style(self._close_btn, variant="neutral")
        row.addWidget(self._preview_btn)
        row.addWidget(self._retry_btn)
        row.addWidget(self._submit_btn)
        row.addWidget(self._close_btn)
        return row

    # ─── 类型 ───────────────────────────────────────────

    def _kind_key(self) -> str:
        value = self._kind_combo.currentData()
        return value if isinstance(value, str) and value in KIND_BY_KEY else DEFAULT_KIND

    def _on_kind_changed(self, *_args) -> None:
        kind = KIND_BY_KEY[self._kind_key()]
        # 建议类不显示复现细节：不能用 Bug 的表格把「提建议」变成填表。
        self._detail_group.setVisible(kind.detail_fields)
        self._hint_label.setText(tr(kind.evidence_hint))

    # ─── 截图 ───────────────────────────────────────────

    def _add_pasted_image(self, image: QImage) -> None:
        self._append_image(image)

    def _paste_from_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        image = clipboard.image() if clipboard is not None else QImage()
        if image.isNull():
            self._set_status(tr("剪贴板里没有图片"), error=True)
            return
        self._append_image(image)

    def _pick_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("选择图片"), "", tr("图片 (*.png *.jpg *.jpeg *.webp)"))
        for path in paths:
            image = QImage(path)
            if image.isNull():
                self._set_status(tr("无法读取图片：{name}").format(
                    name=path.replace("\\", "/").rsplit("/", 1)[-1]), error=True)
                continue
            self._append_image(image)

    def _append_image(self, image: QImage) -> None:
        if len(self._shots) >= MAX_SCREENSHOTS:
            self._set_status(tr("最多附加 {n} 张截图").format(n=MAX_SCREENSHOTS),
                             error=True)
            return
        try:
            attachment = attachment_from_image(image, index=len(self._shots) + 1)
        except AttachmentError as error:
            self._set_status(str(error), error=True)
            return
        self._shots.append(attachment)
        self._refresh_shot_list()
        self._set_status(tr("已附加 1 张截图（已压缩到 {kb} KB）").format(
            kb=max(1, len(attachment.data) // 1024)))

    def _refresh_shot_list(self) -> None:
        self._shot_list.clear()
        for attachment in self._shots:
            pixmap = QPixmap()
            pixmap.loadFromData(attachment.data)
            text = f"{attachment.name}\n{max(1, len(attachment.data) // 1024)} KB"
            if pixmap.isNull():
                item = QListWidgetItem(text)
            else:
                icon = QIcon(pixmap.scaled(
                    96, 54, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                item = QListWidgetItem(icon, text)
            item.setData(Qt.ItemDataRole.UserRole, attachment.name)
            self._shot_list.addItem(item)
        self._shot_count_label.setText(
            tr("{n}/{limit} 张").format(n=len(self._shots), limit=MAX_SCREENSHOTS))

    def _remove_selected_shot(self) -> None:
        row = self._shot_list.currentRow()
        if row < 0 or row >= len(self._shots):
            return
        del self._shots[row]
        self._refresh_shot_list()

    def _view_selected_shot(self) -> None:
        row = self._shot_list.currentRow()
        if row < 0 or row >= len(self._shots):
            return
        pixmap = QPixmap()
        pixmap.loadFromData(self._shots[row].data)
        if pixmap.isNull():
            return
        viewer = QDialog(self)
        viewer.setWindowTitle(tr("截图预览"))
        box = QVBoxLayout(viewer)
        label = QLabel()
        label.setPixmap(pixmap.scaled(
            820, 620, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        box.addWidget(label)
        viewer.exec()

    # ─── 日志 ───────────────────────────────────────────

    def _reload_log_days(self) -> None:
        self._log_day_combo.blockSignals(True)
        self._log_day_combo.clear()
        days = logtail.available_days()
        for day in days:
            self._log_day_combo.addItem(f"{day:%Y-%m-%d}", day)
        self._log_day_combo.blockSignals(False)
        if not days:
            self._log_check.setChecked(False)
            self._log_check.setEnabled(False)
            self._log_size_label.setText(tr("没有找到日志文件"))
            return
        # 选项装好后必须自己算一次：combo 的 currentIndexChanged 在装配期被
        # blockSignals 挡掉了，不主动刷新会让「附加最近日志」勾着却什么都没附。
        self._refresh_log_state()

    def _selected_log_day(self):
        value = self._log_day_combo.currentData()
        return value if hasattr(value, "year") else None

    def _refresh_log_state(self, *_args) -> None:
        if not self._log_check.isChecked() or not self._log_check.isEnabled():
            self._excerpt = None
            self._log_preview_btn.setEnabled(False)
            if self._log_check.isEnabled():
                self._log_size_label.setText("")
            return
        self._excerpt = logtail.read_excerpt(self._selected_log_day())
        self._log_preview_btn.setEnabled(self._excerpt is not None)
        if self._excerpt is None:
            self._log_check.setChecked(False)
            self._set_status(tr("这一天没有可用的日志文件"), error=True)
            return
        size_kb = max(1, len(self._excerpt.text.encode("utf-8")) // 1024)
        suffix = tr("，已截断") if self._excerpt.truncated else ""
        self._log_size_label.setText(tr("约 {kb} KB（已脱敏{suffix}）").format(
            kb=size_kb, suffix=suffix))

    def _preview_log(self) -> None:
        if self._excerpt is None:
            return
        self._show_text_dialog(tr("日志预览（将随反馈提交）"), self._excerpt.text)

    # ─── 校验与提交 ─────────────────────────────────────

    def _fields(self) -> dict:
        fields: dict[str, str] = {}
        # 按类型元数据判断，不看控件可见性：可见性是展示问题，对话框尚未 show
        # 时 isVisible() 恒为 False，用它做判断会把用户填好的细节静默丢掉。
        if KIND_BY_KEY[self._kind_key()].detail_fields:
            steps = self._steps_edit.toPlainText().strip()
            expect = self._expect_edit.text().strip()
            actual = self._actual_edit.text().strip()
            frequency = self._freq_combo.currentData()
            if steps:
                fields["steps"] = steps
            if expect:
                fields["expected"] = expect
            if actual:
                fields["actual"] = actual
            if isinstance(frequency, str) and frequency:
                fields["frequency"] = frequency
        return fields

    def _attachments(self) -> list[OutgoingAttachment]:
        items = list(self._shots)
        if self._excerpt is not None and self._log_check.isChecked():
            items.append(OutgoingAttachment(
                name=self._excerpt.name, kind="log", mime="text/plain",
                data=self._excerpt.text.encode("utf-8")))
        return items

    def _host_extras(self) -> dict:
        """主窗口愿意提供的定位信息（布局名）。取不到就算了，不逼宿主实现接口。"""
        extra: dict[str, str] = {}
        host = self.parent()
        manager = getattr(host, "_layout_manager", None)
        if manager is not None:
            try:
                key = manager.get_active_layout_key()
            except Exception:  # noqa: BLE001 - 环境信息缺失不影响提交
                key = ""
            if isinstance(key, str) and key:
                extra["layout"] = key
        return extra

    def _validate(self) -> str:
        if not is_valid_email(normalize_email(self._email_edit.text())):
            return tr("请填写能收到回复的邮箱")
        if not self._title_edit.text().strip():
            return tr("请填写标题")
        if not self._body_edit.toPlainText().strip():
            return tr("请填写详细描述")
        return ""

    def _submit(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        if self._submitted is not None:
            return
        problem = self._validate()
        if problem:
            self._set_status(problem, error=True)
            return
        self._pending = self._attachments()
        kwargs = {
            "kind": self._kind_key(),
            "title": self._title_edit.text().strip(),
            "body": self._body_edit.toPlainText().strip(),
            "email": normalize_email(self._email_edit.text()),
            "fields": self._fields(),
            "env": collect_environment(extra=self._host_extras()),
            "attachments": list(self._pending),
        }
        self._set_busy(True)
        self._set_status(tr("正在提交…"))
        thread = FeedbackSubmitThread(kwargs, self)
        thread.progress.connect(self._on_progress)
        thread.succeeded.connect(self._on_submitted)
        thread.failed.connect(self._on_failure)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        thread.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        if total <= 0:
            return
        if name:
            self._set_status(tr("正在上传附件 {done}/{total}：{name}").format(
                done=done + 1, total=total, name=name))
        else:
            self._set_status(tr("正文已提交，附件完成 {done}/{total}").format(
                done=done, total=total))

    def _on_submitted(self, result: feedback_client.SubmitResult) -> None:
        self._submitted = result
        feedback_draft.clear_draft()
        self._set_busy(False)
        self._submit_btn.setEnabled(False)
        if result.complete:
            self._retry_btn.setVisible(False)
            self._set_status(tr("已提交，编号 {id}。回复会发到 {email}。").format(
                id=result.id, email=normalize_email(self._email_edit.text())))
            return
        failed = "、".join(item.name for item in result.failures)
        self._retry_btn.setVisible(True)
        self._set_status(tr(
            "正文已提交，编号 {id}；{n} 个附件没传上去（{names}）。"
            "可以点「补传附件」重试，正文不会重复提交。").format(
            id=result.id, n=len(result.failures), names=failed), error=True)

    def _retry_attachments(self) -> None:
        if self._submitted is None or not self._pending:
            return
        if self._thread is not None and self._thread.isRunning():
            return
        self._retry_btn.setEnabled(False)
        self._set_status(tr("正在补传附件…"))
        thread = FeedbackAttachThread(self._submitted.id, self._submitted.ticket,
                                      list(self._pending), self)
        thread.progress.connect(self._on_progress)
        thread.succeeded.connect(self._on_retried)
        thread.failed.connect(self._on_failure)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        thread.start()

    def _on_retried(self, uploaded: int, failures: tuple) -> None:
        self._retry_btn.setEnabled(True)
        if not failures and self._submitted is not None:
            self._submitted = feedback_client.SubmitResult(
                id=self._submitted.id, ticket=self._submitted.ticket,
                uploaded=uploaded, declared=self._submitted.declared, failures=())
            self._retry_btn.setVisible(False)
            self._set_status(tr("附件已全部传上去，编号 {id}。").format(
                id=self._submitted.id))
            return
        names = "、".join(item.name for item in failures)
        self._set_status(tr("还有 {n} 个附件没传上去：{names}").format(
            n=len(failures), names=names), error=True)

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        self._retry_btn.setEnabled(True)
        self._set_status(message, error=True)

    def _on_thread_finished(self) -> None:
        self._thread = None
        if self._submitted is None:
            self._submit_btn.setEnabled(True)

    def _set_busy(self, busy: bool) -> None:
        for widget in (self._submit_btn, self._preview_btn, self._paste_btn,
                       self._pick_btn, self._remove_btn, self._log_check,
                       self._log_day_combo, self._kind_combo):
            widget.setEnabled(not busy)
        if not busy:
            self._submit_btn.setEnabled(self._submitted is None)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            "color: #c0392b;" if error else "color: palette(mid);")

    # ─── 预览 ───────────────────────────────────────────

    def _preview_payload(self) -> None:
        """把「将要上传的全部内容」摊开给用户看——这是这套渠道的信任基础。"""
        self._show_text_dialog(tr("将要提交的内容"), self._payload_preview_text())

    def _payload_preview_text(self) -> str:
        """预览正文。独立成函数，测试才能核对「到底会发出去什么」。"""
        kind = KIND_BY_KEY[self._kind_key()]
        lines = [
            tr("类型：{label}（{key}）").format(label=tr(kind.label), key=kind.key),
            tr("标题：{text}").format(text=self._title_edit.text().strip() or tr("（空）")),
            tr("邮箱：{text}").format(
                text=normalize_email(self._email_edit.text()) or tr("（空）")),
            "",
            tr("详细描述："),
            self._body_edit.toPlainText().strip() or tr("（空）"),
            "",
            tr("复现细节："),
        ]
        fields = self._fields()
        if fields:
            lines.extend(f"  {key}: {value}" for key, value in fields.items())
        else:
            lines.append(tr("  （无）"))
        lines.extend([
            "",
            tr("环境信息（自动采集，不含用户名/角色名/路径）："),
            *(f"  {key}: {value}" for key, value
              in collect_environment(extra=self._host_extras()).items()),
            "",
            tr("附件："),
        ])
        attachments = self._attachments()
        if not attachments:
            lines.append(tr("  （无）"))
        for attachment in attachments:
            lines.append(f"  {attachment.name}（{attachment.kind}, "
                         f"{max(1, len(attachment.data) // 1024)} KB）")
        if self._excerpt is not None and self._log_check.isChecked():
            lines.extend(["", tr("日志片段（前 40 行，已脱敏）："),
                          *self._excerpt.text.splitlines()[:40]])
        return "\n".join(lines)

    def _show_text_dialog(self, title: str, text: str) -> None:
        viewer = QDialog(self)
        viewer.setWindowTitle(title)
        viewer.resize(720, 560)
        box = QVBoxLayout(viewer)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        box.addWidget(view)
        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton(tr("关闭"))
        close_btn.clicked.connect(viewer.accept)
        apply_button_style(close_btn, variant="neutral")
        row.addWidget(close_btn)
        box.addLayout(row)
        viewer.exec()

    # ─── 草稿 ───────────────────────────────────────────

    def _dirty(self) -> bool:
        return any((
            self._title_edit.text().strip(),
            self._body_edit.toPlainText().strip(),
            self._email_edit.text().strip(),
            self._steps_edit.toPlainText().strip(),
            self._expect_edit.text().strip(),
            self._actual_edit.text().strip(),
        ))

    def _save_draft(self) -> None:
        if self._submitted is not None or not self._dirty():
            return
        feedback_draft.save_draft({
            "kind": self._kind_key(),
            "email": self._email_edit.text().strip(),
            "title": self._title_edit.text(),
            "body": self._body_edit.toPlainText(),
            "steps": self._steps_edit.toPlainText(),
            "expected": self._expect_edit.text(),
            "actual": self._actual_edit.text(),
            "frequency": self._freq_combo.currentData() or "",
        })

    def _load_draft(self) -> None:
        data = feedback_draft.load_draft()
        if not data:
            return
        index = self._kind_combo.findData(data.get("kind") or DEFAULT_KIND)
        if index >= 0:
            self._kind_combo.setCurrentIndex(index)
        self._email_edit.setText(str(data.get("email") or ""))
        self._title_edit.setText(str(data.get("title") or ""))
        self._body_edit.setPlainText(str(data.get("body") or ""))
        self._steps_edit.setPlainText(str(data.get("steps") or ""))
        self._expect_edit.setText(str(data.get("expected") or ""))
        self._actual_edit.setText(str(data.get("actual") or ""))
        freq_index = self._freq_combo.findData(data.get("frequency") or "")
        if freq_index >= 0:
            self._freq_combo.setCurrentIndex(freq_index)
        self._dirty_hint = tr("已恢复上次未提交的草稿（截图需要重新粘贴），"
                              "提交成功后草稿自动清除。")

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if self._dirty_hint:
            self._set_status(self._dirty_hint)
            self._dirty_hint = ""

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._set_status(tr("正在提交，请稍候…"))
            return
        self._save_draft()
        super().reject()

    # ─── 其它渠道 ───────────────────────────────────────

    def _open_group_qr(self) -> None:
        """用户主动请求时展示交流群二维码。"""
        GroupQrDialog(self).exec()

    @staticmethod
    def _open_issue_guide() -> None:
        """打开用户指南里的反馈规范（本对话框唯一的规范入口）。"""
        QDesktopServices.openUrl(QUrl(ISSUE_GUIDE_URL))
