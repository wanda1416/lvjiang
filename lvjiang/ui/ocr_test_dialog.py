"""OCR 测试对话框 - 粘贴截图并识别"""

import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QApplication, QFileDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from loguru import logger


class OCRTestDialog(QDialog):
    """OCR 测试对话框：粘贴图片 -> 识别 -> 展示结果"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OCR 测试")
        self.setMinimumSize(700, 600)
        self._image_label = None   # QLabel 显示图片
        self._result_text = None   # QTextEdit 显示结果
        self._status_label = None  # 状态提示
        self._current_pixmap = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 图片显示区
        self._image_label = QLabel("Ctrl+V 粘贴截图，或点击「上传图片」加载文件")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumHeight(300)
        self._image_label.setStyleSheet(
            "background-color: #2b2b2b; color: #888; font-size: 14px;"
        )
        layout.addWidget(self._image_label)

        # 按钮栏
        btn_row = QHBoxLayout()
        self._btn_upload = QPushButton("上传图片")
        self._btn_upload.clicked.connect(self._on_upload)
        btn_row.addWidget(self._btn_upload)

        self._btn_recognize = QPushButton("识别 (F5)")
        self._btn_recognize.setEnabled(False)
        self._btn_recognize.clicked.connect(self._on_recognize)
        btn_row.addWidget(self._btn_recognize)

        self._btn_clear = QPushButton("清空")
        self._btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self._btn_clear)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 识别结果
        layout.addWidget(QLabel("识别结果："))
        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        layout.addWidget(self._result_text)

        # 状态栏
        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet("color: gray;")
        layout.addWidget(self._status_label)

    def keyPressEvent(self, event):
        mod = event.modifiers()
        key = event.key()
        if mod == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_V:
            self._paste_image()
        elif key == Qt.Key.Key_F5:
            self._on_recognize()
        else:
            super().keyPressEvent(event)

    def _paste_image(self):
        """从剪贴板粘贴图片"""
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        if mime.hasImage():
            qimg = clipboard.image()
        elif mime.hasUrls():
            # 某些截图工具以文件路径方式复制
            url = mime.urls()[0]
            qimg = QImage(url.toLocalFile())
        else:
            self._status_label.setText("剪贴板中没有图片")
            return

        if qimg.isNull():
            self._status_label.setText("无法读取剪贴板图片")
            return

        pixmap = QPixmap.fromImage(qimg)
        self._set_pixmap(pixmap)
        self._status_label.setText(
            f"已粘贴图片 ({pixmap.width()}x{pixmap.height()})，点击「识别」或按 F5"
        )
        logger.info(f"OCR 测试：粘贴图片 {pixmap.width()}x{pixmap.height()}")

    def _on_upload(self):
        """从文件对话框加载图片"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)",
        )
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._status_label.setText(f"无法读取图片: {path}")
            return
        self._set_pixmap(pixmap)
        w, h = pixmap.width(), pixmap.height()
        self._status_label.setText(f"已加载图片 ({w}x{h})，点击「识别」或按 F5")
        logger.info(f"OCR 测试：上传图片 {w}x{h} <- {path}")

    def _set_pixmap(self, pixmap: QPixmap):
        """设置当前图片并刷新显示"""
        self._current_pixmap = pixmap
        w, h = pixmap.width(), pixmap.height()
        scaled = pixmap.scaled(
            self._image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._btn_recognize.setEnabled(True)

    def _on_recognize(self):
        """执行 OCR 识别"""
        if self._current_pixmap is None:
            return

        self._status_label.setText("正在识别...")
        QApplication.processEvents()

        # QPixmap -> numpy BGR
        qimg = self._current_pixmap.toImage()
        # 统一转为 RGB888，避免格式分支
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h = qimg.width(), qimg.height()
        bpl = qimg.bytesPerLine()  # 行字节数（含 4 字节对齐填充）
        buf = qimg.constBits()
        buf.setsize(qimg.sizeInBytes())
        # 按实际 stride reshape，再裁掉填充字节
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpl)
        bgr = arr[:, :w * 3].reshape(h, w, 3)[:, :, ::-1].copy()

        try:
            from ..core.ocr import OCREngine
            engine = OCREngine()
            results = engine.recognize(bgr)

            self._result_text.clear()
            if not results:
                self._result_text.append("未识别到文字")
            else:
                for i, r in enumerate(results, 1):
                    self._result_text.append(
                        f"[{i}] {r.text}  (置信度: {r.confidence:.3f})"
                    )
                    pts = " ".join(f"({x},{y})" for x, y in r.bbox)
                    self._result_text.append(f"    位置: {pts}")
                    self._result_text.append("")

            self._status_label.setText(f"识别完成，共 {len(results)} 条结果")
            logger.info(f"OCR 测试：识别到 {len(results)} 条文字")
        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            self._result_text.setText(f"识别失败: {e}")
            self._status_label.setText("识别失败")

    def _on_clear(self):
        """清空图片和结果"""
        self._current_pixmap = None
        self._image_label.setText("Ctrl+V 粘贴截图（支持剪贴板中的图片）")
        self._result_text.clear()
        self._btn_recognize.setEnabled(False)
        self._status_label.setText("就绪")
