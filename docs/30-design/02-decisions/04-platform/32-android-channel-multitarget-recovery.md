# Android ADB 与无障碍通道多目标切换及状态恢复修复方案

> 状态：已实现，含定向回归测试。

相关既有设计决策：
- [18-android-connection-recovery.md](./18-android-connection-recovery.md)（代理与 ADB 重连、输入降级、画面流故障及人工恢复边界）
- [04-execution-targets.md](./04-execution-targets.md)（单窗口、多设备连接与显式执行目标）
- [05-concurrent-target-execution.md](./05-concurrent-target-execution.md)（多执行目标并发任务与断线恢复）

---

## 1. 总修复原则（避免制造更大 Bug 的 7 条红线）

在多目标并发与通道恢复链路中，局部打补丁或顺手加锁、借用既有回调极易制造更隐蔽的死锁、跨目标串台或进程崩溃。本次修复全程遵守以下 7 条硬边界：

1. **绝不阻塞 Qt 主线程**
   - 禁止在主线程做无超时 `QThread.wait()`、多轮 ADB/Agent 重连重试，或在 `_on_connect_done` 中发起同步 `control_begin` / `control_end` RPC。
   - 启动前预检（`device_execution_busy`）只允许单次短超时探测，绝不进入恢复协调器重试循环。
2. **所有状态读写按归属隔离（`target_id` / `run_context` / 资源代次），禁止无条件全局覆盖**
   - 通道横幅、空闲流重建桥、恢复弹窗均按 `target_id` 独立维护。
   - `self._run_state` 与运行按钮仅在 `run_context is self._current_run_context` 时同步，后台目标恢复绝不覆盖当前查看目标的 UI 投影。
   - 后台线程异步回调（设备连接、空闲流重建）回到主线程必须校验请求代次或当前绑定实例身份，过期回调只释放资源、绝不反写新目标状态。
3. **收尾、释放、自动关窗路径必须幂等且零副作用**
   - `AgentClient.close()` 必须同步复位 `_owns_execution = False`；`AgentInput.end_execution()` 收尾绝不触发重连，也不得让 `AdbConnectionError` 穿透 `finally` 覆盖任务正常中断/返回。
   - 自动关闭恢复弹窗必须走**静默关闭**（先摘表、停定时器、阻断 `finished` 槽再关窗），严禁借道 `box.done(No)`（会杀掉刚恢复的健康任务）或 `box.reject()`（会误打等待日志）。
4. **不在可打断的长 I/O 上叠加粗粒度互斥锁**
   - `AndroidStreamCapture.stop()` 必须随时能立即打断卡在首帧/协议头等待中的 `start()` / `restart()`；**严禁把 `start()` / `stop()` 包进 `_lifecycle_lock`**，否则主线程断开或切单帧会被后台重建死锁 5–15 秒且无法打断。
5. **严守既有恢复与降级契约，不收紧合法入口、不放松人工确认门禁**
   - `device_execution_busy()` 遇传输/超时异常必须静默放行交工作线程按策略恢复或降级 ADB，不得提前阻断「允许降级为 ADB 输入」的正常场景。
   - `holds_confirmation=True`（已发出、结果不确定的动作待人工确认）时，重连只修通底层传输，**严禁关闭恢复弹窗、严禁置位 `resume_event`、严禁提前切 `RUNNING`**。
   - `waiting_target` 必须被各订阅方视为**活跃运行态**（禁用开始、锁配置、允许停止），但暂停按钮与托盘暂停仍仅在 `("running", "paused")` 可用，防止等待重连与暂停态叠加错乱。
6. **跨 `TargetHandle.rebind` 保持运行态契约单一来源**
   - 任务启动时冻结的 `AndroidRecoveryConfig` 随 `run_context` 保存，运行中重连必须显式延续到新 `device` 与 `input_ctrl`（连同 `stop_check`），绝不退回硬编码默认值。
   - `WorkflowEngine._execute_scoped` 在 `finally` 动态解析 `self._input.end_execution`，禁止运行前提前绑定到旧 `input_ctrl1`。
   - 运行中重连的端侧租约交接必须满足「**先释 `old_agent`，再验 `run_context` 仍活动，再取 `new_agent` 的 `control_begin`**」，且全过程不阻塞主线程。
7. **分批最小改动，先写可复现契约测试再收口**
   - 每项修复配最小必要定向回归测试，证明修复前必现的异常在修复后稳定消除；UI 用例严格保持 offscreen 限时退出，不污染项目根目录配置。

---

## 2. 确认缺陷总览

| # | 严重度 | 主题 | 核心涉及文件 | 确定性异常后果 |
|---|---|---|---|---|
| 1 | 致命 | 覆盖运行中的 `_device_thread` | `src/lvjiang/ui/main/adb_device_ops.py`, `src/lvjiang/ui/main/window.py` | 析构运行中 `QThread` 触发 Qt `qFatal`（Windows 实测退出码 `0xC0000409` 进程闪退） |
| 2 | 高 | 运行中重连丢租约、丢冻结策略、收尾误调旧 `agent1.end_execution()` | `src/lvjiang/workflows/engine/core.py`, `src/lvjiang/core/android/agent.py`, `src/lvjiang/ui/main/adb_device_ops.py`, `src/lvjiang/ui/main/run_control.py` | 手机悬浮球弹回且失互斥；正常结束反向重连泄漏转发端口；F10 正常停止崩溃为 `FAILED`；禁用 ADB 降级等冻结策略失效 |
| 3 | 高 | `WAITING_TARGET` 经切换投影后恢复不回写，且各订阅方误判为空闲 | `src/lvjiang/ui/main/run_control.py`, `src/lvjiang/ui/main/run_android_recovery.py`, `src/lvjiang/ui/main/adb_device_ops.py`, `src/lvjiang/ui/batch/batch_execution.py`, `src/lvjiang/apps/yysls/ui/tuning/tuning_tab.py`, `src/lvjiang/ui/main/tray_ops.py` | 恢复运行后暂停按钮与 F11 永久失效；批量/调律页解锁配置并显示绿色「开始」；托盘禁用「结束当前目标」且计数错乱 |
| 4 | 中高 | 通道横幅（`_adb_banner`）在恢复、切换与停止下状态脱节 | `src/lvjiang/ui/main/run_android_recovery.py`, `src/lvjiang/ui/main/run_control.py`, `src/lvjiang/ui/main/adb_device_ops.py` | 单目标 ADB 恢复后红条永不消失；切换目标后等待中横幅丢失；停止后切回旧横幅复活 |
| 5 | 中高 | `device_execution_busy()` 主线程同步卷入完整 Agent+ADB 重试并破坏状态 | `src/lvjiang/ui/main/run_control.py`, `src/lvjiang/core/android/agent.py`, `src/lvjiang/ui/batch/batch_execution.py`, `src/lvjiang/ui/main/window.py` | App 退出/断网点开始卡死 UI 10–50s；清空 `agent._status` 触发二次全套重试；离线目标仍放行启动 |
| 6 | 中 | 恢复询问弹窗在同目标重连或恢复成功后残留 | `src/lvjiang/ui/main/run_android_recovery.py`, `src/lvjiang/ui/main/adb_device_ops.py` | 任务已恢复正常运行，旧弹窗仍常驻；用户误点「否」直接杀掉健康任务 |
| 7 | 中 | 空闲流重建全局单槽、缺代次校验，及 `scrcpy_capture` 锁/打断边界缺陷 | `src/lvjiang/ui/main/run_android_recovery.py`, `src/lvjiang/core/android/scrcpy_capture.py` | 目标 A 重建阻挡目标 B；重连或切单帧后旧失败回调给健康目标挂红条；`stop()` 无法提前终止刚启动中的 `restart()` |

---

## 3. 逐项详细分析、安全边界与精确改动方案

### 3.1 缺陷 1：`_device_thread` 生命周期与过期连接隔离

- **现状与确定性复现**：
  - `src/lvjiang/ui/main/adb_device_ops.py` 的 `_on_scan_devices`、`_start_wireless_scan`、`_start_device_connection` 共用单一无 parent 的 `self._device_thread`，`_wait_device_thread()` 仅 `quit()` + `wait(3000)`。
  - 右键离线目标 A 点「重新连接」（`update_candidate_ui=False`）不禁用扫描、连接或目标右键菜单；`_DeviceWorker._do_connect()` 阻塞在 `device.get_resolution()`（15s 超时 + 5 次 ADB 重连，或健康 `scrcpy` 超 3s）。期间右键 B 重连或点「扫描设备」，`wait(3000)` 超时返回后下一行 `self._device_thread = QThread()` 析构运行中的旧线程，直接触发 Qt `qFatal`。
- **高危陷阱**：
  - 禁止把 `wait(3000)` 改为无超时 `wait()`（连离线设备会卡死主线程 15–40s）。
  - 禁止仅保活旧线程而不做请求代次校验（连续重连同一设备时慢返回的旧回调会覆盖新连接并泄漏 `capture`/`agent`）。
- **精确改动清单**：
  1. `src/lvjiang/ui/main/adb_device_ops.py`：
     - 引入 `_retired_device_threads()`（`set[QThread]`）与 `_retired_device_workers()`（保活仍在跑的 worker）；引入递增 `_device_connect_seq` 与 `_latest_connect_seq_by_target: dict[str, int]`，并用 `_candidate_connect_worker` 单独追踪前台候选连接，避免右键重连顶替后 `btn_locate` 卡死。
     - 改写 `_wait_device_thread(*, replacing_with_scan: bool = False)`：仅对非 `"connect"` 任务调 `cancel()`；当扫描被连接/重连顶替时立即恢复扫描按钮，否则短等（`wait(200)`）后把仍存活的旧线程与 worker 移入 retired 集合并绑定 `finished` 自动清理。
     - 扫描回调校验 `worker is getattr(self, "_device_worker", None)`，过期回调直接丢弃。
     - `_start_device_connection` 分配 `connect_seq`；`_on_connect_done` 解析出 `target_id` 后比对 `_latest_connect_seq_by_target.get(target_id, 0)`：过期则调用 `_release_rejected_connection(capture, agent)` 并直接返回，不触碰注册表或 UI；候选区 `btn_locate` 仅由当前最新 `_device_worker` 恢复。
  2. `src/lvjiang/ui/main/window.py`：
     - `_stop_running_qthreads()` 同步纳入 `_retired_device_threads` 集合，确保关闭程序时无残留活跃 `QThread`。

### 3.2 缺陷 2：运行中同目标重连的租约交接、收尾释放与冻结策略延续

- **现状与确定性复现**：
  - `src/lvjiang/workflows/engine/core.py` `_execute_scoped` 在 `try` 前提前捕获 `end = getattr(self._input, "end_execution", None)`（绑定旧 `input_ctrl1`）。
  - `src/lvjiang/core/android/agent.py` `AgentClient.close()` 未清 `_owns_execution`；`AgentInput.end_execution()` 调带重连的 `call("control_end")` 且只捕获 `AgentError`、漏捕 `AdbConnectionError`。
  - `src/lvjiang/ui/main/adb_device_ops.py` `_on_connect_done` 新建 `device2`/`agent2`/`input_ctrl2` 时未延续 `recovery` 与 `stop_check`；`_dispose_execution_target(old)` 调 `agent1.close()` 清空手机端 `AgentServer.pcOwner` 并恢复悬浮球，`agent2` 从未补取 `control_begin`。任务正常结束时死 `agent1` 反向重连泄漏端口；重连后按 F10 停止健康任务时 `AdbConnectionError` 穿透 `finally` 把正常中断打成 `FAILED`。
- **高危陷阱**：
  - 禁止在 `_on_connect_done`（Qt 主线程）同步调 `agent2.control_begin`（主线程可能卡死，且若先于 `agent1.close()` 必被手机端 `pcOwner` 拒绝）。
  - 禁止只清 `agent1._owns_execution` 而不改 `core.py` 动态解析 `end_execution`（否则 `agent2` 拿到租约后任务结束永不释放，手机端永久锁死）。
- **精确改动清单**：
  1. `src/lvjiang/core/android/agent.py`：
     - `AgentClient.close()` 同步复位 `self._owns_execution = False`。
     - 新增 `AgentClient.release_execution(timeout: float = 2.0) -> None`：在 `self._lock` 下，仅当 `self._owns_execution` 为真且 `self._sock is not None` 时发**单次不重试**的 `_roundtrip("control_end", timeout=timeout)`，异常时记 warning 并调 `self.close()`（绝不触发 `_retry_connection()`）；`finally` 必置 `self._owns_execution = False`。
     - `AgentInput.end_execution()` 调 `self._client.release_execution()`，外层兜底捕获 `(AgentError, AdbConnectionError)` 后 `close()`，绝不穿透异常。
     - `AgentInput` 新增 `mark_execution_pending(run_active)` 与 `ensure_execution_owned()`（在 `self._client._lock` 下校验待接管、非 `_using_adb`、`run_active()` 为真且支持 `offline_protocol` 后补取 `control_begin` 并清标记），在 `_call()` 入口同样执行一次守卫。
  2. `src/lvjiang/workflows/engine/core.py`：
     - `_execute_scoped` 删除提前捕获的 `end`，在 `finally:` 内动态取 `getattr(self._input, "end_execution", None)` 并调用。
  3. `src/lvjiang/ui/main/run_control.py`：
     - `_begin_automation` 将冻结的 `recovery` 存入 `run_context.metadata["android_recovery"]`。
  4. `src/lvjiang/ui/main/adb_device_ops.py`：
     - `_on_connect_done` 查到活动 `run_context` 时，将冻结的 `recovery`（优先 `run_context.metadata["android_recovery"]`，回退 `old_device.recovery`）写入新 `device.recovery`、调用 `input_ctrl.configure_recovery(recovery)`，并补挂 `input_ctrl.stop_check = run_context.stop_event.is_set`。
     - 若重连前 `old_agent` 存在且 `old_agent._owns_execution` 为真、新目标存在未停止的活动 `run_context` 且新 `input_ctrl` 支持租约接管：把 `old_agent` 从同步 `_dispose_execution_target(old)` 交由轻量 daemon 线程顺序完成 **先 `old_agent.release_execution(timeout=1.5)` + `old_agent.close()`，再在 `run_active()` 为真时调 `new_input_ctrl.ensure_execution_owned()`**；其余情况保持 `_dispose_execution_target(old)` 直接 `close()`。

### 3.3 缺陷 3：`WAITING_TARGET` 状态同步与多目标订阅方口径统一

- **现状与确定性复现**：
  - 目标 A 进入 `WAITING_TARGET` 后，用户切到 B 再切回 A，`_project_run_context_for_target(A)` 将 `self._run_state` 置为 `"waiting_target"` 并广播 `automation_state_changed`。
  - `batch_execution.py`、`tuning_tab.py`、`tray_ops.py` 的 `active_states` 均遗漏 `"waiting_target"`，导致批量/调律页误判为空闲、解锁配置、显示绿色「开始」，托盘禁用「结束当前目标」且误算「另有 1 个任务运行」。
  - 恢复后 `_on_adb_connection_restored` 与 `_on_connect_done` 均未回写 `self._run_state` 与按钮，导致暂停按钮与 F11 暂停热键在恢复后的整段运行期间永久失效。
- **高危陷阱**：
  - 后台目标 A 恢复时若无条件写 `self._run_state`，会把当前正在查看的空闲目标 B 错置为运行中。
  - 暂停按钮与托盘「暂停」不能把 `"waiting_target"` 加入可用集合，防止等待重连与暂停态叠加错乱。
- **精确改动清单**：
  1. `src/lvjiang/ui/main/run_control.py`：
     - 定义并导出 `STATE_WAITING_TARGET = "waiting_target"`。
     - 新增 `_sync_projected_run_state(run_context, state_value: str) -> None`：**仅当 `run_context is getattr(self, "_current_run_context", None)` 时**更新 `self._run_state = state_value` 并调用 `self._refresh_run_button()`。
  2. `src/lvjiang/ui/main/run_android_recovery.py` 与 `src/lvjiang/ui/main/adb_device_ops.py`：
     - 在进入 `WAITING_TARGET`（`_on_adb_connection_lost` / `_on_stream_lost`）与恢复 `RUNNING`/`PAUSED`（`_on_adb_connection_restored` / `_on_connect_done` 非 `holds_confirmation`）后，统一调用 `_sync_projected_run_state`。
  3. `src/lvjiang/ui/batch/batch_execution.py`、`src/lvjiang/apps/yysls/ui/tuning/tuning_tab.py`、`src/lvjiang/ui/main/tray_ops.py`：
     - 运行态判断与停止按钮可用集合补全 `STATE_WAITING_TARGET`；暂停按钮与托盘「暂停」保持仅 `("running", "paused")` 可用。

### 3.4 缺陷 4：通道横幅（`_adb_banner`）按目标归属统一收口

- **现状与确定性复现**：
  - `_on_adb_connection_lost` 未走 `_show_channel_banner`，导致 `_adb_banner_target_id` 仍为 `None`（单目标恢复时 `_hide_channel_banner` 被 `None != target_id` 挡住，红条不消失）且未记入 `_channel_banner_texts()`（切目标再切回横幅丢失）。
  - `_on_stream_lost` 运行态分支在调用 `_show_channel_banner` 之前就对非当前目标 `return`，后台断流切过去无横幅。
  - `_request_stop` 与 `_end_automation` 直接调 `banner.setVisible(False)`（且后台结束未清理），导致停止后切目标再切回旧横幅复活；`_set_android_target_streaming(..., False)` 切单帧也未收起旧断流横幅。
- **高危陷阱**：
  - 后台任务结束必须按 `context.target_id` 调 `_hide_channel_banner(context.target_id)`，禁止调无条件 `_drop_channel_banner()` 误关其他目标横幅。
  - 非当前目标调用 `_show_channel_banner` 只登记文案，禁止覆盖当前查看目标的 `statusBar`。
- **精确改动清单**：
  1. `src/lvjiang/ui/main/run_android_recovery.py`：
     - `_on_adb_connection_lost` 改为先调 `self._show_channel_banner(target_id, ...)`，仅当前目标再刷新 `statusBar`。
     - `_on_stream_lost` 运行态分支把 `self._show_channel_banner(target_id, ...)` 移到 `if target_id != active_target_id: return` 之前，`statusBar` 留在之后。
  2. `src/lvjiang/ui/main/run_control.py`：
     - `_end_automation`（含 `not is_current` 后台结束分支）与 `_request_stop` 统一按 `context.target_id` 调 `_hide_channel_banner(target_id)`（无 `context` 时仅兼容独立测试宿主回退 `_drop_channel_banner()`）。
  3. `src/lvjiang/ui/main/adb_device_ops.py`：
     - `_set_android_target_streaming(target_id, False)` 切单帧成功后调用 `_hide_channel_banner(target_id)`（若存在）。

### 3.5 缺陷 5：`device_execution_busy()` 单次轻量探测与批量去重

- **现状与确定性复现**：
  - `run_control.py:device_execution_busy()` 在 UI 主线程调 `AgentInput.begin_execution()` → `_call("control_begin")`，App 退出/断网时同步卷入 5 次 Agent 重连 + 5 次 ADB 重连卡死主线程 10–50s；降级路径清空 `agent._status = {}` 后又被 `configure_recovery` 翻回 `AGENT`，导致工作线程再跑第二遍重试；批量启动还在 `batch_execution.py` 与 `window.py` 连调两次。
- **高危陷阱**：
  - 传输/超时异常绝不可改为阻断启动（会破坏默认允许降级为 ADB 输入的正常路径）。
  - 不可删除预检或改查不带租约口径的字段（会回归手机跑离线任务时批量逐条报错的问题）。
- **精确改动清单**：
  1. `src/lvjiang/core/android/agent.py`：
     - 新增 `AgentClient.probe_execution_busy(timeout: float = 1.5) -> str`：在 `self._lock` 下，若 `_sock is None` 或不支持 `offline_protocol` 返回 `""`；发**单次不重试** `_roundtrip("control_begin", timeout=timeout)`：
       - `ok=false` 返回设备拒绝原因；
       - `ok=true` 立即发单次 `_roundtrip("control_end", timeout=timeout)` 返回 `""`；
       - 任何传输/超时异常只记 warning，**不重试、不改 `_status`、不触碰 `recovery_coordinator`**，返回 `""` 放行。
     - `AgentInput.probe_execution_busy(timeout: float = 1.5) -> str`：`_using_adb` 时返回 `""`，否则委托 `self._client.probe_execution_busy(timeout=timeout)`。
  2. `src/lvjiang/ui/main/run_control.py`：
     - `device_execution_busy()` 优先调 `probe_execution_busy()`，仅当后端对象无该方法（如现有测试替身 `_FakeInput` / `_BusyInput`）时回退 `begin_execution` / `end_execution`。
  3. `src/lvjiang/ui/batch/batch_execution.py`、`src/lvjiang/ui/main/window.py`、`src/lvjiang/ui/main/run_control.py`：
     - `_begin_automation(..., check_device_busy: bool = True)` 与 `run_batch(spec, *, prechecked_device_busy: bool = False)` 透传标记，批量页已完成预检后不再重复探测第二次；直接调用 `run_batch()` 的入口仍保留预检保护。

### 3.6 缺陷 6：恢复询问弹窗静默关闭

- **现状与确定性复现**：
  - `run_android_recovery.py` 恢复弹窗的 200ms `poll()` 只看 `not current() or run_context.stop_event.is_set()`；`_on_adb_connection_restored` 与 `_on_connect_done`（`not holds_confirmation`）均未关窗，残留弹窗点「否」直接杀掉健康运行中的任务。
- **高危陷阱**：
  - 禁止调 `box.done(QMessageBox.StandardButton.No)`（会触发 `finish(No)` 杀掉健康任务）。
  - 禁止调 `box.reject()` / `box.close()` 落入 `finish` 的 `else` 分支（会打印误导日志「任务仍在等待通道恢复」）。
  - 禁止在 `holds_confirmation=True` 时关窗（会破坏防重复输入契约）。
- **精确改动清单**：
  1. `src/lvjiang/ui/main/run_android_recovery.py`：
     - 在 `_show_connection_recovery_question` 中增加 `dismissed = False` 标志与静默关窗闭包（先置 `dismissed = True`、停 `timer`、`dialogs.pop(target.id, None)`、断开 `finished` 信号后 `close()` + `deleteLater()`），并在 `finish()` 首行检查 `if dismissed: return`。
     - 新增 `_dismiss_connection_recovery_question(self, target_id: str) -> None`，并在 `_on_adb_connection_restored(target_id)` 中调用。
  2. `src/lvjiang/ui/main/adb_device_ops.py`：
     - 仅在 `_on_connect_done` 的 `if not holds_confirmation:` 分支调用 `_dismiss_connection_recovery_question(target_id)`；`holds_confirmation=True` 保持弹窗开启。

### 3.7 缺陷 7：空闲画面流重建按目标隔离与 `AndroidStreamCapture` 非阻塞打断

- **现状与确定性复现**：
  - `run_android_recovery.py` 全窗口共用单槽 `_stream_recheck_bridge`，且 `_on_stream_recheck_finished` 不校验当前绑定 `capture` 是否仍是本次重建对象，重连或切单帧后旧失败结果会给健康目标挂红条。
  - `scrcpy_capture.py` 只有 `restart()` 持 `_lifecycle_lock`，`restart()` 在 `sleep(0.2)` 后及 `start()` 各阶段间未检查 `_user_stop`。
- **高危陷阱**：
  - **绝对不要给 `start()` / `stop()` 加 `with self._lifecycle_lock:`**，否则主线程断开/切单帧调 `stop()` 会被后台 `restart()` 死锁等待 5–15s 且无法打断卡住的 `start()`。
- **精确改动清单**：
  1. `src/lvjiang/ui/main/run_android_recovery.py`：
     - 将全局单槽 `_stream_recheck_bridge` 改为按 `target_id` 存储的 `_stream_recheck_bridges()`（`dict[str, _StreamRecheckBridge]`），保留兼容属性 `_stream_recheck_bridge` 供既有测试断言无破坏过渡；同目标防重入，不同目标互不阻塞。
     - 回调绑定本次 `capture` 与 `bridge`，在 `_on_stream_recheck_finished` 仅当表中仍是该 `bridge` 时移除，并校验 `target is not None and target.handle is not None and target.handle.binding().capture is capture` 后才更新横幅/状态栏。
  2. `src/lvjiang/core/android/scrcpy_capture.py`：
     - `stop()` 保持**不加 `_lifecycle_lock`**，立即置 `self._user_stop = True; self._running = False`、递增 `self._stop_epoch += 1`、关闭 `_sock` 并终止 `_server_proc`。
     - `restart()` 调 `self.stop()` 后记录 `expected_epoch = self._stop_epoch`、`self._user_stop = False`，在 `time.sleep(0.2)` 后与 `start()` 各阶段之间（`_push_jar` 前后、`forward_dynamic` 后、`_start_server` 后、`_connect_socket` 后、协议头循环每轮、启解码线程前）检查 `self._user_stop or self._stop_epoch != expected_epoch`，命中外部停止立即清理并返回 `False`；同步修正 `restart()` docstring，明确 `_lifecycle_lock` 仅串行化并发 `restart()`。

---

## 4. 分批实施与定向验证计划

按风险与依赖拆成 **3 个可独立验证、独立回退的批次**：

### 批次 A：连接线程防崩溃 + 空闲流重建与非阻塞打断（缺陷 1 + 缺陷 7）
- **文件**：`adb_device_ops.py`、`window.py`、`run_android_recovery.py`、`scrcpy_capture.py`
- **新增定向回归测试**：
  - 运行中 `_device_thread` 未退出时连续触发重连/扫描，旧线程进入 retired 保活不崩溃，过期连接释放旧 `capture`/`agent` 且不覆盖新目标；
  - 双目标各自空闲重建互不阻挡；重建期间目标重连或切 `screencap` 后旧失败结果不反写健康目标横幅；
  - `AndroidStreamCapture.restart()` 阻塞期间并发调用 `stop()` 立即返回且 `restart()` 提前退出不留僵尸线程。
- **验证**：
  - `.venv\Scripts\python.exe -m ruff check` + `mypy` 改动文件
  - `.venv\Scripts\python.exe -m pytest tests/ui/main/test_android_reconnect_recovery.py tests/ui/main/test_android_recovery_question.py tests/core/android/test_android_stream_health.py tests/ui/main/test_window_close_cleanup.py -q`

### 批次 B：运行中重连租约/策略/收尾 + 启动预检去阻塞（缺陷 2 + 缺陷 5）
- **文件**：`agent.py`、`workflows/engine/core.py`、`run_control.py`、`adb_device_ops.py`、`batch_execution.py`、`window.py`
- **新增定向回归测试**：
  - `AgentClient.close()` 清 `_owns_execution`；`end_execution()` 在断开或 `stop_event` 已置位时绝不重连、绝不抛 `AdbConnectionError`；
  - 运行中重连后：新 `device2.recovery` 延续冻结策略、新 `input_ctrl2.stop_check` 生效、先释 `agent1` 再取 `agent2` 的 `control_begin`、结束时 `finally` 动态调 `input_ctrl2.end_execution()`；
  - `device_execution_busy()` 在 `AgentClient` 断线/超时时单次短超时放行且不清空 `_status`、不改 `_using_adb`，设备明确拒绝仍拦截；批量启动不重复探测。
- **验证**：
  - `.venv\Scripts\python.exe -m ruff check` + `mypy` 改动文件
  - `.venv\Scripts\python.exe -m pytest tests/core/android/ tests/ui/main/test_device_busy_precheck.py tests/ui/main/test_batch_device_precheck.py tests/ui/main/test_android_reconnect_recovery.py tests/ui/main/test_target_resource_rebinding.py -q`

### 批次 C：多目标 `WAITING_TARGET` 投影、通道横幅与恢复弹窗收口（缺陷 3 + 缺陷 4 + 缺陷 6）
- **文件**：`run_control.py`、`run_android_recovery.py`、`adb_device_ops.py`、`batch_execution.py`、`tuning_tab.py`、`tray_ops.py`
- **新增定向回归测试**：
  - `WAITING_TARGET` 经切换目标投影后，批量页/调律页/托盘保持活跃运行态（禁用开始、允许停止、暂停仅 `running`/`paused` 可用）；恢复后当前目标立即回写 `_run_state` 与按钮，后台目标不污染当前查看目标；
  - 单目标 `_on_adb_connection_lost` → `_on_adb_connection_restored` 正确隐藏红条；后台断线/断流切过去可见横幅；停止或结束后切目标旧横幅不复活且不误伤其他目标横幅；
  - 恢复或 `not holds_confirmation` 重连后弹窗静默关闭（不置 `stop_event`、不打误导日志）；`holds_confirmation=True` 时弹窗保留。
- **验证**：
  - `.venv\Scripts\python.exe -m ruff check` + `mypy` 改动文件
  - `.venv\Scripts\python.exe -m pytest tests/ui/main/test_android_reconnect_recovery.py tests/ui/main/test_android_recovery_question.py tests/ui/main/test_run_resume_event.py tests/ui/main/test_tray_restore_hotkey.py tests/ui/main/test_task_page_controls.py -q`

---

## 5. 明确非范围

- 不修改 Android APK 端协议与 `AgentServer.kt`（端侧 `pcOwner` 按连接线程隔离已成立，PC 侧修正释放与交接顺序即可闭环）。
- 暂放极窄前置条件的边缘观察：`AgentInput._call_adb` 在已降级 ADB 后遇到多指时间线恢复回 `AGENT` 时漏传 `gesture_ms`。
- 不顺手改动 `docs/00-meta`、`docs/20-plans/01-backlog.md`、`docs/30-design`、`docs/40-reference`、`docs/60-guide`。
