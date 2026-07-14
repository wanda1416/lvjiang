"""PyQt6 主窗口"""

import ctypes
from ctypes import wintypes
import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QTextEdit,
    QTabWidget, QSplitter, QMenuBar,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QImage, QPixmap, QAction
from loguru import logger

from .overlay import BorderOverlay


class MainWindow(QMainWindow):
    """律匠主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("律匠 - 燕云十六声装备调律工具 v0.1.0")
        self.setMinimumSize(1000, 700)

        # 投屏窗口信息
        self._target_window = None  # dict: {title, hwnd, left, top, width, height}
        self._scanned_windows = []  # 扫描到的窗口列表

        # 运行状态
        self._running = False

        # 边框覆盖层（定位/运行状态指示）
        self._overlay = BorderOverlay()

        # 截屏器（定位后初始化，后续自动化复用）
        self._capture = None
        self._last_capture = None  # 最近一次截屏（numpy BGR）

        # 区域布局（定位后由区域编辑器设置）
        self._region_layout = None

        self._setup_menu()
        self._setup_ui()
        logger.info("主窗口已初始化")

    def _setup_menu(self):
        """构建顶部菜单栏"""
        menubar = self.menuBar()

        # 工具菜单
        tools_menu = menubar.addMenu("工具")

        ocr_test_action = QAction("OCR 测试", self)
        ocr_test_action.triggered.connect(self._open_ocr_test)
        tools_menu.addAction(ocr_test_action)

        region_editor_action = QAction("区域编辑器", self)
        region_editor_action.triggered.connect(self._open_region_editor)
        tools_menu.addAction(region_editor_action)

    def _open_ocr_test(self):
        """打开 OCR 测试对话框"""
        from .ocr_test_dialog import OCRTestDialog
        dialog = OCRTestDialog(self)
        dialog.exec()

    def _open_region_editor(self):
        """打开区域编辑器（使用当前截屏图片）"""
        from .region_editor import RegionEditorDialog
        image = self._get_last_capture()
        if image is None:
            logger.warning("请先定位窗口并截屏")
            return
        dialog = RegionEditorDialog(
            image,
            refresh_callback=self._refresh_capture,
            parent=self,
        )
        dialog.exec()

    def _setup_ui(self):
        """构建界面"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # === 顶部：目标窗口选择 ===
        window_group = QGroupBox("目标窗口")
        window_main_layout = QVBoxLayout(window_group)

        # 第一行：扫描按钮 + 窗口列表 + 定位按钮
        row1 = QHBoxLayout()

        self.btn_scan_window = QPushButton("扫描窗口")
        self.btn_scan_window.setFixedWidth(90)
        self.btn_scan_window.clicked.connect(self._on_scan_window)
        row1.addWidget(self.btn_scan_window)

        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(300)
        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        row1.addWidget(self.window_combo)

        self.btn_locate = QPushButton("定位")
        self.btn_locate.setFixedWidth(70)
        self.btn_locate.setEnabled(False)
        self.btn_locate.clicked.connect(self._on_locate_window)
        row1.addWidget(self.btn_locate)

        window_main_layout.addLayout(row1)

        # 第二行：已定位信息
        row2 = QHBoxLayout()

        self.lbl_window_info = QLabel("未选择窗口")
        self.lbl_window_info.setStyleSheet("color: gray;")
        row2.addWidget(self.lbl_window_info)

        row2.addStretch()

        window_main_layout.addLayout(row2)
        main_layout.addWidget(window_group)

        # === 截屏预览区 ===
        self.preview_label = QLabel("定位窗口后自动截屏")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedHeight(320)
        self.preview_label.setStyleSheet(
            "background-color: #2b2b2b; color: #888; font-size: 14px;"
        )
        main_layout.addWidget(self.preview_label)

        # === 中部：左右分栏 ===
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：配置区
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # 流派选择
        flow_group = QGroupBox("目标流派")
        flow_layout = QVBoxLayout(flow_group)
        self.flow_selector = QComboBox()
        self.flow_selector.addItems([
            "会心双刀", "裂石威", "明川药典", "九剑", "无名",
        ])
        flow_layout.addWidget(self.flow_selector)
        left_layout.addWidget(flow_group)

        # 模式选择
        mode_group = QGroupBox("处理模式")
        mode_layout = QVBoxLayout(mode_group)
        self.mode_selector = QComboBox()
        self.mode_selector.addItems(["批量筛选", "精调模式"])
        mode_layout.addWidget(self.mode_selector)
        left_layout.addWidget(mode_group)

        # 操作按钮
        action_group = QGroupBox("操作")
        action_layout = QVBoxLayout(action_group)

        self.btn_scan = QPushButton("扫描穿戴装备")
        self.btn_scan.clicked.connect(self._on_scan)
        action_layout.addWidget(self.btn_scan)

        self.btn_run_toggle = QPushButton()
        self.btn_run_toggle.clicked.connect(self._on_toggle_running)
        self._refresh_run_button()
        action_layout.addWidget(self.btn_run_toggle)

        left_layout.addWidget(action_group)
        left_layout.addStretch()

        # 右侧：日志/预览区
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        self.tabs = QTabWidget()

        # 日志标签页
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        self.tabs.addTab(self.log_text, "运行日志")

        # 状态标签页（预留）
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.tabs.addTab(self.status_text, "装备状态")

        right_layout.addWidget(self.tabs)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 700])

        main_layout.addWidget(splitter)

        # === 底部状态栏 ===
        self.statusBar().showMessage("就绪 | F9 开始 | F10 停止")

        # 初始化日志重定向
        self._setup_log_redirect()

    def _setup_log_redirect(self):
        """将 loguru 日志输出到 GUI 日志面板"""
        class QtSink:
            def __init__(self, text_edit):
                self.text_edit = text_edit

            def write(self, message):
                try:
                    self.text_edit.append(message.strip())
                except RuntimeError:
                    pass

        sink = QtSink(self.log_text)
        logger.add(sink, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

    def _refresh_run_button(self):
        """根据运行状态和定位状态刷新运行按钮。"""
        if self._running:
            self.btn_run_toggle.setText("停止 (F10)")
            self.btn_run_toggle.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold; padding: 8px;"
            )
        elif self._target_window is None:
            self.btn_run_toggle.setText("未定位")
            self.btn_run_toggle.setStyleSheet(
                "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px;"
            )
        else:
            self.btn_run_toggle.setText("开始执行 (F9)")
            self.btn_run_toggle.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;"
            )

    # === 快捷键 ===

    def keyPressEvent(self, event: QKeyEvent):
        """全局快捷键处理"""
        if event.key() == Qt.Key.Key_F9:
            if not self._running:
                self._on_start()
        elif event.key() == Qt.Key.Key_F10:
            if self._running:
                self._on_stop()
        else:
            super().keyPressEvent(event)

    # === 事件处理 ===

    def _on_scan_window(self):
        """扫描所有可见窗口，填充列表"""
        if self._running:
            self.log_text.append("[提示] 请先停止当前任务，再重新扫描窗口")
            return

        had_target = self._target_window is not None
        self._target_window = None
        self._overlay.hide_border()
        self.btn_locate.setEnabled(False)
        self.lbl_window_info.setText("未定位窗口")
        self.lbl_window_info.setStyleSheet("color: gray;")
        self.statusBar().showMessage("正在扫描窗口...")
        self._refresh_run_button()  # 按钮变回黄色"未定位"
        if had_target:
            self.log_text.append("[状态] 重新扫描窗口，旧定位已失效")

        from ..core.capture import list_visible_windows
        self._scanned_windows = list_visible_windows()
        self.window_combo.clear()

        if not self._scanned_windows:
            self.log_text.append("[错误] 未找到可见窗口")
            self.statusBar().showMessage("未定位窗口 | 未找到可见窗口")
            return

        for w in self._scanned_windows:
            self.window_combo.addItem(
                f"{w['title']}  ({w['width']}x{w['height']})",
                w,
            )

        self.log_text.append(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，请下拉选择目标窗口")
        self.btn_locate.setEnabled(True)
        self.lbl_window_info.setText("请下拉选择目标窗口...")
        self.lbl_window_info.setStyleSheet("color: orange;")
        self.statusBar().showMessage("已扫描窗口 | 请下拉选择目标窗口并点击定位")

    def _on_window_selected(self, index):
        """下拉框选择了某项时，启用定位按钮"""
        self.btn_locate.setEnabled(index >= 0)

    def _on_locate_window(self):
        """定位选中的窗口，实时获取其当前坐标"""
        w = self.window_combo.currentData()
        if not w:
            return
        # 实时查询窗口当前位置（扫描时的坐标可能已过期）
        self._refresh_window_rect(w)
        self._target_window = w

        # 边框绘制和窗口枚举都走 Win32 坐标，DPI 这里只做诊断展示。
        ratio = self._get_window_dpi_ratio(w["hwnd"])
        logger.info(
            f"目标窗口 Win32原始: ({w['left']},{w['top']},{w['width']}x{w['height']})"
            f" DPI={ratio}"
        )

        self.lbl_window_info.setText(
            f"已定位: {w['title']}  |  "
            f"位置: ({w['left']}, {w['top']})  大小: {w['width']}x{w['height']}"
            + (f"  DPI缩放: {ratio:.1f}x" if ratio != 1.0 else "")
        )
        self.lbl_window_info.setStyleSheet("color: green;")
        self.log_text.append(
            f"[定位成功] {w['title']}  "
            f"({w['width']}x{w['height']} @ {w['left']},{w['top']})"
            + (f" DPI={ratio:.1f}x" if ratio != 1.0 else "")
        )
        # 显示红色边框（全屏覆盖层模式）
        self._overlay.show_border(w['left'], w['top'], w['width'], w['height'])
        self._overlay.set_color("red")
        # 定位成功，按钮从黄色"未定位"变为绿色"开始执行"
        self._refresh_run_button()
        # 截屏展示
        self._capture_preview()

    def _capture_preview(self):
        """截取已定位窗口的截图并展示在预览区。"""
        if not self._target_window:
            return
        w = self._target_window
        try:
            from ..core.capture import ScreenCapture
            if self._capture is None:
                self._capture = ScreenCapture()
            self._capture.set_capture_region(
                w['left'], w['top'], w['width'], w['height']
            )
            img = self._capture.capture()  # numpy BGR
            if img is None:
                self.preview_label.setText("截屏失败")
                return
            self._last_capture = img  # 保存截屏供区域编辑器使用
            h, w_img = img.shape[:2]
            rgb = np.ascontiguousarray(img[:, :, ::-1])
            fmt = QImage.Format.Format_RGB888
            qimg = QImage(rgb.data, w_img, h, w_img * 3, fmt).copy()
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
            logger.info(f"截屏预览成功 ({w_img}x{h})")
        except Exception as e:
            logger.error(f"截屏预览失败: {e}")
            self.preview_label.setText(f"截屏失败: {e}")

    def _get_last_capture(self) -> np.ndarray | None:
        """获取最近一次截屏图片（numpy BGR）"""
        return self._last_capture

    def _refresh_capture(self) -> np.ndarray | None:
        """重新截取当前窗口截图（用于区域编辑器刷新）"""
        if not self._target_window:
            return None
        try:
            from ..core.capture import ScreenCapture
            if self._capture is None:
                self._capture = ScreenCapture()
            w = self._target_window
            self._capture.set_capture_region(
                w['left'], w['top'], w['width'], w['height']
            )
            img = self._capture.capture()
            if img is not None:
                self._last_capture = img
            return img
        except Exception as e:
            logger.error(f"刷新截图失败: {e}")
            return None

    def _refresh_window_rect(self, w: dict):
        """通过 Win32 GetWindowRect 实时刷新窗口位置。"""
        rect = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(wintypes.HWND(w['hwnd']), ctypes.byref(rect)):
            w['left'] = rect.left
            w['top'] = rect.top
            w['width'] = rect.right - rect.left
            w['height'] = rect.bottom - rect.top

    def _get_window_dpi_ratio(self, hwnd: int) -> float:
        """返回目标窗口所在屏幕的 DPI 缩放比，仅用于日志展示。"""
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(wintypes.HWND(hwnd))
            if dpi:
                return dpi / 96
        except Exception as e:
            logger.debug(f"获取窗口 DPI 失败: {e}")
        return 1.0

    def closeEvent(self, event):
        """关闭主窗口时清理原生覆盖层。"""
        self._overlay.destroy()
        super().closeEvent(event)

    def _on_scan(self):
        """扫描穿戴装备"""
        flow = self.flow_selector.currentText()
        self.log_text.append(f"[操作] 扫描穿戴装备 (流派: {flow})")
        # TODO: Phase 6 实现
        self.log_text.append("[提示] 扫描功能待实现")

    def _on_start(self):
        """开始执行"""
        if self._running:
            return
        if self._target_window is None:
            message = '请先扫描窗口并点击“定位”，再开始执行。'
            self.log_text.append(f"[提示] {message}")
            self.statusBar().showMessage("未定位窗口 | 请先扫描窗口并点击定位")
            return
        flow = self.flow_selector.currentText()
        mode = self.mode_selector.currentText()
        self._running = True
        self.log_text.append(f"[操作] 开始执行: 流派={flow}, 模式={mode}")
        self._refresh_run_button()
        self.statusBar().showMessage(f"执行中: {flow} - {mode} | F10 停止")
        # 边框变绿色
        self._overlay.set_color("green")

    def _on_stop(self):
        """停止执行"""
        if not self._running:
            return
        self._running = False
        self.log_text.append("[操作] 停止执行")
        self._refresh_run_button()
        self.statusBar().showMessage("已停止 | F9 开始")
        # 边框变回红色
        self._overlay.set_color("red")

    def _on_toggle_running(self):
        """单按钮切换运行状态。"""
        if self._running:
            self._on_stop()
        else:
            self._on_start()
