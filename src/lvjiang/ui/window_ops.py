"""窗口操作混入类 - 窗口扫描、定位、截屏、DPI 检测"""

import ctypes
from ctypes import wintypes

import numpy as np
from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap


class WindowOpsMixin:
    """窗口扫描/定位/截屏混入类

    依赖主类提供:
        _target_window, _scanned_windows, _overlay, _capture, _last_capture,
        _layout_manager, _running, btn_locate, lbl_window_info, window_combo,
        preview_label, log_text, statusBar(), _refresh_run_button()
    scrcpy 帧信号:
        主类需定义 _scrcpy_frame_ready = pyqtSignal(object) 并连接 _on_scrcpy_frame_ui
    """

    # 流式截图态的类级兜底：连接成功前被读也有明确默认值
    _scrcpy_streaming = False

    # ─── 后端模式切换 ──────────────────────────────────────

    def _apply_backend_ui(self, mode: str):
        """根据当前后端模式调整定位按钮文案。
        两个扫描按钮始终保留，用户点哪个即切到哪个模式。
        """
        if mode == "adb":
            self.btn_locate.setText("连接")
        else:
            self.btn_locate.setText("定位")

    # ─── 窗口扫描 ──────────────────────────────────────────

    def _on_scan_window(self):
        """扫描所有可见窗口，填充列表（切换到 Windows 投屏模式）"""
        if self._running:
            self.log_text.append("[提示] 请先停止当前任务，再重新扫描窗口")
            return

        # 切到 windows 模式：清理可能存在的 ADB 资源，恢复 Windows 输入控制器
        self._teardown_adb_backend()
        self._set_connected_ui(False)
        self._backend = "windows"
        self._input = self._win_input
        self._apply_backend_ui("windows")

        # 显示后台模式开关，隐藏流式截图开关
        if hasattr(self, "chk_bg_mode"):
            if not self.chk_bg_mode.isVisible():
                # 首次进入 Windows 模式，从配置读取初始状态
                self.chk_bg_mode.blockSignals(True)
                self.chk_bg_mode.setChecked(self._user_config.desktop_background_input)
                self.chk_bg_mode.blockSignals(False)
            self.chk_bg_mode.setVisible(True)
            self.chk_bg_mode.setEnabled(True)
        if hasattr(self, "chk_scrcpy"):
            self.chk_scrcpy.setVisible(False)

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

        # 自动匹配 window_title（配置管理保存的 desktop_window_title）
        keyword = self._user_config.desktop_window_title
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
        self._set_connected_ui(False)
        self._backend = "adb"
        self._apply_backend_ui("adb")

        # 显示流式截图开关，隐藏后台模式开关
        if hasattr(self, "chk_scrcpy"):
            if not self.chk_scrcpy.isVisible():
                # 首次进入 ADB 模式，从配置读取初始状态
                is_scrcpy = self._user_config.adb_capture_streaming
                self.chk_scrcpy.blockSignals(True)
                self.chk_scrcpy.setChecked(is_scrcpy)
                self.chk_scrcpy.blockSignals(False)
            self.chk_scrcpy.setVisible(True)
            self.chk_scrcpy.setEnabled(True)
        if hasattr(self, "chk_bg_mode"):
            self.chk_bg_mode.setVisible(False)
        if self._target_window is not None:
            self._target_window = None
            self._overlay.hide_border()

        self._device_ready = False
        self.btn_locate.setEnabled(False)
        self.lbl_window_info.setText("未连接设备")
        self.lbl_window_info.setStyleSheet("color: gray;")
        self.statusBar().showMessage("正在扫描设备...")
        self._refresh_run_button()

        from ..core.android import list_adb_devices
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
        """ADB 模式：连接选中设备（adb shell input + adb screencap/scrcpy）"""
        d = self.window_combo.currentData()
        if not d:
            return
        from ..core.android import (
            AdbDevice,
            create_capture_backend,
            create_input_backend,
        )

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

        # 创建截图后端（根据配置选择 screencap 或 scrcpy）
        capture_method = "scrcpy" if self._user_config.adb_capture_streaming else "screencap"
        self._capture = create_capture_backend(device=device, method=capture_method)
        if not self._capture.start():
            self.log_text.append(f"[错误] {capture_method} 截图后端不可用")
            self.statusBar().showMessage(f"连接失败 | {capture_method} 不可用")
            return

        # 创建输入控制器（adb shell input）
        self._input = create_input_backend(device=device, delay_config=self._user_config.input_delay)

        # scrcpy 模式下订阅帧回调，实现预览区实时视频流
        self._scrcpy_streaming = False
        if capture_method == "scrcpy":
            from ..core.android import AndroidStreamCapture
            if isinstance(self._capture, AndroidStreamCapture):
                self._capture.set_on_frame(self._on_scrcpy_frame)
                self._scrcpy_streaming = True
                logger.info("[连接] scrcpy 视频流预览已启用")

        self._device = device
        self._device_ready = True
        method_label = "scrcpy" if capture_method == "scrcpy" else "screencap"
        self.lbl_window_info.setText(f"已连接: {d['serial']}  |  分辨率: {w}x{h}  |  {method_label}")
        self.lbl_window_info.setStyleSheet("color: green;")
        self.log_text.append(f"[连接成功] {d['serial']} ({w}x{h}) [{method_label}]")
        self.statusBar().showMessage(f"已连接设备 {d['serial']} | F9 开始 | F10 停止")
        self.btn_locate.setText("断连")
        self._set_connected_ui(True)
        self._refresh_run_button()
        # screencap 模式手动刷新预览；scrcpy 模式自动推帧
        if not self._scrcpy_streaming:
            self._capture_preview()

    def _teardown_adb_backend(self):
        """清理 ADB 后端资源，用于重连或退出（仅清理资源，不改 UI）"""
        # 录屏进行中/待保存时先自动转正保存，再停止截图后端
        if self._screen_recorder is not None:
            self._abort_screen_record("断连")
        self._device_ready = False
        self._scrcpy_streaming = False
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception:
                pass
            self._capture = None
        self._input = None
        self._device = None

    def _set_connected_ui(self, connected: bool):
        """连接/断连后统一更新 UI 控件可用性"""
        self.btn_scan_window.setEnabled(not connected)
        self.btn_scan_device.setEnabled(not connected)
        self.window_combo.setEnabled(not connected)
        if connected:
            # 连接/定位后锁定所有 checkbox，防止误切换
            if hasattr(self, "chk_bg_mode"):
                self.chk_bg_mode.setEnabled(False)
            if hasattr(self, "chk_scrcpy"):
                self.chk_scrcpy.setEnabled(False)
        else:
            # 断连后恢复当前模式可见 checkbox 的可选状态
            if hasattr(self, "chk_bg_mode") and self.chk_bg_mode.isVisible():
                self.chk_bg_mode.setEnabled(True)
            if hasattr(self, "chk_scrcpy") and self.chk_scrcpy.isVisible():
                self.chk_scrcpy.setEnabled(True)
        # 采集面板（录屏/截屏）随连接态刷新可用性
        if hasattr(self, "_apply_rec_state"):
            self._apply_rec_state()

    def _on_disconnect(self):
        """通用断连：根据后端模式清理资源并恢复 UI"""
        if self._backend == "adb":
            serial = self._device_combo_current_serial()
            self._teardown_adb_backend()
            self._set_connected_ui(False)
            self.btn_locate.setText("连接")
            self.lbl_window_info.setText("已断开连接")
            self.lbl_window_info.setStyleSheet("color: gray;")
            self.preview_label.setText("预览已停止")
            self.statusBar().showMessage("已断开设备连接")
            self.log_text.append(f"[断连] 设备已断开: {serial}")
        else:
            # Windows 模式：清除定位状态，但保留窗口选择
            self._target_window = None
            self._overlay.hide_border()
            self._set_connected_ui(False)
            self.btn_locate.setText("定位")
            self.lbl_window_info.setText("未定位窗口")
            self.lbl_window_info.setStyleSheet("color: gray;")
            self.statusBar().showMessage("已取消窗口定位")
            self.log_text.append("[断连] 窗口定位已清除")
        self._refresh_run_button()

    def _device_combo_current_serial(self) -> str:
        """获取当前下拉框中的设备 serial（用于日志）"""
        d = self.window_combo.currentData()
        return d.get("serial", "?") if d else "?"

    # ─── 窗口定位 ──────────────────────────────────────────

    def _on_locate_window(self):
        """定位选中的窗口 / 连接设备；已连接/定位后变为断连"""
        if self._device_ready or self._target_window is not None:
            # 已连接/已定位 → 断连
            self._on_disconnect()
            return
        if self._backend == "adb":
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
        self._set_connected_ui(True)
        self.btn_locate.setText("断连")
        self._refresh_run_button()
        self._capture_preview()

        # 定位成功后根据 checkbox 状态选择输入后端
        if hasattr(self, 'chk_bg_mode'):
            if self.chk_bg_mode.isChecked():
                from ..core.desktop import PostMessageInput
                self._input = PostMessageInput(
                    delay_config=self._user_config.input_delay,
                    hwnd=w["hwnd"],
                )
            else:
                from ..core.desktop import SendInputInput
                self._input = SendInputInput(delay_config=self._user_config.input_delay)
                self.log_text.append("[模式] 已切换到前台模式（SendInput，移动光标）")

    def _on_bg_mode_changed(self, state):
        """后台模式开关切换：在 PostMessageInput / SendInputInput 之间替换整个 _input 实例"""
        if not self._target_window:
            return
        hwnd = self._target_window["hwnd"]
        if bool(state):
            from ..core.desktop import PostMessageInput
            self._input = PostMessageInput(
                delay_config=self._user_config.input_delay,
                hwnd=hwnd,
            )
            self.log_text.append("[模式] 已切换到后台模式（PostMessage，不移动光标）")
        else:
            from ..core.desktop import SendInputInput
            self._input = SendInputInput(delay_config=self._user_config.input_delay)
            self.log_text.append("[模式] 已切换到前台模式（SendInput，移动光标）")

    def _on_capture_method_changed(self, state):
        """截图方式开关切换：在 screencap / scrcpy 之间重建截图后端"""
        method = "scrcpy" if state else "screencap"

        # 录屏依赖流式推帧，切换前自动转正保存
        if self._screen_recorder is not None:
            self._abort_screen_record("切换截图方式")

        if not self._device:
            # 未连接设备时仅更新内存配置
            self._user_config.adb_capture_streaming = state
            return

        # 已连接设备时重建截图后端
        self._user_config.adb_capture_streaming = state
        from ..core.android import create_capture_backend
        old_capture = self._capture
        self._capture = create_capture_backend(device=self._device, method=method)
        if self._capture.start():
            # scrcpy 模式订阅帧回调
            self._scrcpy_streaming = False
            if method == "scrcpy":
                from ..core.android import AndroidStreamCapture
                if isinstance(self._capture, AndroidStreamCapture):
                    self._capture.set_on_frame(self._on_scrcpy_frame)
                    self._scrcpy_streaming = True
            # 清理旧后端
            if old_capture:
                try:
                    old_capture.stop()
                except Exception:
                    pass
            mode_label = "scrcpy 流式" if method == "scrcpy" else "screencap"
            self.log_text.append(f"[模式] 已切换到 {mode_label} 截图")
            if not self._scrcpy_streaming:
                self._capture_preview()
        else:
            self.log_text.append(f"[错误] {method} 截图后端不可用，回退到 screencap")
            self._user_config.adb_capture_streaming = False
            if hasattr(self, "chk_scrcpy"):
                self.chk_scrcpy.blockSignals(True)
                self.chk_scrcpy.setChecked(False)
                self.chk_scrcpy.blockSignals(False)
            # 回退到 screencap
            self._capture = create_capture_backend(device=self._device, method="screencap")
            self._capture.start()
        # 流式状态变化后刷新采集面板可用性
        if hasattr(self, "_apply_rec_state"):
            self._apply_rec_state()

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
        if self._backend == "adb":
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
        """重新截取当前窗口/设备截图（用于场景编辑器刷新）
        返回 (image, error_message)，成功时 error_message 为 None
        """
        if self._backend == "adb":
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

    # ─── scrcpy 帧回调 ────────────────────────────────────

    def _on_scrcpy_frame(self, bgr: np.ndarray):
        """scrcpy 解码线程回调：通过 Qt 信号将帧转发到 UI 线程，并分叉喂给录屏器"""
        if hasattr(self, "_scrcpy_frame_ready"):
            self._scrcpy_frame_ready.emit(bgr)
        # 录屏分叉：push 仅入队不阻塞解码线程，暂停/停止态内部直接丢弃
        rec = self._screen_recorder
        if rec is not None:
            rec.push(bgr)

    def _on_scrcpy_frame_ui(self, bgr: np.ndarray):
        """UI 线程槽：更新预览区显示（由 _scrcpy_frame_ready 信号触发）

        预览隐藏时仅更新 _last_capture，跳过 BGR→RGB→QPixmap 转换以节省 CPU。
        """
        if not self._device_ready:
            return
        # 始终更新最新帧，供 capture() 使用
        self._last_capture = bgr
        # 预览不可见时跳过 UI 渲染
        if not self.preview_label.isVisible():
            return
        try:
            h, w_img = bgr.shape[:2]
            rgb = np.ascontiguousarray(bgr[:, :, ::-1])
            fmt = QImage.Format.Format_RGB888
            qimg = QImage(rgb.data, w_img, h, w_img * 3, fmt).copy()
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self.preview_label.setPixmap(scaled)
        except Exception as e:
            logger.debug(f"[scrcpy] 预览更新失败: {e}")
