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
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.ocr_cleaner import OCRCleaner
from .ocr_canvas import OCRBox, OCRCanvas


class OCRDialog(QDialog):
    """图像识别对话框：图像识别 + 清洗规则管理"""

    def __init__(self, parent=None, refresh_callback=None):
        """
        Args:
            parent: 父窗口
            refresh_callback: 刷新截图回调，返回 (image, error_msg)
        """
        super().__init__(parent)
        self.setWindowTitle("图像识别")
        self.setMinimumSize(1000, 700)
        self._refresh_callback = refresh_callback
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

        # 左右分割：左侧画布，右侧结果文本
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ─── 左侧：画布区域 ───
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 画布上方按钮栏
        canvas_btn_row = QHBoxLayout()

        self._btn_upload = QPushButton("上传图片")
        self._btn_upload.clicked.connect(self._on_upload)
        canvas_btn_row.addWidget(self._btn_upload)

        self._btn_refresh = QPushButton("刷新截图")
        self._btn_refresh.setToolTip("从设备获取最新截图")
        self._btn_refresh.clicked.connect(self._on_refresh)
        canvas_btn_row.addWidget(self._btn_refresh)

        self._btn_clear_canvas = QPushButton("清空画布")
        self._btn_clear_canvas.clicked.connect(self._on_clear)
        canvas_btn_row.addWidget(self._btn_clear_canvas)

        self._btn_clear_selection = QPushButton("清除选择")
        self._btn_clear_selection.setToolTip("清除红色选框")
        self._btn_clear_selection.clicked.connect(self._on_clear_selection)
        canvas_btn_row.addWidget(self._btn_clear_selection)

        canvas_btn_row.addStretch()
        left_layout.addLayout(canvas_btn_row)

        # 画布
        self._canvas = OCRCanvas()
        left_layout.addWidget(self._canvas)

        splitter.addWidget(left_panel)

        # ─── 右侧：识别结果文本 ───
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 识别按钮栏
        btn_row = QHBoxLayout()

        self._btn_ocr = QPushButton("识别文字 (F5)")
        self._btn_ocr.setEnabled(False)
        self._btn_ocr.clicked.connect(self._on_recognize)
        btn_row.addWidget(self._btn_ocr)

        self._btn_material = QPushButton("识别材料 (F6)")
        self._btn_material.setEnabled(False)
        self._btn_material.clicked.connect(self._on_recognize_material)
        btn_row.addWidget(self._btn_material)

        btn_row.addStretch()
        right_layout.addLayout(btn_row)

        # 识别结果
        right_layout.addWidget(QLabel("识别结果（已应用清洗规则）："))
        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        right_layout.addWidget(self._result_text)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)  # 画布占 3/4
        splitter.setStretchFactor(1, 1)  # 文本占 1/4
        layout.addWidget(splitter, stretch=1)  # splitter 占满剩余空间

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
        self._repl_table.cellChanged.connect(self._on_repl_cell_changed)
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
        self._pattern_table.cellChanged.connect(self._on_pattern_cell_changed)
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
            self._repl_table.setItem(i, 0, QTableWidgetItem(wrong))
            self._repl_table.setItem(i, 1, QTableWidgetItem(correct))

        # 正则替换
        patterns = cleaner.get_patterns()
        self._pattern_table.setRowCount(len(patterns))
        for i, (pattern, replacement) in enumerate(patterns.items()):
            self._pattern_table.setItem(i, 0, QTableWidgetItem(pattern))
            self._pattern_table.setItem(i, 1, QTableWidgetItem(replacement))

    # ─── 清洗规则操作 ────────────────────────────────────────

    def _sync_repl_table(self):
        """将文本替换表格内容同步到清洗器"""
        cleaner = OCRCleaner()
        # 读取表格当前数据
        new_repls = {}
        for r in range(self._repl_table.rowCount()):
            key_item = self._repl_table.item(r, 0)
            val_item = self._repl_table.item(r, 1)
            key = key_item.text() if key_item else ""
            val = val_item.text() if val_item else ""
            if key:
                new_repls[key] = val
        # 批量写入，只保存一次
        cleaner.set_replacements(new_repls)

    def _sync_pattern_table(self):
        """将正则替换表格内容同步到清洗器"""
        cleaner = OCRCleaner()
        import re
        # 读取表格当前数据
        new_patterns = {}
        for r in range(self._pattern_table.rowCount()):
            key_item = self._pattern_table.item(r, 0)
            val_item = self._pattern_table.item(r, 1)
            key = key_item.text() if key_item else ""
            val = val_item.text() if val_item else ""
            if key:
                try:
                    re.compile(key)
                except re.error:
                    self._status_label.setText(f"行 {r + 1}: 无效的正则表达式")
                    return
                new_patterns[key] = val
        # 批量写入，只保存一次
        cleaner.set_patterns(new_patterns)
        self._status_label.setText("规则已保存")

    def _on_add_replacement(self):
        """添加文本替换规则：插入空行供编辑"""
        row = self._repl_table.rowCount()
        self._repl_table.insertRow(row)
        self._repl_table.setItem(row, 0, QTableWidgetItem(""))
        self._repl_table.setItem(row, 1, QTableWidgetItem(""))
        self._repl_table.scrollToBottom()
        self._repl_table.setCurrentCell(row, 0)
        self._repl_table.editItem(self._repl_table.item(row, 0))

    def _on_delete_replacement(self):
        """删除选中的替换规则"""
        row = self._repl_table.currentRow()
        if row < 0:
            return
        self._repl_table.removeRow(row)
        self._sync_repl_table()

    def _on_repl_cell_changed(self, row: int, col: int):
        """文本替换表格单元格修改后同步"""
        self._sync_repl_table()

    def _on_add_pattern(self):
        """添加正则替换规则：插入空行供编辑"""
        row = self._pattern_table.rowCount()
        self._pattern_table.insertRow(row)
        self._pattern_table.setItem(row, 0, QTableWidgetItem(""))
        self._pattern_table.setItem(row, 1, QTableWidgetItem(""))
        self._pattern_table.scrollToBottom()
        self._pattern_table.setCurrentCell(row, 0)
        self._pattern_table.editItem(self._pattern_table.item(row, 0))

    def _on_delete_pattern(self):
        """删除选中的正则规则"""
        row = self._pattern_table.currentRow()
        if row < 0:
            return
        self._pattern_table.removeRow(row)
        self._sync_pattern_table()

    def _on_pattern_cell_changed(self, row: int, col: int):
        """正则替换表格单元格修改后同步"""
        self._sync_pattern_table()

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

        # QImage -> numpy BGR
        bgr = self._qimage_to_bgr(qimg)
        if bgr is not None:
            self._canvas.set_image(bgr)
            h, w = bgr.shape[:2]
            self._btn_ocr.setEnabled(True)
            self._btn_material.setEnabled(True)
            self._status_label.setText(f"已粘贴图片 ({w}x{h})，点击「识别」或按 F5")
            logger.info(f"OCR 测试：粘贴图片 {w}x{h}")

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
        # QPixmap -> numpy BGR
        bgr = self._qpixmap_to_bgr(pixmap)
        if bgr is not None:
            self._canvas.set_image(bgr)
            h, w = bgr.shape[:2]
            self._btn_ocr.setEnabled(True)
            self._btn_material.setEnabled(True)
            self._status_label.setText(f"已加载图片 ({w}x{h})，点击「识别」或按 F5")
            logger.info(f"OCR 测试：上传图片 {w}x{h} <- {path}")

    @staticmethod
    def _qimage_to_bgr(qimg: QImage) -> np.ndarray | None:
        """QImage -> BGR numpy 数组"""
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h = qimg.width(), qimg.height()
        bpl = qimg.bytesPerLine()
        buf = qimg.constBits()
        buf.setsize(qimg.sizeInBytes())
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpl)
        rgb = arr[:, :w * 3].reshape(h, w, 3)
        return rgb[:, :, ::-1].copy()

    @staticmethod
    def _qpixmap_to_bgr(pixmap: QPixmap) -> np.ndarray | None:
        """QPixmap -> BGR numpy 数组"""
        return OCRDialog._qimage_to_bgr(pixmap.toImage())

    def _on_refresh(self):
        """从设备获取最新截图"""
        if self._refresh_callback is None:
            self._status_label.setText("刷新截图不可用：未连接设备或未传入回调")
            return

        self._status_label.setText("正在刷新截图...")
        QApplication.processEvents()

        try:
            result = self._refresh_callback()
        except Exception as e:
            logger.error(f"刷新截图回调异常: {e}")
            self._status_label.setText(f"刷新截图失败: {e}")
            return

        new_image, error_msg = result if isinstance(result, tuple) else (result, None)
        if new_image is None:
            self._status_label.setText(error_msg or "刷新截图失败")
            return

        # 直接使用 numpy 数组设置画布
        self._canvas.set_image(new_image)
        h, w = new_image.shape[:2]
        self._btn_ocr.setEnabled(True)
        self._btn_material.setEnabled(True)
        self._status_label.setText(f"已刷新截图 ({w}x{h})")
        logger.info(f"OCR 测试：刷新截图 {w}x{h}")

    def _get_recognition_image(self) -> tuple[np.ndarray | None, str | None]:
        """获取识别用图，考虑选框裁剪
        返回: (bgr_image, error_msg)
        """
        bgr = self._canvas.get_image()
        if bgr is None:
            return None, "无图片"

        # 检查是否有选框
        sel = self._canvas.get_selection_pixels()
        if sel is not None:
            x1, y1, x2, y2 = sel
            # 裁剪选框区域
            crop = bgr[y1:y2, x1:x2].copy()
            if crop.size == 0:
                return None, "选框区域为空"
            return crop, None

        return bgr, None

    def _on_recognize(self):
        """执行 OCR 文字识别（输出已由引擎层清洗）"""
        image, error_msg = self._get_recognition_image()
        if image is None:
            self._status_label.setText(error_msg or "获取图像失败")
            return

        self._status_label.setText("正在识别文字...")
        QApplication.processEvents()

        try:
            from lvjiang.core.ocr import OCREngine
            engine = OCREngine()
            results = engine.recognize(image)

            self._result_text.clear()
            if not results:
                self._result_text.append("未识别到文字")
                self._canvas.set_ocr_boxes([])
            else:
                # 构建画布标注（如果有选框，需要加上偏移）
                sel = self._canvas.get_selection_pixels()
                offset_x, offset_y = (sel[0], sel[1]) if sel else (0, 0)

                boxes = []
                for i, r in enumerate(results, 1):
                    # 如果有选框，将坐标偏移回去
                    bbox = r.bbox
                    if sel is not None:
                        bbox = [[x + offset_x, y + offset_y] for x, y in bbox]
                    boxes.append(OCRBox(
                        text=r.text, confidence=r.confidence, bbox=bbox
                    ))
                    self._result_text.append(
                        f"[{i}] {r.text}  (置信度: {r.confidence:.3f})"
                    )
                    pts = " ".join(f"({x},{y})" for x, y in r.bbox)
                    self._result_text.append(f"    位置: {pts}")
                    self._result_text.append("")
                self._canvas.set_ocr_boxes(boxes)

            self._status_label.setText(f"文字识别完成，共 {len(results)} 条结果")
            logger.info(f"OCR 测试：识别到 {len(results)} 条文字")
        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            self._result_text.setText(f"识别失败: {e}")
            self._status_label.setText("识别失败")

    def _on_recognize_material(self):
        """执行材料识别（类型 + 等级 + 数量）"""
        image, error_msg = self._get_recognition_image()
        if image is None:
            self._status_label.setText(error_msg or "获取图像失败")
            return

        self._status_label.setText("正在识别材料...")
        QApplication.processEvents()

        try:
            from lvjiang.apps.yysls.core.material_recognizer import (
                MaterialRecognizer,
            )
            from lvjiang.core.ocr import OCREngine

            ocr = OCREngine()
            recognizer = MaterialRecognizer(ocr)
            result = recognizer.recognize(image)

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

    def _on_clear(self):
        """清空图片和结果"""
        self._canvas.clear()
        self._result_text.clear()
        self._btn_ocr.setEnabled(False)
        self._btn_material.setEnabled(False)
        self._status_label.setText("就绪")

    def _on_clear_selection(self):
        """清除选框"""
        self._canvas.clear_selection()
        self._status_label.setText("已清除选框")
