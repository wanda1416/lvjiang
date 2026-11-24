"""窗口/设备操作混入类 - 目标管理、窗口定位、ADB 连接与截屏"""

import ctypes
import time
from ctypes import wintypes

import numpy as np
from loguru import logger
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QImage, QPixmap
from PyQt6.QtWidgets import QMenu, QTreeWidgetItem

from ...i18n import tr
from ..execution.feedback import clear_start_failures
from .adb_device_ops import (
    _AdbConnSignalBridge as _AdbConnSignalBridge,
)
from .adb_device_ops import (
    _AdbDeviceOpsMixin,
)
from .adb_device_ops import (
    _DeviceWorker as _DeviceWorker,
)
from .adb_device_ops import (
    _pending_uncertain as _pending_uncertain,
)
from .adb_device_ops import (
    _WirelessScanDialog as _WirelessScanDialog,
)


def bg_capture_tip() -> str:
    """「后台截图」的悬停提示（创建控件与刷新可用性共用一份口径）

    显式标明这是实验能力并且不受理不可用反馈：WGC 取帧路径和一部分脚本的识别/时序
    假设还没磨合好，与其让用户把「脚本跑不通」当成 bug 报回来，不如在勾选前就说清。
    """
    return tr(
        "实验性功能：很多脚本暂时无法在该模式下跑通，不受理该功能的不可用反馈。\n"
        "用 Windows Graphics Capture 取帧，游戏窗口被别的窗口盖住也能截图；"
        "窗口最小化时仍然拿不到画面。仅本次运行期间生效")


def _find_auto_connect_window_index(
        windows: list[dict], title_keyword: str) -> int | None:
    """返回标题自动连接候选；其他律匠窗口只能手动选择。"""
    if not title_keyword:
        return None
    for index, window in enumerate(windows):
        title = str(window.get("title") or "")
        if "律匠" in title:
            continue
        if title_keyword in title:
            return index
    return None




class WindowOpsMixin(_AdbDeviceOpsMixin):
    """窗口扫描/定位/截屏混入类

    依赖主类提供:
        _target_window, _scanned_windows, _overlay, _capture, _last_capture,
        _layout_manager, _running, btn_locate, window_combo,
        preview_label, log_text, statusBar(), _refresh_run_button()
    scrcpy 帧信号:
        主类需定义 _scrcpy_frame_ready = pyqtSignal(object) 并连接 _on_scrcpy_frame_ui
    """

    # 流式截图态的类级兜底：连接成功前被读也有明确默认值
    _scrcpy_streaming = False

    # ─── 已连接目标 / 执行目标 ─────────────────────────────

    def _active_execution_target(self):
        return self._execution_targets.active()

    def _set_locate_enabled(self, enabled: bool) -> None:
        """记录「候选侧是否允许定位」，并与运行锁取与后落到按钮上。

        定位按钮原来由扫描流程的六处 setEnabled 各自决定，运行锁再往里插一处
        就会互相覆盖——扫描结束把它打开，任务还在跑；任务结束把它打开，可是
        下拉框里一个候选都没有。所以候选侧的意图单独存一份，真正的可用性由
        这里统一算。
        """
        self._locate_allowed_by_candidate = enabled
        self._apply_locate_lock()

    def _apply_locate_lock(self) -> None:
        """运行中禁止定位/连接：只能扫描，不能改变连接拓扑。

        定位会替换窗口目标并 dispose 旧目标，而旧目标的 capture 正是运行中引擎
        持有的那一个（快照存的是同一个对象引用），stop 掉它等于把正在跑的任务
        的截图通道拆了。ADB 候选也走这个按钮，顺带堵住「点已连接设备把执行目标
        切走」。
        """
        allowed = getattr(self, "_locate_allowed_by_candidate", False)
        busy = False
        if self._candidate_backend == "windows":
            from .execution_targets import WINDOW_TARGET_ID
            busy = self._target_has_active_run(WINDOW_TARGET_ID)
        elif self._candidate_backend == "adb":
            candidate = self.window_combo.currentData()
            if isinstance(candidate, dict):
                existing = self._execution_targets.device(
                    str(candidate.get("serial") or ""))
                busy = bool(
                    existing is not None
                    and self._target_has_active_run(existing.id)
                )
        self.btn_locate.setEnabled(allowed and not busy)
        self.btn_locate.setToolTip(
            tr("该目标正在执行任务，不能重新定位或连接") if busy else "")

    def _sync_active_target_compat(self) -> None:
        """把当前执行目标投影到既有单目标字段。

        大量场景、采集和工作流入口仍通过这些字段访问后端。目标列表在运行中
        同样可以切换（运行实例持有自己的冻结快照），因此这些字段只代表“当前
        查看的目标”，运行线程一律不得读取，否则切换观察目标就会让引擎改道。
        """
        target = self._active_execution_target()
        if target is None:
            self._backend = None
            self._capture = None
            self._input = None
            self._device = None
            self._device_ready = False
            self._target_window = None
            self._agent = None
            self._scrcpy_streaming = False
            self._last_capture = None
            return
        self._backend = target.kind
        self._capture = target.capture
        self._input = target.input_ctrl
        self._device = target.device
        self._device_ready = target.kind == "adb" and target.ready
        self._target_window = target.window
        self._agent = target.agent
        self._scrcpy_streaming = target.streaming
        self._last_capture = target.last_capture
        # 无条件覆盖：窗口目标没有 resume_event，若这里跳过赋值，字段会继续
        # 指向上一台设备，停止窗口任务时就会误唤醒那台设备等待重连的任务。
        self._adb_resume_event = target.resume_event
        from ...core.app_controller import set_active_connected_target
        set_active_connected_target(target.id)

    def _store_active_target_compat(self) -> None:
        """把兼容字段上的后端替换写回当前目标。"""
        target = self._active_execution_target()
        if target is None:
            return
        target.capture = self._capture
        target.input_ctrl = self._input
        target.input_kind = str(getattr(self._input, "kind", "") or "")
        target.window = self._target_window
        target.device = self._device
        target.agent = getattr(self, "_agent", None)
        target.streaming = self._scrcpy_streaming
        target.last_capture = self._last_capture

    @staticmethod
    def _target_size_details(target) -> str:
        if target.kind == "adb":
            # serial 已经是名称列的内容，不在相邻一列再重复一遍
            return f"{target.width}×{target.height}" \
                if target.width and target.height else ""
        window = target.window or {}
        return f"{window.get('width', 0)}×{window.get('height', 0)}"

    @staticmethod
    def _target_connection_details(target) -> str:
        """连接信息列：固定「输入方式 · 截图方式」，每段四个汉字。

        顺序与连接选项的两个复选框槽位一致（槽 1 怎么操作、槽 2 怎么取画面），
        两处可以直接对读。原来 ADB 分支是「截图 · 执行」的反序，而且把
        ``screencap`` / ``scrcpy`` / ``ADB`` 这些实现 key 原样摆给用户看。
        """
        if target.kind == "adb":
            actual_kind = getattr(getattr(target, "input_ctrl", None), "kind", None)
            device_input = (actual_kind == "agent" if actual_kind is not None
                            else target.agent is not None)
            input_mode = tr("端侧执行") if device_input else tr("指令执行")
            # 截图方式只可能是 scrcpy / screencap（UserConfig 有白名单校验）；
            # 设备端代理只接管输入，不改变截图命令
            capture_mode = tr("流式截图") \
                if target.capture_method == "scrcpy" else tr("单帧截图")
        else:
            from ...core.desktop import WgcCapture
            input_mode = tr("后台输入") \
                if bool(getattr(target.input_ctrl, "background_mode", False)) \
                else tr("前台输入")
            capture_mode = tr("后台截图") \
                if isinstance(target.capture, WgcCapture) else tr("前台截图")
        return tr("{input_mode} · {capture_mode}").format(
            input_mode=input_mode,
            capture_mode=capture_mode,
        )

    def _target_details(self, target) -> str:
        """日志使用的完整摘要；UI 分列展示连接与状态。"""
        return " · ".join(part for part in (
            self._target_size_details(target),
            self._target_connection_details(target),
        ) if part)

    def _refresh_execution_targets_ui(self) -> None:
        tree = self.execution_target_list
        selected_id = self._execution_targets.active_target_id
        tree.blockSignals(True)
        tree.clear()
        selected_item = None
        for target in self._execution_targets.all():
            manager = getattr(self, "_run_manager", None)
            run_context = manager.run_for_target(target.id) \
                if manager is not None else None
            if run_context is None:
                state_text = tr("已连接") if target.ready else tr("已离线")
            else:
                state_text = {
                    "starting": tr("启动中"),
                    "running": tr("运行中"),
                    "pausing": tr("暂停中"),
                    "paused": tr("已暂停"),
                    "waiting_target": tr("等待重连"),
                    "stopping": tr("结束中"),
                }.get(run_context.state.value, tr("运行中"))
            item = QTreeWidgetItem([
                target.display_name,
                state_text,
                self._target_size_details(target),
                self._target_connection_details(target),
                "×",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, target.id)
            if self._identity_unconfirmed(target):
                # 连接信息列的格式由「四个汉字 · 四个汉字」固定，这类少见的
                # 身份问题放 tooltip，不挤占那两段。
                item.setToolTip(0, tr(
                    "设备标识未确认：只能按当前连接地址区分，重启或换用另一种"
                    "连接方式后会被视为新目标"))
            item.setTextAlignment(4, Qt.AlignmentFlag.AlignCenter)
            item.setToolTip(
                4, tr("断开定位") if target.kind == "windows"
                else tr("断开连接"))
            tree.addTopLevelItem(item)
            if target.id == selected_id:
                selected_item = item
        if selected_item is not None:
            tree.setCurrentItem(selected_item)
        self._resize_execution_target_columns()
        tree.blockSignals(False)
        self._apply_execution_target_colors()

    #: 连接信息列（第 4 列）：运行状态配色只作用于这一列，不动其他列。
    _CONNECTION_INFO_COLUMN = 3

    @staticmethod
    def _target_run_state(target, run_context) -> str:
        """目标此刻的可观察状态：idle / running / paused / fault。"""
        if run_context is not None and run_context.state.value == "waiting_target":
            return "fault"
        # 空闲断流时目标仍是「已连接」（能重连、能开任务），但画面不可用，
        # 不能把它显示成正常——横幅在提示同一件事。capture 是资源代理，当前代次
        # 没有绑定资源时取属性会抛错，配色不能因此打断列表刷新。
        try:
            fault = str(getattr(target.capture, "last_fault", "") or "")
        except Exception:  # noqa: BLE001 代理无可用资源
            fault = ""
        if fault:
            return "fault"
        if not target.ready:
            return "fault"
        if run_context is None:
            return "idle"
        if run_context.state.value in {"pausing", "paused"}:
            return "paused"
        return "running"

    def _target_state_brush(self, target, run_context) -> QBrush:
        """连接信息列的文字色：未运行默认、运行中绿、暂停黄、异常红。

        取主题令牌而不是写死色值，深浅色主题各自成立。
        """
        from ..widgets.theme import get_theme_manager

        tokens = get_theme_manager().tokens
        color = {
            "running": tokens.success,
            "paused": tokens.warning,
            "fault": tokens.danger,
        }.get(self._target_run_state(target, run_context))
        return QBrush(QColor(color)) if color else QBrush()

    def _apply_execution_target_colors(self) -> None:
        """按运行状态给连接信息列上色；**当前选中行不着色**。

        显式 ForegroundRole 会盖掉选中态的高亮文字，选中一行时颜色突变很扎眼，
        所以选中行一律回到默认画刷；取消选中后再恢复状态色。
        """
        tree = getattr(self, "execution_target_list", None)
        if tree is None:
            return
        current = tree.currentItem()
        manager = getattr(self, "_run_manager", None)
        tree.blockSignals(True)
        try:
            for row in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(row)
                target = self._execution_targets.get(
                    self._execution_target_id_from_item(item))
                if target is None:
                    continue
                run_context = (manager.run_for_target(target.id)
                               if manager is not None else None)
                brush = (QBrush() if item is current
                         else self._target_state_brush(target, run_context))
                item.setForeground(self._CONNECTION_INFO_COLUMN, brush)
        finally:
            tree.blockSignals(False)

    def _resize_execution_target_columns(self) -> None:
        """四个信息列均分剩余空间；文本放不下时滚动，操作列保持独立留白。"""
        if getattr(self, "_target_columns_resizing", False):
            return
        self._target_columns_resizing = True
        try:
            tree = self.execution_target_list
            metrics = tree.fontMetrics()
            content_width = max(
                [metrics.horizontalAdvance(tree.headerItem().text(column))
                 for column in range(4)] +
                [metrics.horizontalAdvance(tree.topLevelItem(row).text(column))
                 for row in range(tree.topLevelItemCount()) for column in range(4)])
            action_width = metrics.horizontalAdvance("×") + 32
            width = max(content_width + 24, (tree.viewport().width() - action_width) // 4)
            for column, desired in enumerate([width] * 4 + [action_width]):
                if tree.columnWidth(column) != desired:
                    tree.setColumnWidth(column, desired)
        finally:
            self._target_columns_resizing = False

    @staticmethod
    def _execution_target_id_from_item(item) -> str:
        if item is None:
            return ""
        return str(item.data(0, Qt.ItemDataRole.UserRole) or "")

    def _on_execution_target_item_clicked(self, item, column: int) -> None:
        if column != 4:
            return
        target_id = self._execution_target_id_from_item(item)
        if not target_id:
            return
        # 断开会 tree.clear() 掉触发本信号的那个 item。推到下一轮事件循环，
        # 让 Qt 先把这次点击处理完；捕获的是稳定 ID 而不是 item，所以推迟
        # 不会指错目标——注册表按 ID 索引正是为这种场合准备的。
        QTimer.singleShot(
            0, lambda: self._disconnect_execution_target(target_id))

    def _on_execution_target_context_menu(self, pos) -> None:
        item = self.execution_target_list.itemAt(pos)
        target_id = self._execution_target_id_from_item(item)
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        menu = QMenu(self.execution_target_list)
        if target.kind == "windows":
            background_action = menu.addAction(tr("后台输入"))
            assert background_action is not None
            background_action.setCheckable(True)
            background_action.setChecked(
                bool(getattr(target.input_ctrl, "background_mode", False)))
            from ...core.desktop import WgcCapture
            capture_action = menu.addAction(tr("后台截图"))
            assert capture_action is not None
            capture_action.setCheckable(True)
            capture_action.setChecked(isinstance(target.capture, WgcCapture))
            marker_action = menu.addAction(tr("红框标定"))
            assert marker_action is not None
            marker_action.setCheckable(True)
            marker_action.setChecked(self.chk_red_box.isChecked())
            menu.addSeparator()
            refresh_action = menu.addAction(tr("刷新位置"))
            disconnect_action = menu.addAction(tr("断开定位"))
            assert refresh_action is not None
            assert disconnect_action is not None
            chosen = menu.exec(
                self.execution_target_list.viewport().mapToGlobal(pos))
            if chosen is background_action:
                self._set_window_target_background_input(
                    target_id, background_action.isChecked())
            elif chosen is capture_action:
                self._set_window_target_background_capture(
                    target_id, capture_action.isChecked())
            elif chosen is marker_action:
                self._set_window_target_marker(
                    target_id, marker_action.isChecked())
            elif chosen is refresh_action:
                self._refresh_or_reconnect_execution_target(target_id)
            elif chosen is disconnect_action:
                self._disconnect_execution_target(target_id)
        else:
            capture_action = menu.addAction(tr("流式截图"))
            assert capture_action is not None
            capture_action.setCheckable(True)
            capture_action.setChecked(target.capture_method == "scrcpy")
            execution_action = menu.addAction(tr("端侧执行"))
            assert execution_action is not None
            execution_action.setCheckable(True)
            execution_action.setChecked(target.agent is not None)
            menu.addSeparator()
            refresh_action = menu.addAction(tr("重新连接"))
            disconnect_action = menu.addAction(tr("断开连接"))
            assert refresh_action is not None
            assert disconnect_action is not None
            chosen = menu.exec(
                self.execution_target_list.viewport().mapToGlobal(pos))
            if chosen is capture_action:
                self._set_android_target_streaming(
                    target_id, capture_action.isChecked())
            elif chosen is execution_action:
                self._reconnect_android_target(
                    target_id, device_execution=execution_action.isChecked())
            elif chosen is refresh_action:
                self._refresh_or_reconnect_execution_target(target_id)
            elif chosen is disconnect_action:
                self._disconnect_execution_target(target_id)

    def _on_execution_target_selected(self, current, _previous=None) -> None:
        if current is None:
            return
        target_id = current.data(0, Qt.ItemDataRole.UserRole)
        if not target_id or target_id == self._execution_targets.active_target_id:
            return
        self._capture_launch_draft(self._execution_targets.active_target_id)
        # 这里不中断录制：录制来源已在开始时冻结，切换观察目标只改预览。
        # 用 select 的返回值，而不是回头再查一次 active()：后者的返回类型是
        # 可空的，加守卫等于承认这里可能是 None，而实际上不可能。
        target = self._execution_targets.select(str(target_id))
        self._restore_active_target_view()
        self.append_log(
            tr("[执行目标] 已切换到 {name}").format(name=target.display_name))

    def _activate_connected_target(self, target, old) -> None:
        """新连接进入自己的编辑视图；后台重连保持当前观察目标。"""
        if old is None:
            if target.id != self._execution_targets.active_target_id:
                self._capture_launch_draft(self._execution_targets.active_target_id)
                self._execution_targets.select(target.id)
            self._restore_active_target_view()
        else:
            self._sync_active_target_compat()
            if target.id == self._execution_targets.active_target_id:
                self._ensure_target_plan()

    def _restore_active_target_view(self) -> None:
        """手动选中和删除后的自动选中共用完整投影，不依赖列表选择信号。"""
        clear_start_failures(self)
        target_id = self._execution_targets.active_target_id
        self._project_run_context_for_target(target_id)
        run_context = self._run_manager.run_for_target(target_id) if target_id else None
        frozen_draft = (
            run_context.metadata.get("launch_draft")
            if run_context is not None else None)
        if target_id:
            self._restore_launch_draft(target_id, frozen_draft)
        batch_tab = getattr(self, "_batch_tab", None)
        if batch_tab is not None:
            batch_tab.show_run(
                run_context.task_run_id
                if run_context is not None and run_context.metadata.get("batch")
                else "")
        self._sync_active_target_compat()
        self._ensure_target_plan()
        self._refresh_active_target_ui()
        self._refresh_run_button()
        redraw_logs = getattr(self, "_redraw_log_events", None)
        log_scope = getattr(self, "_log_scope_combo", None)
        # “全部任务”视图与当前目标无关，切换时无需重建；只有按目标过滤的
        # 日志才随选择变化。
        if (callable(redraw_logs) and log_scope is not None
                and log_scope.currentData() == "target"):
            redraw_logs()

    def _refresh_active_target_ui(self) -> None:
        # 横幅按目标归属：切到别的目标不能继续挂着上一台的「画面流已中断」，
        # 切回来还要能恢复，所以每次切换当前目标都重新应用一次。
        # 宿主可能只带部分混入（测试替身、插件页），缺方法时跳过。
        sync_banner = getattr(self, "_sync_channel_banner", None)
        if callable(sync_banner):
            sync_banner()
        # 选中行不着色、取消选中恢复状态色，切换目标时同样要重算一次。
        apply_colors = getattr(self, "_apply_execution_target_colors", None)
        if callable(apply_colors):
            apply_colors()
        target = self._active_execution_target()
        preview_enabled = bool(getattr(self, "_preview_enabled", False))
        if target is None:
            if preview_enabled:
                self.preview_label.clear()
                self.preview_label.setText(tr("连接目标后可预览"))
            refresh_capture_state = getattr(self, "_apply_rec_state", None)
            if callable(refresh_capture_state):
                refresh_capture_state()
            return
        if preview_enabled and target.last_capture is not None:
            self._show_preview_image(target.last_capture)
        elif preview_enabled:
            # 切换目标的核心是恢复左侧任务视图，不能在 UI 线程里顺带执行最长
            # 10 秒的 ADB screencap。用户需要新画面时使用预览区“刷新”。
            self.preview_label.clear()
            self.preview_label.setText(tr("暂无缓存画面，请点击刷新"))
        refresh_capture_state = getattr(self, "_apply_rec_state", None)
        if callable(refresh_capture_state):
            refresh_capture_state()

    def _refresh_connection_draft_ui(self, mode: str | None) -> None:
        """显示候选类型的连接草稿，不读取当前执行目标。"""
        is_adb = mode == "adb"
        is_windows = mode == "windows"
        self.chk_scrcpy.setVisible(is_adb)
        self.chk_agent.setVisible(is_adb)
        self.chk_bg_mode.setVisible(is_windows)
        self.chk_bg_capture.setVisible(is_windows)
        self.chk_red_box.setVisible(False)

        for checkbox in (
                self.chk_scrcpy, self.chk_agent,
                self.chk_bg_mode, self.chk_bg_capture):
            checkbox.setEnabled(not self._running)
        if is_adb:
            self.chk_scrcpy.blockSignals(True)
            self.chk_scrcpy.setChecked(
                self._android_connection_draft.capture_method == "scrcpy")
            self.chk_scrcpy.blockSignals(False)
            self.chk_agent.blockSignals(True)
            self.chk_agent.setChecked(
                self._android_connection_draft.device_execution)
            self.chk_agent.blockSignals(False)
        elif is_windows:
            from ...core.desktop import wgc_available
            available, unavailable_reason = wgc_available()
            if not self._window_connection_draft.background_input:
                self._window_connection_draft.background_capture = False
                reason = tr(
                    "需要先勾选「后台模式」：前台输入要求窗口在前台，配后台截图没有意义")
            elif not available:
                self._window_connection_draft.background_capture = False
                reason = unavailable_reason
            elif self._running:
                reason = tr("运行中不能修改下一次连接参数")
            else:
                reason = ""
            self.chk_bg_mode.blockSignals(True)
            self.chk_bg_mode.setChecked(
                self._window_connection_draft.background_input)
            self.chk_bg_mode.blockSignals(False)
            self.chk_bg_capture.blockSignals(True)
            self.chk_bg_capture.setChecked(
                self._window_connection_draft.background_capture)
            self.chk_bg_capture.blockSignals(False)
            self.chk_bg_capture.setEnabled(not reason)
            self.chk_bg_capture.setToolTip(reason or bg_capture_tip())

    # ─── 后端模式切换 ──────────────────────────────────────

    def _apply_backend_ui(self, mode: str):
        """根据当前候选类型调整连接按钮；不改变执行目标。"""
        self._candidate_backend = mode
        if mode == "adb":
            self.btn_locate.setText(tr("连接"))
        else:
            self.btn_locate.setText(tr("定位"))
        self._refresh_connection_draft_ui(mode)

    # ─── 窗口扫描 ──────────────────────────────────────────

    def _on_scan_window(self):
        """扫描所有可见窗口；只刷新候选，不改变连接或执行目标。"""
        from ...core.platforms import DESKTOP_BACKEND_AVAILABLE
        if not DESKTOP_BACKEND_AVAILABLE:
            # 按钮在非 Windows 已隐藏，此处为防御：投屏模式依赖 Win32 API
            self.append_log(tr("[提示] 当前平台不支持窗口投屏模式，请使用「扫描设备」"))
            return
        self._apply_backend_ui("windows")

        self._set_locate_enabled(False)
        self.statusBar().showMessage(tr("正在扫描窗口..."))

        from ...core.desktop import list_visible_windows
        self._scanned_windows = list_visible_windows()
        self.window_combo.clear()

        if not self._scanned_windows:
            self.append_log(tr("[错误] 未找到可见窗口"))
            self.statusBar().showMessage(tr("未定位窗口 | 未找到可见窗口"))
            return

        for w in self._scanned_windows:
            self.window_combo.addItem(
                f"{w['title']}  ({w['width']}x{w['height']})",
                w,
            )

        # 自动匹配 window_title（配置管理保存的 desktop_window_title）
        keyword = self._user_config.desktop_window_title
        if keyword:
            match_index = _find_auto_connect_window_index(
                self._scanned_windows, keyword)
            if match_index is not None:
                window = self._scanned_windows[match_index]
                self.window_combo.setCurrentIndex(match_index)
                self._on_locate_window()
                self.append_log(
                    f"[扫描] 已自动匹配窗口: {window['title']}（关键字: {keyword}）")
                return
            self.append_log(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，未匹配到关键字「{keyword}」")
        else:
            self.append_log(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，请下拉选择目标窗口")
        self._set_locate_enabled(True)
        self.statusBar().showMessage(tr("已扫描窗口 | 请下拉选择目标窗口并点击定位"))

    def _on_window_selected(self, index):
        """下拉框选择了某项时，启用定位按钮"""
        self._set_locate_enabled(index >= 0)

    # ─── 窗口定位 ──────────────────────────────────────────

    def _on_locate_window(self):
        """连接当前候选；窗口单例替换，Android 按 serial 累加。"""
        if self._candidate_backend == "adb":
            self._on_connect_device()
            return
        from .execution_targets import WINDOW_TARGET_ID
        if self._target_has_active_run(WINDOW_TARGET_ID):
            self.append_log(
                tr("[提示] 窗口目标正在执行任务，不能重新定位"))
            return
        w = self.window_combo.currentData()
        if not w:
            return
        if not self._refresh_window_rect(w):
            message = tr("所选窗口已消失，请重新扫描窗口")
            self.statusBar().showMessage(message)
            self.append_log(tr("[定位失败] {message}").format(
                message=message))
            return
        from ...core.app_controller import record_connected_window
        from .execution_targets import WINDOW_TARGET_ID
        record_connected_window(w)

        ratio = self._get_window_dpi_ratio(w["hwnd"])
        logger.info(
            f"目标窗口 Win32原始: ({w['left']},{w['top']},{w['width']}x{w['height']})"
            f" DPI={ratio}"
        )

        self.append_log(
            f"[定位成功] {w['title']}  "
            f"({w['width']}x{w['height']} @ {w['left']},{w['top']})"
            + (f" DPI={ratio:.1f}x" if ratio != 1.0 else "")
        )
        self._red_box_flash_timer.stop()
        self._overlay.show_border(w['left'], w['top'], w['width'], w['height'])
        self._overlay.set_color("red")
        if not self.chk_red_box.isChecked():
            self._red_box_flash_timer.start(1000)
        target = self._build_window_execution_target(w)
        old = self._execution_targets.put(target)
        if old is not None:
            self._dispose_execution_target(old)
        self._activate_connected_target(target, old)
        self._refresh_execution_targets_ui()
        self._refresh_active_target_ui()
        self._refresh_run_button()
        if (self._execution_targets.active_target_id == WINDOW_TARGET_ID
                and getattr(self, "_preview_enabled", False)):
            self._capture_preview()

    def _build_window_execution_target(self, w: dict):
        """为已定位窗口建立独占截图/输入资源。"""
        # 窗口目标有自己的截图和输入实例；连接它不触碰当前 Android 目标。
        from ...core.desktop import (
            DesktopCapture,
            PostMessageInput,
            SendInputInput,
            WgcCapture,
        )
        from .execution_targets import (
            WINDOW_TARGET_ID,
            ExecutionTarget,
            window_target_label,
        )
        want_bg_capture = (
            self._window_connection_draft.background_input
            and self._window_connection_draft.background_capture)
        capture = WgcCapture() if want_bg_capture else DesktopCapture()
        capture.set_capture_region(w["left"], w["top"], w["width"], w["height"])
        if (want_bg_capture and isinstance(capture, WgcCapture)
                and not capture.attach_hwnd(w["hwnd"])):
            capture.stop()
            capture = DesktopCapture()
            capture.set_capture_region(
                w["left"], w["top"], w["width"], w["height"])
            self.chk_bg_capture.blockSignals(True)
            self.chk_bg_capture.setChecked(False)
            self.chk_bg_capture.blockSignals(False)
        actual_bg_capture = isinstance(capture, WgcCapture)
        input_ctrl: object
        if self._window_connection_draft.background_input:
            input_ctrl = PostMessageInput(
                input_sim=self._user_config.input_sim, hwnd=w["hwnd"])
        else:
            input_ctrl = SendInputInput(
                input_sim=self._user_config.input_sim,
                target_hwnd=w["hwnd"],
            )
        return ExecutionTarget(
            id=WINDOW_TARGET_ID,
            kind="windows",
            display_name=window_target_label(w),
            capture=capture,
            input_ctrl=input_ctrl,
            input_kind=str(getattr(input_ctrl, "kind", "") or ""),
            window=w,
            width=int(w.get("width") or 0),
            height=int(w.get("height") or 0),
            capture_method="wgc" if actual_bg_capture else "mss",
        )

    def _hide_red_box_after_locate(self):
        """未勾选标定时，定位成功的红框提示只显示一秒。

        这个定时器只由 _on_locate_window 启动，要收的就是它自己一秒前亮的那个
        框，所以除了「用户有没有要求持续标定」之外不该再有别的条件。

        不能依据 `_backend` 判断：定位后一秒内可能已切换到手机，
        切换不碰定时器，而红框仍属于刚定位的窗口。
        """
        if not self.chk_red_box.isChecked():
            self._overlay.hide_border()

    def _on_red_box_changed(self, state):
        """红框标定仅控制本次运行中的持续显示，不写入用户配置。

        红框属于**窗口目标**，与当前执行目标无关，所以直接委托给
        _set_window_target_marker，不再读执行目标投影出来的 _target_window
        ——那个在执行目标是手机时为 None，会让勾选静默无效。

        复选框当前全程 setVisible(False)（唯一入口是目标列表的右键菜单），
        这里保持与右键菜单同源，是为了将来放开它时不必再想一遍。
        """
        from .execution_targets import WINDOW_TARGET_ID
        self._set_window_target_marker(WINDOW_TARGET_ID, bool(state))

    def _on_bg_mode_changed(self, state):
        """修改下一次窗口定位使用的输入方式。"""
        enabled = bool(state)
        self._window_connection_draft.background_input = enabled
        if not enabled:
            self._window_connection_draft.background_capture = False
        elif self._user_config.desktop_background_capture:
            self._window_connection_draft.background_capture = True
        self._refresh_connection_draft_ui("windows")

    def _on_bg_capture_changed(self, state):
        """修改下一次窗口定位使用的截图方式。"""
        if bool(state):
            from ...core.desktop import wgc_available
            ok, reason = wgc_available()
            if not ok:
                self.append_log(tr("[截图] 后台截图不可用：") + reason)
                self.chk_bg_capture.blockSignals(True)
                self.chk_bg_capture.setChecked(False)
                self.chk_bg_capture.blockSignals(False)
                return
        self._window_connection_draft.background_capture = bool(state)

    def _rebuild_desktop_capture(self, want_bg: bool | None = None):
        """按目标实际状态重建桌面截图后端并绑定当前窗口。

        后端实例带着会话状态（WGC 的取帧线程），切换时必须整个换掉而不是改标志位。
        WGC 建会话失败就退回 mss 并复位开关——让用户停在一个截不到图的状态比报错更糟。
        """
        from ...core.desktop import DesktopCapture, WgcCapture
        current = self._capture
        if want_bg is None:
            want_bg = isinstance(current, WgcCapture)
        if current is None or isinstance(current, WgcCapture) != want_bg:
            if current is not None:
                try:
                    current.stop()
                except Exception as exc:
                    logger.debug(f"停止旧截图后端时报错（忽略）: {exc}")
            self._capture = WgcCapture() if want_bg else DesktopCapture()

        w = self._target_window
        if not w:
            return self._capture
        capture = self._capture
        assert capture is not None
        capture.set_capture_region(w["left"], w["top"], w["width"], w["height"])
        if isinstance(capture, WgcCapture) and not capture.attach_hwnd(w["hwnd"]):
            self.append_log(
                tr("[截图] 后台截图启动失败，已退回前台截图（窗口最小化时用不了）"))
            try:
                capture.stop()
            except Exception:
                pass
            self._capture = DesktopCapture()
            self._capture.set_capture_region(
                w["left"], w["top"], w["width"], w["height"])
        self._store_active_target_compat()
        return self._capture

    def _on_capture_method_changed(self, state):
        """修改下一次设备连接使用的截图方式。"""
        self._android_connection_draft.capture_method = (
            "scrcpy" if state else "screencap")

    def _on_agent_mode_changed(self, state):
        """修改下一次设备连接使用的执行方式。"""
        self._android_connection_draft.device_execution = bool(state)
        label = tr("端侧执行（需安装律匠 App）") if state \
            else tr("指令执行（adb shell input）")
        self.append_log(f"[模式] 安卓输入方式: {label}（下次连接生效）")

    # ─── 截屏 ─────────────────────────────────────────────

    def _capture_preview(self):
        """截取已定位窗口/设备的截图并展示在预览区。"""
        img = self._grab_capture_image()
        if img is None:
            if self.preview_label.isVisible():
                self.preview_label.setText(tr("截屏失败"))
            return
        self._show_preview_image(img)

    def _show_preview_image(self, img):
        """把一帧画面渲染到预览区并记为最近一次截图。

        取帧与渲染分开：截屏按钮已经抓过一帧，再调 _capture_preview 会为了刷新
        预览白抓第二次——两帧之间画面可能已经变了，存下来的和看到的还对不上。
        """
        self._last_capture = img
        target = self._active_execution_target()
        if target is not None:
            target.last_capture = img
        try:
            h, w_img = img.shape[:2]
            rgb = np.ascontiguousarray(img[:, :, ::-1])
            fmt = QImage.Format.Format_RGB888
            qimg = QImage(bytes(rgb.data), w_img, h, w_img * 3, fmt).copy()
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
            copy_button = getattr(self, "btn_copy_capture", None)
            if copy_button is not None:
                copy_button.setEnabled(True)
                copy_button.setToolTip(tr("复制当前预览的原始图像到剪贴板，可用 Ctrl+V 粘贴，不保存文件"))
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
        capture = self._rebuild_desktop_capture()
        return capture.capture() if capture is not None else None

    def _get_last_capture(self) -> np.ndarray | None:
        """获取最近一次截屏图片（numpy BGR）"""
        return self._last_capture

    def _refresh_capture(self) -> tuple[np.ndarray | None, str | None]:
        """重新截取当前窗口/设备截图（用于场景编辑器刷新）
        返回 (image, error_message)，成功时 error_message 为 None
        """
        if self._backend == "adb":
            if not self._device_ready or self._capture is None:
                return None, tr("请先在主窗口连接设备")
            img = self._capture.capture()
            if img is not None:
                self._last_capture = img
                return img, None
            return None, tr("截图失败")
        if not self._target_window:
            return None, tr("请先在主窗口定位窗口")
        try:
            capture = self._rebuild_desktop_capture()
            img = capture.capture() if capture is not None else None
            if img is not None:
                self._last_capture = img
                return img, None
            return None, tr("截图失败")
        except Exception as e:
            logger.error(f"刷新截图失败: {e}")
            return None, f"截图失败: {e}"

    # ─── 运行期窗口重绑 ───────────────────────────────────

    def _on_target_window_rebound(self, target_id: str, window: dict):
        """工作流重启客户端后回调：把定位状态挪到新窗口上。

        运行在工作流线程，因此只改该运行实例绑定目标的纯数据，不碰任何控件；
        当前 UI 可能正在查看另一台设备，不能再依赖 `_target_window` 投影。
        """
        execution_target = self._execution_targets.get(target_id)
        if execution_target is None or execution_target.window is None:
            return
        target = execution_target.window
        for key in ("hwnd", "pid", "title", "executable",
                    "left", "top", "width", "height"):
            if window.get(key) is not None:
                target[key] = window[key]
        from .execution_targets import window_target_label
        execution_target.display_name = window_target_label(target)
        execution_target.status = "connected"
        if execution_target.handle is not None:
            execution_target.handle.update_window(target)
        logger.info(
            f"[定位跟随] {target_id} 客户端已重启，"
            f"跟随到新窗口 hwnd={target.get('hwnd')}")

    # ─── Win32 工具 ───────────────────────────────────────

    def _refresh_window_rect(self, w: dict) -> bool:
        """验证窗口身份并刷新位置；窗口消失或已换进程时返回 False。"""
        user32 = ctypes.windll.user32
        hwnd_value = int(w.get("hwnd") or 0)
        hwnd = wintypes.HWND(hwnd_value)
        if not hwnd or not user32.IsWindow(hwnd):
            logger.warning(f"刷新窗口位置失败：HWND 已失效 hwnd=0x{hwnd_value:X}")
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        expected_pid = int(w.get("pid") or 0)
        if not pid.value:
            logger.warning(
                f"刷新窗口位置失败：无法读取窗口进程 hwnd=0x{hwnd_value:X}")
            return False
        if expected_pid and int(pid.value) != expected_pid:
            logger.warning(
                f"刷新窗口位置失败：HWND 已被其他进程复用 "
                f"hwnd=0x{hwnd_value:X} expected_pid={expected_pid} "
                f"actual_pid={pid.value}")
            return False
        rect = wintypes.RECT()
        set_last_error = getattr(ctypes, "set_last_error", None)
        if set_last_error is not None:
            set_last_error(0)
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
            logger.warning(
                f"刷新窗口位置失败：GetWindowRect error={get_last_error()} "
                f"hwnd=0x{hwnd_value:X} pid={pid.value}")
            return False
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            logger.warning(
                f"刷新窗口位置失败：窗口矩形无效 hwnd=0x{hwnd_value:X} "
                f"pid={pid.value} rect=({rect.left},{rect.top},"
                f"{rect.right},{rect.bottom})")
            return False
        w['left'] = rect.left
        w['top'] = rect.top
        w['width'] = width
        w['height'] = height
        return True

    def _get_window_dpi_ratio(self, hwnd: int) -> float:
        """返回目标窗口所在屏幕的 DPI 缩放比，仅用于日志展示。"""
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(wintypes.HWND(hwnd))
            if dpi:
                return dpi / 96
        except Exception as e:
            logger.debug(f"获取窗口 DPI 失败: {e}")
        return 1.0

    # ─── 画面流健康接线 ───────────────────────────────────

    def _install_stream_hooks(
            self, capture, target_id: str, generation: int, bridge, device) -> None:
        """把「画面流故障」与「恢复钩子」接到宿主。

        故障回调来自解码线程/看门狗线程，必须经 Qt 信号转到主线程再碰控件；
        恢复钩子走 ``AndroidRecovery.recover_stream``（阶梯重试 + 无限等待人工），
        界面与运行层都不需要知道重试次数与退避细节。
        """
        from ...core.android import AndroidStreamCapture
        if not isinstance(capture, AndroidStreamCapture):
            return
        capture.set_on_stream_lost(
            lambda kind, detail, tid=target_id, gen=generation:
            self._on_stream_lost_event(tid, kind, detail, gen))
        capture.set_stream_recovery(
            lambda stream, dev=device: dev.recovery_coordinator.recover_stream(stream))

    def _on_stream_lost_event(
            self, target_id: str, kind: str, detail: str, generation: int | None) -> None:
        """解码/看门狗线程 → Qt 信号；代次不符的旧流不得污染已恢复的新目标。"""
        target = self._execution_targets.get(target_id)
        if target is None or target.handle is None:
            return
        if generation is not None and target.handle.binding().generation != generation:
            return
        bridge = target.connection_bridge
        if bridge is None:
            return
        try:
            bridge.notify_stream_lost(kind, detail)
        except RuntimeError:
            # 目标已被重建释放，旧流晚到的通知直接丢弃
            pass

    # ─── scrcpy 帧回调 ────────────────────────────────────

    def _on_scrcpy_frame(
            self, target_id: str, bgr: np.ndarray,
            generation: int | None = None):
        """scrcpy 解码线程回调：通过 Qt 信号将帧转发到 UI 线程，并分叉喂给录屏器"""
        target = self._execution_targets.get(target_id)
        if (target is None or generation is not None
                and target.handle is not None
                and target.handle.binding().generation != generation):
            return
        if target is not None:
            target.last_capture = bgr
        # 隐藏预览或查看其他目标时不向 Qt 主线程投递帧。缓存仍在目标上更新，
        # 再次展开预览即可立即显示最近一帧。
        if (getattr(self, "_preview_enabled", False)
                and target_id == self._execution_targets.active_target_id
                and hasattr(self, "_scrcpy_frame_ready")):
            # 自动化和录屏仍消费原始帧率；主界面预览最多 10 FPS，避免高分辨率
            # BGR 转换、QImage 复制与缩放持续占满 UI 线程。
            now = time.monotonic()
            emitted_at = getattr(self, "_preview_frame_emit_at", {})
            if now - emitted_at.get(target_id, 0.0) >= 0.1:
                emitted_at[target_id] = now
                self._preview_frame_emit_at = emitted_at
                self._scrcpy_frame_ready.emit(target_id, bgr)
        # 录屏分叉：push 仅入队不阻塞解码线程，暂停/停止态内部直接丢弃。
        # 来源是开始录制时冻结的目标，不是当前预览目标——否则切换观察目标会
        # 把两台设备的画面静默拼进同一个视频。
        rec = self._screen_recorder
        if rec is not None and target_id == getattr(
                self, "_record_target_id", None):
            rec.push(bgr)

    def _on_scrcpy_frame_ui(self, target_id: str, bgr: np.ndarray):
        """UI 线程槽：更新预览区显示（由 _scrcpy_frame_ready 信号触发）

        预览隐藏时仅更新 _last_capture，跳过 BGR→RGB→QPixmap 转换以节省 CPU。
        """
        if target_id != self._execution_targets.active_target_id:
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
            qimg = QImage(bytes(rgb.data), w_img, h, w_img * 3, fmt).copy()
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self.preview_label.setPixmap(scaled)
        except Exception as e:
            logger.debug(f"[scrcpy] 预览更新失败: {e}")

    @staticmethod
    def _identity_unconfirmed(target) -> bool:
        """该目标的身份是否只能靠 transport 地址区分。"""
        return target.kind == "adb" and not bool(
            target.metadata.get("device_identity_stable", True))

    def _unconfirmed_identity_conflict(self, target_id: str, stable: bool):
        """返回与待连接设备身份无法区分的已连接目标；没有冲突返回 None。

        同一个 target_id 的重连不算冲突：那就是同一个目标换了条 transport。
        """
        if stable:
            return None
        return next(
            (other for other in self._execution_targets.all()
             if other.id != target_id and self._identity_unconfirmed(other)),
            None,
        )

    @staticmethod
    def _release_rejected_connection(capture, agent) -> None:
        """释放一条被拒绝的连接已经建立的资源，不影响任何已登记目标。"""
        for name, resource, close in (
            ("截图后端", capture, "stop"), ("设备端代理", agent, "close"),
        ):
            if resource is None:
                continue
            try:
                getattr(resource, close)()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"释放被拒绝连接的{name}失败: {exc}")

    def _abort_recording_for_target(self, target_id: str, reason: str) -> None:
        """只有被改动的目标正是录制来源时，才先安全结束当前录制。

        换绑截图后端或断开连接会让帧流中断，两次 binding 的帧不能静默拼成
        一个视频；但动的是别的目标时，正在录的那一路不该被牵连。
        """
        if target_id != getattr(self, "_record_target_id", None):
            return
        recorder = getattr(self, "_screen_recorder", None)
        abort_recording = getattr(self, "_abort_screen_record", None)
        if recorder is not None and callable(abort_recording):
            abort_recording(reason)

    def _rebind_target_resources(self, target) -> int:
        """资源换绑后同步 TargetHandle，返回新的资源代次。

        任务持有的是 TargetHandle 而不是裸后端引用，所以只改
        ``target.capture/input_ctrl`` 等于只改了 UI 投影：handle 仍指向上一次
        连接时的绑定，下一次启动会把已经 ``stop()`` 的后端喂给引擎。流式截图
        停掉后 ``capture()`` 仍返回最后一帧，错误不会报出来，工作流直接按几分钟
        前的画面点击，所以每处换绑都必须走这里。
        """
        if target.handle is None:
            from .execution_targets import TargetHandle
            target.handle = TargetHandle(target)
            return target.handle.binding().generation
        return target.handle.rebind(target).generation

    def _target_has_active_run(self, target_id: str) -> bool:
        """目标是否被运行实例占用；兼容独立测试宿主尚未提供 RunManager。"""
        manager = getattr(self, "_run_manager", None)
        if manager is None:
            return bool(getattr(self, "_running", False))
        return manager.run_for_target(target_id) is not None
