"""Unified user avatar cropper and saved-avatar library."""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QPointF,
    QRectF,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.user_avatars import UserAvatarStore
from ..core.user_config import UserConfigManager
from ..i18n import tr
from .button_styles import apply_button_style, fit_button_width

MAX_SOURCE_PIXELS = 40_000_000


class AvatarCropCanvas(QWidget):
    """Fixed square viewport; users pan and zoom the image underneath it."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 380)
        self._image = QImage()
        self._zoom = 1.0
        self._offset = QPointF()
        self._drag_origin: QPointF | None = None
        self._offset_origin = QPointF()
        self.setStyleSheet("background: #20242b; border-radius: 8px;")

    def has_image(self) -> bool:
        return not self._image.isNull()

    def set_image(self, image: QImage) -> None:
        self._image = image.convertToFormat(QImage.Format.Format_ARGB32)
        self.reset_view()

    def clear_image(self) -> None:
        self._image = QImage()
        self._zoom = 1.0
        self._offset = QPointF()
        self.changed.emit()
        self.update()

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._offset = QPointF()
        self.changed.emit()
        self.update()

    def set_zoom_percent(self, value: int) -> None:
        self._zoom = max(1.0, min(4.0, value / 100.0))
        self._clamp_offset()
        self.changed.emit()
        self.update()

    def _crop_rect(self) -> QRectF:
        side = max(1.0, min(self.width(), self.height()) - 36.0)
        return QRectF(
            (self.width() - side) / 2,
            (self.height() - side) / 2,
            side,
            side,
        )

    def _base_scale(self) -> float:
        if self._image.isNull():
            return 1.0
        crop = self._crop_rect()
        return max(crop.width() / self._image.width(),
                   crop.height() / self._image.height())

    def _display_rect(self) -> QRectF:
        crop = self._crop_rect()
        scale = self._base_scale() * self._zoom
        width = self._image.width() * scale
        height = self._image.height() * scale
        center = crop.center() + self._offset
        return QRectF(center.x() - width / 2, center.y() - height / 2,
                      width, height)

    def _clamp_offset(self) -> None:
        if self._image.isNull():
            self._offset = QPointF()
            return
        crop = self._crop_rect()
        scale = self._base_scale() * self._zoom
        excess_x = max(0.0, (self._image.width() * scale - crop.width()) / 2)
        excess_y = max(0.0, (self._image.height() * scale - crop.height()) / 2)
        self._offset.setX(max(-excess_x, min(excess_x, self._offset.x())))
        self._offset.setY(max(-excess_y, min(excess_y, self._offset.y())))

    def cropped_image(self, size: int = 512) -> QImage:
        if self._image.isNull():
            return QImage()
        crop = self._crop_rect()
        shown = self._display_rect()
        scale = shown.width() / self._image.width()
        source = QRectF(
            (crop.left() - shown.left()) / scale,
            (crop.top() - shown.top()) / scale,
            crop.width() / scale,
            crop.height() / scale,
        ).intersected(QRectF(self._image.rect()))
        copied = self._image.copy(source.toAlignedRect())
        return copied.scaled(
            size, size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def png_bytes(self) -> bytes:
        image = self.cropped_image()
        if image.isNull():
            return b""
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            return b""
        return bytes(payload.data())

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self.has_image()
                and self._crop_rect().contains(event.position())):
            self._drag_origin = event.position()
            self._offset_origin = QPointF(self._offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if self._drag_origin is not None:
            self._offset = self._offset_origin + event.position() - self._drag_origin
            self._clamp_offset()
            self.changed.emit()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if self._drag_origin is not None:
            self._drag_origin = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.has_image():
            return super().wheelEvent(event)
        step = 0.1 if event.angleDelta().y() > 0 else -0.1
        self._zoom = max(1.0, min(4.0, self._zoom + step))
        self._clamp_offset()
        self.changed.emit()
        self.update()
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._clamp_offset()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#20242b"))
        crop = self._crop_rect()

        if self._image.isNull():
            painter.setPen(QColor("#a9b1bd"))
            painter.drawText(
                crop,
                Qt.AlignmentFlag.AlignCenter,
                tr("导入图片或从剪贴板粘贴"),
            )
            return

        shown = self._display_rect()
        painter.save()
        painter.setClipRect(crop)
        painter.drawImage(shown, self._image)
        painter.restore()

        overlay = QColor(0, 0, 0, 125)
        painter.fillRect(QRectF(0, 0, self.width(), crop.top()), overlay)
        painter.fillRect(QRectF(0, crop.bottom(), self.width(),
                                self.height() - crop.bottom()), overlay)
        painter.fillRect(QRectF(0, crop.top(), crop.left(), crop.height()), overlay)
        painter.fillRect(QRectF(crop.right(), crop.top(),
                                self.width() - crop.right(), crop.height()), overlay)

        painter.setPen(QPen(QColor(255, 255, 255, 215), 2))
        painter.drawRect(crop)
        painter.setPen(QPen(QColor(255, 255, 255, 110), 1))
        for fraction in (1 / 3, 2 / 3):
            x = crop.left() + crop.width() * fraction
            y = crop.top() + crop.height() * fraction
            painter.drawLine(QPointF(x, crop.top()), QPointF(x, crop.bottom()))
            painter.drawLine(QPointF(crop.left(), y), QPointF(crop.right(), y))


class AvatarEditorDialog(QDialog):
    """Import/paste cropper and shared saved-avatar picker."""

    avatar_changed = pyqtSignal(str, str)

    def __init__(
        self,
        username: str,
        user_manager: UserConfigManager,
        parent=None,
        *,
        store: UserAvatarStore | None = None,
        screenshot_callback: Callable[[], object] | None = None,
    ):
        super().__init__(parent)
        self._username = username
        self._user_manager = user_manager
        self._store = store or UserAvatarStore()
        self._screenshot_callback = screenshot_callback
        user = user_manager.get_user(username)
        self._current_filename = user.avatar if user is not None else ""
        self._selected_filename = ""
        self._draft_mode = False
        self.setWindowTitle(tr("编辑“{name}”的头像").format(name=username))
        self.setMinimumSize(900, 610)
        self.resize(980, 680)
        self._setup_ui()
        self._load_library()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(12)

        toolbar = QHBoxLayout()
        self._import_button = QPushButton(tr("导入图片"))
        self._import_button.clicked.connect(self._import_image)
        toolbar.addWidget(self._import_button)
        self._paste_button = QPushButton(tr("从剪贴板粘贴"))
        self._paste_button.clicked.connect(self._paste_image)
        toolbar.addWidget(self._paste_button)
        self._screenshot_button = QPushButton(tr("刷新截图"))
        self._screenshot_button.setToolTip(tr("从当前设备或窗口获取最新截图"))
        self._screenshot_button.setEnabled(self._screenshot_callback is not None)
        self._screenshot_button.clicked.connect(self._refresh_screenshot)
        toolbar.addWidget(self._screenshot_button)
        apply_button_style(
            self._import_button,
            self._paste_button,
            self._screenshot_button,
            variant="action",
        )
        fit_button_width(
            self._import_button,
            self._paste_button,
            self._screenshot_button,
            minimum=112,
        )
        self._import_button.setMinimumHeight(34)
        self._paste_button.setMinimumHeight(34)
        self._screenshot_button.setMinimumHeight(34)
        toolbar.addStretch()
        hint = QLabel(tr("可拖动图片定位，滚轮或滑块缩放 · Ctrl+V 粘贴"))
        hint.setStyleSheet("color: palette(mid);")
        toolbar.addWidget(hint)
        root.addLayout(toolbar)

        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, stretch=1)

        editor_panel = QVBoxLayout()
        self._canvas = AvatarCropCanvas()
        self._canvas.changed.connect(self._sync_zoom_from_canvas)
        editor_panel.addWidget(self._canvas, stretch=1)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel(tr("缩放")))
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(100, 400)
        self._zoom_slider.setValue(100)
        self._zoom_slider.valueChanged.connect(self._canvas.set_zoom_percent)
        zoom_row.addWidget(self._zoom_slider, stretch=1)
        self._reset_button = QPushButton(tr("重置"))
        self._reset_button.clicked.connect(self._reset_crop)
        apply_button_style(self._reset_button, variant="neutral")
        self._reset_button.setMinimumSize(72, 32)
        zoom_row.addWidget(self._reset_button)
        editor_panel.addLayout(zoom_row)
        body.addLayout(editor_panel, stretch=3)

        library_card = QFrame()
        library_card.setFrameShape(QFrame.Shape.StyledPanel)
        library_card.setMinimumWidth(290)
        library_layout = QVBoxLayout(library_card)
        title = QLabel(tr("头像库"))
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        library_layout.addWidget(title)
        subtitle = QLabel(tr("选择之前保存的头像，或导入一张新图片"))
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: palette(mid);")
        library_layout.addWidget(subtitle)
        self._library = QListWidget()
        self._library.setViewMode(QListWidget.ViewMode.IconMode)
        self._library.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._library.setMovement(QListWidget.Movement.Static)
        self._library.setIconSize(QSize(76, 76))
        self._library.setGridSize(QSize(94, 106))
        self._library.itemClicked.connect(self._select_library_item)
        self._library.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._library.customContextMenuRequested.connect(
            self._show_library_context_menu)
        library_layout.addWidget(self._library, stretch=1)
        body.addWidget(library_card, stretch=2)

        footer = QHBoxLayout()
        self._remove_button = QPushButton(tr("移除当前头像"))
        self._remove_button.setEnabled(bool(self._current_filename))
        self._remove_button.clicked.connect(self._remove_avatar)
        apply_button_style(self._remove_button, variant="danger")
        self._remove_button.setMinimumHeight(36)
        footer.addWidget(self._remove_button)
        footer.addStretch()
        self._cancel_button = QPushButton(tr("取消"))
        self._cancel_button.clicked.connect(self.reject)
        apply_button_style(self._cancel_button, variant="neutral")
        self._cancel_button.setMinimumHeight(36)
        footer.addWidget(self._cancel_button)
        self._save_button = QPushButton(tr("保存并使用"))
        self._save_button.setDefault(True)
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self._save)
        apply_button_style(self._save_button, variant="action")
        self._save_button.setMinimumHeight(36)
        fit_button_width(self._cancel_button, self._save_button, minimum=112)
        footer.addWidget(self._save_button)
        root.addLayout(footer)

    def _sync_zoom_from_canvas(self) -> None:
        value = round(self._canvas._zoom * 100)  # canvas is this dialog's private child
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(value)
        self._zoom_slider.blockSignals(False)

    def _reset_crop(self) -> None:
        self._canvas.reset_view()
        self._zoom_slider.setValue(100)

    def _load_library(self) -> None:
        self._library.clear()
        current_item = None
        for filename in self._store.list_filenames():
            path = self._store.path_for(filename)
            if path is None:
                continue
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                continue
            label = tr("当前使用") if filename == self._current_filename else ""
            icon = QIcon(pixmap.scaled(
                76, 76,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            ))
            item = QListWidgetItem(icon, label)
            item.setData(Qt.ItemDataRole.UserRole, filename)
            item.setToolTip(filename)
            self._library.addItem(item)
            if filename == self._current_filename:
                current_item = item
        if current_item is not None:
            self._library.setCurrentItem(current_item)
            self._select_library_item(current_item)

    def _set_source_image(self, image: QImage) -> None:
        if image.isNull():
            QMessageBox.warning(self, tr("导入失败"), tr("没有读取到有效图片"))
            return
        if image.width() * image.height() > MAX_SOURCE_PIXELS:
            QMessageBox.warning(
                self, tr("导入失败"), tr("图片尺寸过大，请选择不超过 4000 万像素的图片"))
            return
        self._library.clearSelection()
        self._selected_filename = ""
        self._draft_mode = True
        self._canvas.set_image(image)
        self._save_button.setText(tr("保存并使用"))
        self._save_button.setEnabled(True)

    def _import_image(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            tr("导入头像图片"),
            "",
            tr("图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)"),
        )
        if not filename:
            return
        reader = QImageReader(filename)
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            QMessageBox.warning(
                self, tr("导入失败"),
                tr("无法读取图片：{error}").format(error=reader.errorString()),
            )
            return
        self._set_source_image(image)

    def _paste_image(self) -> None:
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard is None:
            QMessageBox.information(self, tr("无法粘贴"), tr("剪贴板中没有图片"))
            return
        image = clipboard.image()
        if image.isNull():
            QMessageBox.information(self, tr("无法粘贴"), tr("剪贴板中没有图片"))
            return
        self._set_source_image(image)

    @staticmethod
    def _capture_to_qimage(frame: object) -> QImage:
        """Convert a capture callback's BGR/BGRA frame into an owned QImage."""
        if isinstance(frame, QImage):
            return frame.copy()
        array = np.asarray(frame)
        if array.ndim == 2:
            gray = np.ascontiguousarray(array, dtype=np.uint8)
            height, width = gray.shape
            return QImage(
                bytes(gray.data), width, height, width,
                QImage.Format.Format_Grayscale8,
            ).copy()
        if array.ndim != 3 or array.shape[2] not in (3, 4):
            return QImage()
        if array.shape[2] == 3:
            converted = np.ascontiguousarray(array[:, :, ::-1], dtype=np.uint8)
            image_format = QImage.Format.Format_RGB888
        else:
            converted = np.ascontiguousarray(array[:, :, [2, 1, 0, 3]], dtype=np.uint8)
            image_format = QImage.Format.Format_RGBA8888
        height, width, channels = converted.shape
        return QImage(
            bytes(converted.data), width, height, width * channels, image_format,
        ).copy()

    def _refresh_screenshot(self) -> None:
        """Load a fresh frame from the main window into the crop canvas."""
        if self._screenshot_callback is None:
            QMessageBox.warning(
                self,
                tr("截图失败"),
                tr("截图功能不可用，请先在主窗口定位窗口或连接设备"),
            )
            return
        try:
            result = self._screenshot_callback()
        except Exception as exc:  # noqa: BLE001 - external capture errors are user-facing
            QMessageBox.warning(self, tr("截图失败"), f"{tr('截图失败')}: {exc}")
            return
        frame, error = result if isinstance(result, tuple) else (result, None)
        if frame is None:
            QMessageBox.warning(self, tr("截图失败"), error or tr("截图失败"))
            return
        image = self._capture_to_qimage(frame)
        if image.isNull():
            QMessageBox.warning(self, tr("截图失败"), tr("没有读取到有效图片"))
            return
        self._set_source_image(image)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Paste):
            self._paste_image()
            event.accept()
            return
        super().keyPressEvent(event)

    def _select_library_item(self, item: QListWidgetItem) -> None:
        filename = str(item.data(Qt.ItemDataRole.UserRole) or "")
        path = self._store.path_for(filename)
        if path is None:
            return
        image = QImage(str(path))
        if image.isNull():
            return
        self._selected_filename = filename
        self._draft_mode = False
        self._canvas.set_image(image)
        self._save_button.setText(tr("使用此头像"))
        self._save_button.setEnabled(filename != self._current_filename)

    def _show_library_context_menu(self, pos) -> None:
        item = self._library.itemAt(pos)
        if item is None:
            return
        self._library.setCurrentItem(item)
        menu = QMenu(self)
        delete_action = menu.addAction(tr("删除历史头像"))
        selected = menu.exec(self._library.viewport().mapToGlobal(pos))
        if selected is delete_action:
            self._delete_library_item(item)

    def _delete_library_item(self, item: QListWidgetItem) -> None:
        filename = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not filename:
            return
        used_by = [
            name for name in self._user_manager.list_users()
            if (user := self._user_manager.get_user(name)) is not None
            and user.avatar == filename
        ]
        if used_by:
            QMessageBox.information(
                self,
                tr("头像正在使用"),
                tr("该头像正被以下用户使用，不能删除：\n{users}\n\n"
                   "请先为这些用户更换或移除头像。")
                .format(users="、".join(used_by)),
            )
            return
        answer = QMessageBox.question(
            self,
            tr("删除历史头像"),
            tr("确定永久删除这张历史头像吗？此操作无法撤销。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self._store.delete(filename)
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr("删除失败"),
                tr("删除历史头像失败：{error}").format(error=exc),
            )
            return
        if not deleted:
            QMessageBox.warning(
                self, tr("删除失败"), tr("头像文件不存在或文件名无效"))
            self._load_library()
            return
        self._selected_filename = ""
        self._draft_mode = False
        self._canvas.clear_image()
        self._save_button.setText(tr("保存并使用"))
        self._save_button.setEnabled(False)
        self._load_library()

    def _persist_reference(self, filename: str) -> None:
        if not self._user_manager.set_user_avatar(self._username, filename):
            raise RuntimeError(tr("无法更新用户头像引用"))
        self._current_filename = filename
        self.avatar_changed.emit(self._username, filename)

    def _save(self) -> None:
        try:
            if self._draft_mode:
                data = self._canvas.png_bytes()
                if not data:
                    raise ValueError(tr("没有可保存的裁剪图片"))
                filename = self._store.save_png(data)
            else:
                filename = self._selected_filename
            if not filename:
                return
            self._persist_reference(filename)
        except Exception as exc:  # noqa: BLE001 - disk/config errors are user-facing
            QMessageBox.warning(
                self, tr("保存失败"),
                tr("保存头像失败：{error}").format(error=exc),
            )
            return
        self.accept()

    def _remove_avatar(self) -> None:
        try:
            self._persist_reference("")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, tr("保存失败"),
                tr("移除头像失败：{error}").format(error=exc),
            )
            return
        self.accept()


__all__ = ["AvatarCropCanvas", "AvatarEditorDialog"]
