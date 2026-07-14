"""PyQt6 主窗口"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QTextEdit,
    QStatusBar, QTabWidget, QSplitter, QListWidget, QListWidgetItem,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer, QRect
from PyQt6.QtGui import QKeyEvent, QPainter, QPen, QColor
from loguru import logger


class BorderOverlay(QWidget):
    """全屏透明覆盖层，在目标窗口位置绘制彩色边框
    
    采用全屏覆盖策略：避免 Qt 多 DPI 下对非主屏窗口的坐标缩放问题。
    窗口覆盖整个虚拟屏幕，只在目标窗口位置画边框，其余区域透明。
    使用 WS_EX_TRANSPARENT 让鼠标点击穿透。
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # 不显示在任务栏
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._color = QColor(255, 0, 0)  # 默认红色
        self._pen_width = 5
        self._overlay_rect = None  # 边框绘制区域 (x, y, w, h)

        # 设置为全屏点击穿透
        import ctypes
        self._setup_click_through()

    def _setup_click_through(self):
        """设置 WS_EX_TRANSPARENT 让鼠标事件穿透"""
        import ctypes
        hwnd = int(self.winId())
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT | WS_EX_LAYERED)

    def show_border(self, left: int, top: int, width: int, height: int):
        """显示全屏覆盖层，在指定位置绘制边框
        坐标直接使用 Win32 虚拟屏幕坐标，无需 DPI 转换"""
        # 计算整个虚拟屏幕范围
        app = QApplication.instance()
        min_x = min(s.geometry().x() for s in app.screens())
        min_y = min(s.geometry().y() for s in app.screens())
        max_x = max(s.geometry().x() + s.geometry().width() for s in app.screens())
        max_y = max(s.geometry().y() + s.geometry().height() for s in app.screens())
        # 设置全屏几何
        self.setGeometry(min_x, min_y, max_x - min_x, max_y - min_y)
        # 计算边框绘制区域（相对于窗口左上角）
        offset = self._pen_width
        self._overlay_rect = (
            left - offset - min_x,
            top - offset - min_y,
            width + offset * 2,
            height + offset * 2,
        )
        self.show()
        self._setup_click_through()  # show 后重新设置
        logger.debug(f"Overlay 全屏模式: 窗口=({min_x},{min_y},{max_x-min_x}x{max_y-min_y}) 边框={self._overlay_rect}")

    def hide_border(self):
        """隐藏边框"""
        self._overlay_rect = None
        self.hide()

    def set_color(self, color: str):
        """设置边框颜色 ('red' / 'green')"""
        if color == "red":
            self._color = QColor(255, 0, 0, 220)
        elif color == "green":
            self._color = QColor(0, 200, 0, 220)
        self.update()

    def paintEvent(self, event):
        if not self._overlay_rect:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color, self._pen_width)
        painter.setPen(pen)
        rx, ry, rw, rh = self._overlay_rect
        painter.drawRect(rx, ry, rw, rh)
        painter.end()


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

        self._setup_ui()
        logger.info("主窗口已初始化")

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

        self.window_list = QListWidget()
        self.window_list.setMaximumHeight(80)
        self.window_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.window_list.itemSelectionChanged.connect(self._on_window_selected)
        row1.addWidget(self.window_list)

        self.btn_locate = QPushButton("定位")
        self.btn_locate.setFixedWidth(70)
        self.btn_locate.setEnabled(False)
        self.btn_locate.clicked.connect(self._on_locate_window)
        row1.addWidget(self.btn_locate)

        window_main_layout.addLayout(row1)

        # 第二行：已定位信息 + 快捷键提示
        row2 = QHBoxLayout()

        self.lbl_window_info = QLabel("未选择窗口")
        self.lbl_window_info.setStyleSheet("color: gray;")
        row2.addWidget(self.lbl_window_info)

        row2.addStretch()

        shortcut_hint = QLabel("F9 开始 | F10 停止")
        shortcut_hint.setStyleSheet("color: #666; font-size: 12px;")
        row2.addWidget(shortcut_hint)

        window_main_layout.addLayout(row2)
        main_layout.addWidget(window_group)

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

        self.btn_start = QPushButton("开始执行 (F9)")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;")
        self.btn_start.clicked.connect(self._on_start)
        action_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("停止 (F10)")
        self.btn_stop.setStyleSheet("background-color: #f44336; color: white; padding: 8px;")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        action_layout.addWidget(self.btn_stop)

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
        # 隐藏边框覆盖层
        self._overlay.hide_border()
        from ..core.capture import list_visible_windows
        self._scanned_windows = list_visible_windows()
        self.window_list.clear()

        if not self._scanned_windows:
            self.log_text.append("[错误] 未找到可见窗口")
            return

        for w in self._scanned_windows:
            item = QListWidgetItem(f"{w['title']}  ({w['width']}x{w['height']})")
            item.setData(Qt.ItemDataRole.UserRole, w)  # 存完整数据
            self.window_list.addItem(item)

        self.log_text.append(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，请在列表中选择目标窗口")
        self.btn_locate.setEnabled(False)
        self.lbl_window_info.setText("请在列表中选择目标窗口...")
        self.lbl_window_info.setStyleSheet("color: orange;")

    def _on_window_selected(self):
        """窗口列表中选择了某项时，启用定位按钮"""
        has_selection = len(self.window_list.selectedItems()) > 0
        self.btn_locate.setEnabled(has_selection)

    def _on_locate_window(self):
        """定位选中的窗口，获取其坐标"""
        selected = self.window_list.selectedItems()
        if not selected:
            return
        w = selected[0].data(Qt.ItemDataRole.UserRole)
        self._target_window = w

        # Win32 与 Qt 同坐标系（Per Monitor DPI Aware v2），直接使用
        ratio = self._get_dpi_ratio(w['left'], w['top'])
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

    def _get_dpi_ratio(self, physical_x: int, physical_y: int) -> float:
        """根据坐标所在屏幕返回 DPI 缩放比

        注意：Qt 的 screen.geometry() 逻辑坐标与 Win32 GetWindowRect
        在同一坐标系下（Per Monitor DPI Aware v2），无需乘以 DPI。
        """
        app = QApplication.instance()
        if not app:
            return 1.0
        for i, screen in enumerate(app.screens()):
            geo = screen.geometry()
            ratio = screen.devicePixelRatio()
            logger.debug(
                f"屏幕{i}: 逻辑=({geo.x()},{geo.y()},{geo.width()}x{geo.height()}) "
                f"DPI={ratio}"
            )
            # 直接用逻辑坐标范围匹配（与 GetWindowRect 同坐标系）
            if (geo.x() <= physical_x < geo.x() + geo.width()
                    and geo.y() <= physical_y < geo.y() + geo.height()):
                logger.info(f"目标在屏幕{i}, DPI={ratio}")
                return ratio
        logger.warning(f"未找到包含 ({physical_x},{physical_y}) 的屏幕")
        return 1.0

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
        flow = self.flow_selector.currentText()
        mode = self.mode_selector.currentText()
        self._running = True
        self.log_text.append(f"[操作] 开始执行: 流派={flow}, 模式={mode}")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.statusBar().showMessage(f"执行中: {flow} - {mode} | F10 停止")
        # 边框变绿色
        self._overlay.set_color("green")

    def _on_stop(self):
        """停止执行"""
        if not self._running:
            return
        self._running = False
        self.log_text.append("[操作] 停止执行")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.statusBar().showMessage("已停止 | F9 开始")
        # 边框变回红色
        self._overlay.set_color("red")


def run_app():
    """启动应用"""
    # Qt6 默认已设置 Per Monitor DPI Aware v2，无需额外调用

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
