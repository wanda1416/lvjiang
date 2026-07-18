"""窗口操作混入类 - 窗口扫描、定位、截屏、DPI 检测"""

import ctypes
from ctypes import wintypes

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from loguru import logger


class WindowOpsMixin:
    """窗口扫描/定位/截屏混入类

    依赖主类提供:
        _target_window, _scanned_windows, _overlay, _capture, _last_capture,
        _layout_manager, _running, btn_locate, lbl_window_info, window_combo,
        preview_label, log_text, statusBar(), _refresh_run_button()
    """

    # ─── 后端模式切换 ──────────────────────────────────────

    def _apply_backend_ui(self, mode: str):
        """根据当前后端模式调整分组标题/定位按钮文案/后台开关可见性。
        两个扫描按钮始终保留，用户点哪个即切到哪个模式。
        """
        if mode == "adb":
            self.window_group.setTitle("目标设备")
            self.btn_locate.setText("连接")
            self.chk_bg_mode.setVisible(False)
        else:
            self.window_group.setTitle("目标窗口")
            self.btn_locate.setText("定位")
            self.chk_bg_mode.setVisible(True)

    # ─── 窗口扫描 ──────────────────────────────────────────

    def _on_scan_window(self):
        """扫描所有可见窗口，填充列表（切换到 Windows 投屏模式）"""
        if self._running:
            self.log_text.append("[提示] 请先停止当前任务，再重新扫描窗口")
            return

        # 切到 windows 模式：清理可能存在的 ADB 资源，恢复 Windows 输入控制器
        self._teardown_adb_backend()
        self._backend = "windows"
        self._input = self._win_input
        self._apply_backend_ui("windows")

        had_target = self._target_window is not None
        self._target_window = None
        self._overlay.hide_border()
        self.btn_locate.setEnabled(False)
        self.lbl_window_info.setText("未定位窗口")
        self.lbl_window_info.setStyleSheet("color: gray;")
        self.statusBar().showMessage("正在扫描窗口...")
        self._refresh_run_button()
        if had_target:
            self.log_text.append("[状态] 重新扫描窗口，旧定位已失效")

        from ..core.desktop import list_visible_windows
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

        # 自动匹配 window_title
        keyword = self._layout_manager.get_window_title()
        if keyword:
            for i, w in enumerate(self._scanned_windows):
                if keyword in w["title"]:
                    self.window_combo.setCurrentIndex(i)
                    self._on_locate_window()
                    self.log_text.append(f"[扫描] 已自动匹配窗口: {w['title']}（关键字: {keyword}）")
                    return
            self.log_text.append(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，未匹配到关键字「{keyword}」")
        else:
            self.log_text.append(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，请下拉选择目标窗口")
        self.btn_locate.setEnabled(True)
        self.lbl_window_info.setText("请下拉选择目标窗口...")
        self.lbl_window_info.setStyleSheet("color: orange;")
        self.statusBar().showMessage("已扫描窗口 | 请下拉选择目标窗口并点击定位")

    def _on_window_selected(self, index):
        """下拉框选择了某项时，启用定位按钮"""
        self.btn_locate.setEnabled(index >= 0)

    # ─── ADB 设备扫描/连接 ─────────────────────────────────

    def _on_scan_devices(self):
        """扫描已连接（device 状态）的设备，填充下拉框（切换到 ADB 设备模式）"""
        if self._running:
            self.log_text.append("[提示] 请先停止当前任务，再重新扫描设备")
            return

        # 切到 adb 模式：清理旧 ADB 资源与 Windows 定位状态
        self._teardown_adb_backend()
        self._backend = "adb"
        self._apply_backend_ui("adb")
        if self._target_window is not None:
            self._target_window = None
            self._overlay.hide_border()

        self._device_ready = False
        self.btn_locate.setEnabled(False)
        self.lbl_window_info.setText("未连接设备")
        self.lbl_window_info.setStyleSheet("color: gray;")
        self.statusBar().showMessage("正在扫描设备...")
        self._refresh_run_button()

        from ..core.adb import list_adb_devices
        devices = list_adb_devices()
        self._scanned_windows = devices
        self.window_combo.clear()

        if not devices:
            self.log_text.append("[错误] 未找到 ADB 设备，请确认已连接并开启 USB 调试授权")
            self.statusBar().showMessage("未连接设备 | 未找到 ADB 设备")
            return

        for d in devices:
            label = d["serial"] + (f"  ({d['model']})" if d["model"] else "")
            self.window_combo.addItem(label, d)
        self.btn_locate.setEnabled(True)
        self.lbl_window_info.setText("请下拉选择设备并点击连接...")
        self.lbl_window_info.setStyleSheet("color: orange;")
        self.log_text.append(f"[扫描] 找到 {len(devices)} 台设备，请选择并点击连接")
        self.statusBar().showMessage("已扫描设备 | 请选择设备并点击连接")

    def _on_connect_device(self):
        """ADB 模式：连接选中设备（adb shell input + adb screencap，无需 minicap/minitouch 二进制）"""
        d = self.window_combo.currentData()
        if not d:
            return
        from ..core.adb import AdbDevice
        from ..core.adb import create_capture_backend, create_input_backend

        # 若已连接旧设备，先清理资源
        self._teardown_adb_backend()

        try:
            device = AdbDevice(serial=d["serial"])
            w, h = device.get_resolution()
            if w <= 0 or h <= 0:
                self.log_text.append("[错误] 无法获取设备分辨率")
                self.statusBar().showMessage("连接失败 | 无法获取分辨率")
                return
        except Exception as e:
            logger.error(f"连接设备失败: {e}")
            self.log_text.append(f"[错误] 连接设备失败: {e}")
            self.statusBar().showMessage("连接失败 | 详见日志")
            return

        # 创建输入控制器（adb shell input）
        self._input = create_input_backend(device=device, delay_config=self._user_config.delay)

        # 创建截图后端（adb screencap）
        self._capture = create_capture_backend(device=device)
        if not self._capture.start():
            self.log_text.append("[错误] adb screencap 不可用")
            self.statusBar().showMessage("连接失败 | screencap 不可用")
            return

        self._device = device
        self._device_ready = True
        self.lbl_window_info.setText(f"已连接: {d['serial']}  |  分辨率: {w}x{h}")
        self.lbl_window_info.setStyleSheet("color: green;")
        self.log_text.append(f"[连接成功] {d['serial']} ({w}x{h})")
        self.statusBar().showMessage(f"已连接设备 {d['serial']} | F9 开始 | F10 停止")
        self._refresh_run_button()
        self._capture_preview()

    def _teardown_adb_backend(self):
        """清理 ADB 后端资源，用于重连或退出"""
        self._device_ready = False
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception:
                pass
            self._capture = None
        self._input = None
        self._device = None

    # ─── 窗口定位 ──────────────────────────────────────────

    def _on_locate_window(self):
        """定位选中的窗口，实时获取其当前坐标（ADB 模式转为连接设备）"""
        if getattr(self, "_backend", "windows") == "adb":
            self._on_connect_device()
            return
        w = self.window_combo.currentData()
        if not w:
            return
        self._refresh_window_rect(w)
        self._target_window = w

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
        self._overlay.show_border(w['left'], w['top'], w['width'], w['height'])
        self._overlay.set_color("red")
        self._refresh_run_button()
        self._capture_preview()

        # 定位成功后启用后台模式开关，并默认勾选（后台模式为默认）
        if hasattr(self, 'chk_bg_mode'):
            self.chk_bg_mode.setEnabled(True)
            self.chk_bg_mode.blockSignals(True)
            self.chk_bg_mode.setChecked(True)
            self.chk_bg_mode.blockSignals(False)
            # 默认使用 PostMessage 后台模式，构造时传入 hwnd
            from ..core.desktop import PostMessageInput
            self._input = PostMessageInput(
                delay_config=self._user_config.delay,
                hwnd=w["hwnd"],
            )

    def _on_bg_mode_changed(self, state):
        """后台模式开关切换：在 PostMessageInput / SendInputInput 之间替换整个 _input 实例"""
        if not self._target_window:
            return
        hwnd = self._target_window["hwnd"]
        if bool(state):
            from ..core.desktop import PostMessageInput
            self._input = PostMessageInput(
                delay_config=self._user_config.delay,
                hwnd=hwnd,
            )
            self.log_text.append("[模式] 已切换到后台模式（PostMessage，不移动光标）")
        else:
            from ..core.desktop import SendInputInput
            self._input = SendInputInput(delay_config=self._user_config.delay)
            self.log_text.append("[模式] 已切换到前台模式（SendInput，移动光标）")

    # ─── 截屏 ─────────────────────────────────────────────

    def _capture_preview(self):
        """截取已定位窗口/设备的截图并展示在预览区。"""
        img = self._grab_capture_image()
        if img is None:
            if self.preview_label.isVisible():
                self.preview_label.setText("截屏失败")
            return
        self._last_capture = img
        try:
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

    def _grab_capture_image(self) -> np.ndarray | None:
        """按 backend 获取一帧截图（numpy BGR），失败返回 None"""
        if getattr(self, "_backend", "windows") == "adb":
            if not self._device_ready or self._capture is None:
                return None
            return self._capture.capture()
        # windows 投屏窗口
        if not self._target_window:
            return None
        from ..core.desktop import DesktopCapture
        if self._capture is None:
            self._capture = DesktopCapture()
        w = self._target_window
        self._capture.set_capture_region(w['left'], w['top'], w['width'], w['height'])
        return self._capture.capture()

    def _get_last_capture(self) -> np.ndarray | None:
        """获取最近一次截屏图片（numpy BGR）"""
        return self._last_capture

    def _refresh_capture(self) -> tuple[np.ndarray | None, str | None]:
        """重新截取当前窗口/设备截图（用于区域编辑器刷新）
        返回 (image, error_message)，成功时 error_message 为 None
        """
        if getattr(self, "_backend", "windows") == "adb":
            if not self._device_ready or self._capture is None:
                return None, "请先在主窗口连接设备"
            img = self._capture.capture()
            if img is not None:
                self._last_capture = img
                return img, None
            return None, "截图失败"
        if not self._target_window:
            return None, "请先在主窗口定位窗口"
        try:
            from ..core.desktop import DesktopCapture
            if self._capture is None:
                self._capture = DesktopCapture()
            w = self._target_window
            self._capture.set_capture_region(
                w['left'], w['top'], w['width'], w['height']
            )
            img = self._capture.capture()
            if img is not None:
                self._last_capture = img
                return img, None
            return None, "截图失败"
        except Exception as e:
            logger.error(f"刷新截图失败: {e}")
            return None, f"截图失败: {e}"

    # ─── Win32 工具 ───────────────────────────────────────

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
