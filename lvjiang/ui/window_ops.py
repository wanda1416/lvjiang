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

    # ─── 窗口扫描 ──────────────────────────────────────────

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
        self._refresh_run_button()
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

    # ─── 窗口定位 ──────────────────────────────────────────

    def _on_locate_window(self):
        """定位选中的窗口，实时获取其当前坐标"""
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

        # 定位成功后启用后台模式开关
        if hasattr(self, 'chk_bg_mode'):
            self.chk_bg_mode.setEnabled(True)

    def _on_bg_mode_changed(self, state):
        """后台模式开关切换"""
        enabled = bool(state)
        if enabled and self._target_window:
            self._input.set_background_mode(True, hwnd=self._target_window["hwnd"])
            self.log_text.append("[模式] 已切换到后台模式（PostMessage，不移动光标）")
        else:
            self._input.set_background_mode(False)
            self.log_text.append("[模式] 已切换到前台模式（SendInput，移动光标）")

    # ─── 截屏 ─────────────────────────────────────────────

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
            img = self._capture.capture()
            if img is None:
                self.preview_label.setText("截屏失败")
                return
            self._last_capture = img
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

    def _refresh_capture(self) -> tuple[np.ndarray | None, str | None]:
        """重新截取当前窗口截图（用于区域编辑器刷新）
        返回 (image, error_message)，成功时 error_message 为 None
        """
        if not self._target_window:
            return None, "请先在主窗口定位窗口"
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
