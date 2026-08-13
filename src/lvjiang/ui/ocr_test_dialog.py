"""图像识别对话框 - 多 Tab 结构

Tab 1: 图像识别 - 粘贴截图，支持 OCR 文字识别和材料识别
Tab 2: 清洗规则 - 管理 OCR 文本通用清洗规则
"""

import numpy as np
from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.ocr_cleaner import OCRCleaner


class OCRTestDialog(QDialog):
    """图像识别对话框：图像识别 + 清洗规则管理"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图像识别")
        self.setMinimumSize(750, 650)
        self._current_pixmap = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Tab 控件
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # Tab 1: 图像识别
        self._ocr_tab = self._create_ocr_tab()
        self._tabs.addTab(self._ocr_tab, "图像识别")

        # Tab 2: 清洗规则
        self._rules_tab = self._create_rules_tab()
        self._tabs.addTab(self._rules_tab, "清洗规则")

    # ─── Tab 1: 图像识别 ────────────────────────────────────

    def _create_ocr_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 图片显示区
        self._image_label = QLabel("Ctrl+V 粘贴截图，或点击「上传图片」加载文件")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumHeight(250)
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
        layout.addWidget(QLabel("识别结果（已应用清洗规则）："))
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

        return widget

    # ─── Tab 2: 清洗规则 ────────────────────────────────────

    def _create_rules_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel(
            "通用清洗规则：所有 OCR 识别出的文字都会经过这些规则处理。\n"
            "规则修改后立即生效，无需重启。"
        ))

        # ── 文本替换规则 ──
        repl_group = QGroupBox("文本替换（精确匹配）")
        repl_layout = QVBoxLayout(repl_group)

        self._repl_table = QTableWidget(0, 2)
        self._repl_table.setHorizontalHeaderLabels(["原始文本", "替换为"])
        self._repl_table.horizontalHeader().setStretchLastSection(True)
        self._repl_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        repl_layout.addWidget(self._repl_table)

        repl_btn_row = QHBoxLayout()
        self._btn_add_repl = QPushButton("+ 添加替换规则")
        self._btn_add_repl.clicked.connect(self._on_add_replacement)
        repl_btn_row.addWidget(self._btn_add_repl)

        self._btn_del_repl = QPushButton("- 删除选中")
        self._btn_del_repl.clicked.connect(self._on_delete_replacement)
        repl_btn_row.addWidget(self._btn_del_repl)

        repl_btn_row.addStretch()
        repl_layout.addLayout(repl_btn_row)

        layout.addWidget(repl_group)

        # ── 正则替换规则 ──
        pattern_group = QGroupBox("正则替换")
        pattern_layout = QVBoxLayout(pattern_group)

        self._pattern_table = QTableWidget(0, 2)
        self._pattern_table.setHorizontalHeaderLabels(["正则表达式", "替换为"])
        self._pattern_table.horizontalHeader().setStretchLastSection(True)
        self._pattern_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        pattern_layout.addWidget(self._pattern_table)

        pattern_btn_row = QHBoxLayout()
        self._btn_add_pattern = QPushButton("+ 添加正则规则")
        self._btn_add_pattern.clicked.connect(self._on_add_pattern)
        pattern_btn_row.addWidget(self._btn_add_pattern)

        self._btn_del_pattern = QPushButton("- 删除选中")
        self._btn_del_pattern.clicked.connect(self._on_delete_pattern)
        pattern_btn_row.addWidget(self._btn_del_pattern)

        pattern_btn_row.addStretch()
        pattern_layout.addLayout(pattern_btn_row)

        layout.addWidget(pattern_group)

        # ── 测试区 ──
        test_group = QGroupBox("测试清洗效果")
        test_layout = QVBoxLayout(test_group)

        test_input_row = QHBoxLayout()
        self._test_input = QTextEdit()
        self._test_input.setPlaceholderText("输入测试文本...")
        self._test_input.setMaximumHeight(60)
        test_input_row.addWidget(self._test_input)

        self._test_output = QTextEdit()
        self._test_output.setReadOnly(True)
        self._test_output.setPlaceholderText("清洗结果...")
        self._test_output.setMaximumHeight(60)
        self._test_output.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        test_input_row.addWidget(self._test_output)
        test_layout.addLayout(test_input_row)

        test_btn_row = QHBoxLayout()
        self._btn_test = QPushButton("测试")
        self._btn_test.clicked.connect(self._on_test_clean)
        test_btn_row.addWidget(self._btn_test)
        test_btn_row.addStretch()
        test_layout.addLayout(test_btn_row)

        layout.addWidget(test_group)

        # 加载规则到表格
        self._refresh_rules_tables()

        return widget

    def _refresh_rules_tables(self):
        """刷新规则表格"""
        cleaner = OCRCleaner()

        # 文本替换
        repls = cleaner.get_replacements()
        self._repl_table.setRowCount(len(repls))
        for i, (wrong, correct) in enumerate(repls.items()):
            item_wrong = QTableWidgetItem(wrong)
            item_wrong.setFlags(item_wrong.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._repl_table.setItem(i, 0, item_wrong)
            self._repl_table.setItem(i, 1, QTableWidgetItem(correct))

        # 正则替换
        patterns = cleaner.get_patterns()
        self._pattern_table.setRowCount(len(patterns))
        for i, rule in enumerate(patterns):
            item_pattern = QTableWidgetItem(rule.get("pattern", ""))
            item_pattern.setFlags(item_pattern.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._pattern_table.setItem(i, 0, item_pattern)
            self._pattern_table.setItem(i, 1, QTableWidgetItem(rule.get("replacement", "")))

    # ─── 清洗规则操作 ────────────────────────────────────────

    def _on_add_replacement(self):
        """添加文本替换规则"""
        wrong, ok = QInputDialog.getText(self, "添加替换规则", "原始文本（将被替换的）:")
        if not ok or not wrong:
            return
        correct, ok = QInputDialog.getText(self, "添加替换规则", f"将 \"{wrong}\" 替换为:")
        if not ok:
            return
        OCRCleaner().add_replacement(wrong, correct)
        self._refresh_rules_tables()

    def _on_delete_replacement(self):
        """删除选中的替换规则"""
        row = self._repl_table.currentRow()
        if row < 0:
            return
        item = self._repl_table.item(row, 0)
        if item:
            OCRCleaner().remove_replacement(item.text())
            self._refresh_rules_tables()

    def _on_add_pattern(self):
        """添加正则替换规则"""
        pattern, ok = QInputDialog.getText(self, "添加正则规则", "正则表达式:")
        if not ok or not pattern:
            return
        # 验证正则
        try:
            import re
            re.compile(pattern)
        except re.error as e:
            QMessageBox.warning(self, "正则错误", f"无效的正则表达式:\n{e}")
            return
        replacement, ok = QInputDialog.getText(self, "添加正则规则", "替换为:")
        if not ok:
            return
        OCRCleaner().add_pattern(pattern, replacement)
        self._refresh_rules_tables()

    def _on_delete_pattern(self):
        """删除选中的正则规则"""
        row = self._pattern_table.currentRow()
        if row < 0:
            return
        OCRCleaner().remove_pattern(row)
        self._refresh_rules_tables()

    def _on_test_clean(self):
        """测试清洗效果"""
        text = self._test_input.toPlainText()
        cleaned = OCRCleaner().clean(text)
        self._test_output.setPlainText(cleaned)

    # ─── 图像识别操作 ────────────────────────────────────────

    def keyPressEvent(self, event):
        mod = event.modifiers()
        key = event.key()
        if mod == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_V:
            self._paste_image()
            self._tabs.setCurrentIndex(0)  # 切换到图像识别 Tab
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
        scaled = pixmap.scaled(
            self._image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._btn_ocr.setEnabled(True)
        self._btn_material.setEnabled(True)

    def _on_recognize(self):
        """执行 OCR 文字识别（输出已由引擎层清洗）"""
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
            from lvjiang.apps.yysls.core.material_recognizer import (
                MaterialRecognizer,
            )
            from lvjiang.core.ocr import OCREngine

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
