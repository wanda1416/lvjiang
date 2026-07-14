"""PyQt6 主窗口"""

import ctypes
import sys
from ctypes import wintypes
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QTextEdit,
    QStatusBar, QTabWidget, QSplitter, QListWidget, QListWidgetItem,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from loguru import logger


HINSTANCE = getattr(wintypes, "HINSTANCE", wintypes.HANDLE)
HICON = getattr(wintypes, "HICON", wintypes.HANDLE)
HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
HBRUSH = getattr(wintypes, "HBRUSH", wintypes.HANDLE)
HMENU = getattr(wintypes, "HMENU", wintypes.HANDLE)
LPVOID = getattr(wintypes, "LPVOID", ctypes.c_void_p)
ATOM = getattr(wintypes, "ATOM", ctypes.c_ushort)
BYTE = getattr(wintypes, "BYTE", ctypes.c_ubyte)
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t


class BorderOverlay:
    """Win32 点击穿透边框层。

    这里刻意不用 Qt 顶层透明窗跨屏绘制，因为 Qt 的 screen.geometry()
    是高 DPI 逻辑坐标，而 Win32 GetWindowRect/SetWindowPos 使用的是
    当前进程视角下的窗口坐标。绘制和定位都走 Win32，能避免多屏/混合
    DPI 时从屏幕 0 标记屏幕 1 出现偏移。
    """

    _class_name = "LvjiangBorderOverlayWindow"
    _class_registered = False
    _wndproc = None
    _instances: dict[int, "BorderOverlay"] = {}

    def __init__(self):
        self._hwnd = None
        self._color = (255, 0, 0)
        self._pen_width = 5

    @classmethod
    def _register_class(cls):
        if cls._class_registered:
            return

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = HINSTANCE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]

        wndproc_type = ctypes.WINFUNCTYPE(
            LRESULT,
            wintypes.HWND,
            wintypes.UINT,
            WPARAM,
            LPARAM,
        )

        @wndproc_type
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == 0x000F:  # WM_PAINT
                overlay = cls._instances.get(int(hwnd))
                if overlay is not None:
                    overlay._paint(hwnd)
                    return 0
            if msg == 0x0002:  # WM_DESTROY
                cls._instances.pop(int(hwnd), None)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        cls._wndproc = wndproc

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc_type),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", HINSTANCE),
                ("hIcon", HICON),
                ("hCursor", HCURSOR),
                ("hbrBackground", HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.RegisterClassW.restype = ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

        hinstance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = cls._wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = cls._class_name

        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError()
        cls._class_registered = True

    def _ensure_window(self):
        if self._hwnd:
            return

        self._register_class()
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = HINSTANCE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            HMENU,
            HINSTANCE,
            LPVOID,
        ]
        user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, BYTE, wintypes.DWORD]

        ex_style = (
            0x00000008  # WS_EX_TOPMOST
            | 0x00080000  # WS_EX_LAYERED
            | 0x00000020  # WS_EX_TRANSPARENT
            | 0x00000080  # WS_EX_TOOLWINDOW
            | 0x08000000  # WS_EX_NOACTIVATE
        )
        style = 0x80000000  # WS_POPUP
        hwnd = user32.CreateWindowExW(
            ex_style,
            self._class_name,
            "Lvjiang Border Overlay",
            style,
            0,
            0,
            1,
            1,
            None,
            None,
            kernel32.GetModuleHandleW(None),
            None,
        )
        if not hwnd:
            raise ctypes.WinError()

        # 颜色键透明：窗口里的黑色透明，红/绿边框可见。
        user32.SetLayeredWindowAttributes(hwnd, 0x000000, 255, 0x00000001)
        self._hwnd = hwnd
        self._instances[int(hwnd)] = self

    def _paint(self, hwnd):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wintypes.HDC),
                ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", ctypes.c_byte * 32),
            ]

        user32.BeginPaint.restype = wintypes.HDC
        user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.EndPaint.restype = wintypes.BOOL
        user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.FillRect.restype = ctypes.c_int
        user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), HBRUSH]
        gdi32.CreateSolidBrush.restype = HBRUSH
        gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
        gdi32.CreatePen.restype = wintypes.HANDLE
        gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        gdi32.GetStockObject.restype = wintypes.HANDLE
        gdi32.GetStockObject.argtypes = [ctypes.c_int]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.Rectangle.restype = wintypes.BOOL
        gdi32.Rectangle.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]

        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))

            black_brush = gdi32.CreateSolidBrush(0x000000)
            user32.FillRect(hdc, ctypes.byref(rect), black_brush)
            gdi32.DeleteObject(black_brush)

            r, g, b = self._color
            colorref = r | (g << 8) | (b << 16)
            pen = gdi32.CreatePen(0, self._pen_width, colorref)  # PS_SOLID
            old_pen = gdi32.SelectObject(hdc, pen)
            old_brush = gdi32.SelectObject(hdc, gdi32.GetStockObject(5))  # NULL_BRUSH

            inset = max(1, self._pen_width // 2)
            gdi32.Rectangle(
                hdc,
                inset,
                inset,
                max(inset + 1, rect.right - inset),
                max(inset + 1, rect.bottom - inset),
            )

            gdi32.SelectObject(hdc, old_brush)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.DeleteObject(pen)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def show_border(self, left: int, top: int, width: int, height: int):
        """在 Win32 窗口坐标上显示边框。"""
        self._ensure_window()
        user32 = ctypes.windll.user32
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.InvalidateRect.restype = wintypes.BOOL
        user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL]
        user32.UpdateWindow.restype = wintypes.BOOL
        user32.UpdateWindow.argtypes = [wintypes.HWND]
        user32.SetWindowPos(
            self._hwnd,
            wintypes.HWND(-1),  # HWND_TOPMOST
            int(left),
            int(top),
            max(1, int(width)),
            max(1, int(height)),
            0x0010 | 0x0040 | 0x0200,  # NOACTIVATE | SHOWWINDOW | NOOWNERZORDER
        )
        user32.InvalidateRect(self._hwnd, None, True)
        user32.UpdateWindow(self._hwnd)
        logger.debug(f"Overlay Win32: ({left},{top},{width}x{height})")

    def hide_border(self):
        """隐藏边框。"""
        if self._hwnd:
            ctypes.windll.user32.ShowWindow.restype = wintypes.BOOL
            ctypes.windll.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            ctypes.windll.user32.ShowWindow(self._hwnd, 0)  # SW_HIDE

    def set_color(self, color: str):
        """设置边框颜色 ('red' / 'green')。"""
        if color == "red":
            self._color = (255, 0, 0)
        elif color == "green":
            self._color = (0, 200, 0)
        if self._hwnd:
            ctypes.windll.user32.InvalidateRect.restype = wintypes.BOOL
            ctypes.windll.user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL]
            ctypes.windll.user32.UpdateWindow.restype = wintypes.BOOL
            ctypes.windll.user32.UpdateWindow.argtypes = [wintypes.HWND]
            ctypes.windll.user32.InvalidateRect(self._hwnd, None, True)
            ctypes.windll.user32.UpdateWindow(self._hwnd)

    def destroy(self):
        """销毁原生窗口。"""
        if self._hwnd:
            ctypes.windll.user32.DestroyWindow.restype = wintypes.BOOL
            ctypes.windll.user32.DestroyWindow.argtypes = [wintypes.HWND]
            ctypes.windll.user32.DestroyWindow(self._hwnd)
            self._instances.pop(int(self._hwnd), None)
            self._hwnd = None


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
        """根据运行状态刷新单一运行按钮。"""
        if self._running:
            self.btn_run_toggle.setText("停止 (F10)")
            self.btn_run_toggle.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold; padding: 8px;"
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
        if had_target:
            self.log_text.append("[状态] 重新扫描窗口，旧定位已失效")

        from ..core.capture import list_visible_windows
        self._scanned_windows = list_visible_windows()
        self.window_list.clear()

        if not self._scanned_windows:
            self.log_text.append("[错误] 未找到可见窗口")
            self.statusBar().showMessage("未定位窗口 | 未找到可见窗口")
            return

        for w in self._scanned_windows:
            item = QListWidgetItem(f"{w['title']}  ({w['width']}x{w['height']})")
            item.setData(Qt.ItemDataRole.UserRole, w)  # 存完整数据
            self.window_list.addItem(item)

        self.log_text.append(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，请在列表中选择目标窗口")
        self.btn_locate.setEnabled(False)
        self.lbl_window_info.setText("请在列表中选择目标窗口...")
        self.lbl_window_info.setStyleSheet("color: orange;")
        self.statusBar().showMessage("已扫描窗口 | 请选择目标窗口并点击定位")

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
            message = "请先扫描窗口并点击“定位”，再开始执行。"
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


def run_app():
    """启动应用"""
    # Qt6 默认已设置 Per Monitor DPI Aware v2，无需额外调用

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
