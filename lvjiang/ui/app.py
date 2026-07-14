"""PyQt6 主窗口"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QTextEdit,
    QStatusBar, QTabWidget, QSplitter,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from loguru import logger


class MainWindow(QMainWindow):
    """律匠主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("律匠 - 燕云十六声装备调律工具 v0.1.0")
        self.setMinimumSize(1000, 700)

        # 投屏窗口坐标（由"扫描窗口"捕获）
        self._target_window_rect = None  # (left, top, width, height)

        # 运行状态
        self._running = False

        self._setup_ui()
        logger.info("主窗口已初始化")

    def _setup_ui(self):
        """构建界面"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # === 顶部：扫描窗口 ===
        window_group = QGroupBox("目标窗口")
        window_layout = QHBoxLayout(window_group)

        self.btn_scan_window = QPushButton("扫描窗口")
        self.btn_scan_window.setToolTip("点击后选择投屏窗口，自动捕获其位置和大小")
        self.btn_scan_window.clicked.connect(self._on_scan_window)
        window_layout.addWidget(self.btn_scan_window)

        self.lbl_window_info = QLabel("未选择窗口")
        self.lbl_window_info.setStyleSheet("color: gray;")
        window_layout.addWidget(self.lbl_window_info)

        window_layout.addStretch()

        # 快捷键提示
        shortcut_hint = QLabel("F9 开始 | F10 停止")
        shortcut_hint.setStyleSheet("color: #666; font-size: 12px;")
        window_layout.addWidget(shortcut_hint)

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
        """扫描并选择投屏窗口"""
        self.log_text.append("[操作] 扫描窗口 - 请选择投屏窗口...")
        # TODO: 实现窗口选择逻辑（枚举窗口列表让用户选择，或鼠标悬停高亮）
        # 临时：枚举所有可见窗口供选择
        from ..core.capture import list_visible_windows
        windows = list_visible_windows()
        if not windows:
            self.log_text.append("[错误] 未找到可见窗口")
            return

        # 简单起见，取第一个匹配投屏关键词的窗口
        # 后续改为弹窗列表让用户选择
        for w in windows:
            title = w["title"]
            # 常见投屏窗口关键词
            keywords = ["vivo", "投屏", "scrcpy", "mirror", "screen", "phone"]
            if any(kw in title.lower() for kw in keywords):
                self._target_window_rect = (w["left"], w["top"], w["width"], w["height"])
                self.lbl_window_info.setText(
                    f"已选择: {title} ({w['width']}x{w['height']} @ {w['left']},{w['top']})"
                )
                self.lbl_window_info.setStyleSheet("color: green;")
                self.log_text.append(f"[成功] 已定位投屏窗口: {title}")
                return

        # 没找到匹配的，列出所有窗口让用户知道
        titles = [w["title"] for w in windows[:10]]
        self.log_text.append(f"[提示] 未自动匹配到投屏窗口，当前可见窗口: {titles}")
        self.log_text.append("[提示] 请在日志中查看窗口列表，后续版本将提供选择对话框")

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

    def _on_stop(self):
        """停止执行"""
        if not self._running:
            return
        self._running = False
        self.log_text.append("[操作] 停止执行")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.statusBar().showMessage("已停止 | F9 开始")


def run_app():
    """启动应用"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
