"""Reusable rounded-square user avatar display."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QPushButton, QWidget

from ..core.user_avatars import UserAvatarStore
from ..i18n import tr


class AvatarWidget(QWidget):
    """Rounded avatar with an optional hover/double-click edit affordance."""

    edit_requested = pyqtSignal()

    def __init__(
        self,
        username: str = "",
        filename: str = "",
        *,
        editable: bool = False,
        size: int = 144,
        store: UserAvatarStore | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._username = username
        self._filename = filename
        self._editable = editable
        self._store = store or UserAvatarStore()
        self._image = QImage()
        self.setFixedSize(size, size)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if editable else Qt.CursorShape.ArrowCursor
        )
        self.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus if editable else Qt.FocusPolicy.NoFocus
        )
        self.setToolTip(tr("双击编辑头像") if editable else "")

        self._edit_button = QPushButton("✎", self)
        self._edit_button.setAccessibleName(tr("编辑头像"))
        self._edit_button.setFixedSize(44, 44)
        self._edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_button.setStyleSheet("""
            QPushButton {
                color: white;
                background: rgba(0, 0, 0, 150);
                border: 1px solid rgba(255, 255, 255, 150);
                border-radius: 22px;
                font-size: 22px;
            }
            QPushButton:hover { background: rgba(0, 120, 212, 220); }
        """)
        self._edit_button.clicked.connect(self.edit_requested)
        self._edit_button.setVisible(False)
        self.set_avatar(username, filename)

    @property
    def filename(self) -> str:
        return self._filename

    def set_avatar(self, username: str, filename: str) -> None:
        self._username = username
        self._filename = filename
        self._image = QImage()
        path = self._store.path_for(filename)
        if path is not None and path.exists():
            image = QImage(str(path))
            if not image.isNull():
                self._image = image
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._edit_button.move(
            (self.width() - self._edit_button.width()) // 2,
            (self.height() - self._edit_button.height()) // 2,
        )
        super().resizeEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._editable:
            self._edit_button.show()
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._edit_button.hide()
        self.update()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        if self._editable and event.button() == Qt.MouseButton.LeftButton:
            self.edit_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._editable and event.key() in (
            Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space,
        ):
            self.edit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 16, 16)
        painter.setClipPath(path)

        if not self._image.isNull():
            scaled = self._image.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width() - rect.width()) // 2
            y = (scaled.height() - rect.height()) // 2
            painter.drawImage(rect, scaled, scaled.rect().adjusted(x, y, -x, -y))
        else:
            painter.fillPath(path, QColor("#5b8def"))
            initial = (self._username.strip()[:1] or "?").upper()
            font = QFont(self.font())
            font.setBold(True)
            font.setPointSize(max(18, self.width() // 4))
            painter.setFont(font)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, initial)

        if self._editable and self._edit_button.isVisible():
            painter.fillPath(path, QColor(0, 0, 0, 55))
        painter.setClipping(False)
        painter.setPen(QPen(QColor(0, 0, 0, 45), 1))
        painter.drawPath(path)


__all__ = ["AvatarWidget"]
