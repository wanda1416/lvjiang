"""图像识别测试对话框 - 粘贴截图，支持 OCR 文字识别和材料识别"""

import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QApplication, QFileDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from loguru import logger


class OCRTestDialog(QDialog):
    """图像识别测试对话框：粘贴图片 -> OCR 文字识别 / 材料识别"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图像识别")
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

        self._btn_ocr = QPushButton("识别文字 (F5)")
        self._btn_ocr.setEnabled(False)
        self._btn_ocr.clicked.connect(self._on_recognize)
        btn_row.addWidget(self._btn_ocr)

        self._btn_material = QPushButton("识别材料 (F6)")
        self._btn_material.setEnabled(False)
        self._btn_material.clicked.connect(self._on_recognize_material)
        btn_row.addWidget(self._btn_material)

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
        elif key == Qt.Key.Key_F6:
            self._on_recognize_material()
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
        self._btn_ocr.setEnabled(True)
        self._btn_material.setEnabled(True)

    def _on_recognize(self):
        """执行 OCR 文字识别"""
        if self._current_pixmap is None:
            return

        self._status_label.setText("正在识别文字...")
        QApplication.processEvents()

        bgr = self._pixmap_to_bgr(self._current_pixmap)

        try:
            from lvjiang.core.ocr import OCREngine
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

            self._status_label.setText(f"文字识别完成，共 {len(results)} 条结果")
            logger.info(f"OCR 测试：识别到 {len(results)} 条文字")
        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            self._result_text.setText(f"识别失败: {e}")
            self._status_label.setText("识别失败")

    def _on_recognize_material(self):
        """执行材料识别（类型 + 等级 + 数量）"""
        if self._current_pixmap is None:
            return

        self._status_label.setText("正在识别材料...")
        QApplication.processEvents()

        bgr = self._pixmap_to_bgr(self._current_pixmap)

        try:
            from lvjiang.core.ocr import OCREngine
            from lvjiang.apps.yysls.core.material_recognizer import MaterialRecognizer  # noqa: 插件专属

            ocr = OCREngine()
            recognizer = MaterialRecognizer(ocr)
            result = recognizer.recognize(bgr)

            self._result_text.clear()
            if not result.type:
                self._result_text.append("未识别到材料（空槽或无匹配）")
                self._result_text.append(f"  置信度: {result.confidence:.3f}")
            else:
                self._result_text.append(f"类型: {result.type}")
                self._result_text.append(f"等级: {result.level if result.level is not None else '无'}")
                count_str = str(result.count) if result.count is not None else '?'
                owned_str = str(result.owned) if result.owned is not None else '?'
                self._result_text.append(f"数量: {count_str}/{owned_str} (投入/持有)")
                self._result_text.append(f"匹配置信度: {result.confidence:.3f}")

            self._status_label.setText(
                f"材料识别完成: {result.type or '(空)'}"
            )
            logger.info(
                f"材料识别: type={result.type} level={result.level} "
                f"count={result.count} conf={result.confidence:.3f}"
            )
        except Exception as e:
            logger.error(f"材料识别失败: {e}")
            self._result_text.setText(f"识别失败: {e}")
            self._status_label.setText("识别失败")

    @staticmethod
    def _pixmap_to_bgr(pixmap: QPixmap) -> np.ndarray:
        """QPixmap -> BGR numpy 数组"""
        qimg = pixmap.toImage()
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h = qimg.width(), qimg.height()
        bpl = qimg.bytesPerLine()
        buf = qimg.constBits()
        buf.setsize(qimg.sizeInBytes())
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpl)
        bgr = arr[:, :w * 3].reshape(h, w, 3)[:, :, ::-1].copy()
        return bgr

    def _on_clear(self):
        """清空图片和结果"""
        self._current_pixmap = None
        self._image_label.setText("Ctrl+V 粘贴截图（支持剪贴板中的图片）")
        self._result_text.clear()
        self._btn_ocr.setEnabled(False)
        self._btn_material.setEnabled(False)
        self._status_label.setText("就绪")
