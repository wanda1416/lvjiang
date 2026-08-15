# DSL 系统与交互函数

用户交互（confirm/pause/notify/input）、系统函数（save/panel_rows/panel_cols）以及玩家档案（profile）函数。

> 总览与速查表见 [06-functions.md](06-functions.md)。

## 目录

- [一、用户交互函数](#一用户交互函数)
  - [confirm — 确认对话框](#confirm--确认对话框)
  - [pause — 暂停执行](#pause--暂停执行)
  - [notify — 非阻塞通知](#notify--非阻塞通知)
  - [input — 输入对话框](#input--输入对话框)
  - [线程安全](#线程安全)
  - [异常处理场景示例](#异常处理场景示例)
- [二、系统函数](#二系统函数)
  - [save — 手动保存 session](#save--手动保存-session)
  - [panel_rows / panel_cols — Panel 尺寸查询](#panel_rows--panel_cols--panel-尺寸查询)
  - [clock — Unix 时间戳](#clock--unix-时间戳)
  - [datetime — 格式化时间](#datetime--格式化时间)
- [三、玩家档案函数](#三玩家档案函数)

---

## 一、用户交互函数

运行中与用户交互的内置函数。`confirm`/`pause`/`input` 通过 Qt 主线程回调机制实现，可在工作流子线程中安全调用；`notify` 使用 Win32 超时 API，不阻塞工作流。

> 旧版 `messagebox` 函数已移除，等价功能请使用 `pause`。

### confirm — 确认对话框

弹出"是/否"对话框，返回 `true`（是）或 `false`（否）。

```
eval $ok = confirm("确认开始批量调律？")
if $ok
    log "用户确认，开始执行"
else
    log "用户取消"
end
```

### pause — 暂停执行

阻塞工作流直到用户点击"确定"。用于需要用户手动介入的场景：

```
try
    scan [target_scene] as $result
catch $err
    eval pause(concat("扫描失败: ", $err, "，请手动处理后点击确定"))
end
```

无参数调用使用默认消息：`eval pause()` 显示"工作流已暂停，点击确定继续"。

### notify — 非阻塞通知

双重通知机制：
1. **弹窗通知**：Windows 在后台守护线程调用 Win32 `MessageBoxTimeoutW`（5 秒自动关闭）；macOS 走系统通知中心。工作流线程立即返回，不被阻塞。
2. **告警面板**：同时写入 `session.json` 的 `alert_info`，在 UI 告警面板持久化展示。

```
eval notify("第一批调律完成")
# 继续执行后续步骤
```

### input — 输入对话框

弹出文本输入框，返回用户输入的字符串。用户取消或关闭对话框返回 `null`：

```
eval $name = input("请输入角色名:")
if $name is_empty
    log "用户取消输入"
    return
end
log concat("角色名: ", $name)
```

### 线程安全

`confirm`/`pause`/`input` 通过 `engine._ui_callback` 机制实现。UI 层在创建引擎时注入回调，内部使用常驻主线程的 `QObject` 信号桥 + `threading.Event` 机制：工作流线程发信号携带请求 dict → 主线程槽显示 Qt 对话框并回填结果 → `Event.set()` 唤醒等待中的工作流线程。无竞态、无需事件循环。

按 F10 停止时，UI 层会主动关闭当前活动对话框（`confirm` 返回 `false`、`input` 返回 `null`、`pause` 立即返回），避免工作流阻塞在弹窗上无法响应停止。

`notify` 的弹窗部分在后台守护线程中调用 Win32 `MessageBoxTimeoutW`（自带超时自动关闭），工作流线程立即返回，无需 Qt 回调。告警面板写入通过 `engine._ui_callback` 机制完成。

无回调时（如测试环境），`confirm`/`pause` 回退到 Win32 MessageBoxW，`input` 返回 `null`。

### 异常处理场景示例

```
# 重试 + 用户介入 + 兜底
eval $attempt = 0
eval $max_retry = 3
eval $success = false

loop while $attempt < $max_retry
    eval $attempt = $attempt + 1
    try
        scan [target] as $result
        eval $success = true
        break
    catch $err
        eval $msg = concat("第 ", $attempt, " 次失败: ", $err, "\n是否重试？")
        eval $need_help = confirm($msg)
        if not $need_help
            break
        end
    end
end

if not $success
    eval $pause_msg = "自动流程失败，请手动完成后点击确定"
    eval pause($pause_msg)
end
```

---

## 二、系统函数

### save — 手动保存 session

通过 engine 回调触发 SessionManager.save()，将当前 session 持久化到磁盘。

```
eval save()
```

### panel_rows / panel_cols — Panel 尺寸查询

返回 panel 经网格校准后检测到的实际行数/列数。

```
eval $rows = panel_rows("bag_equip_detail", "bag_grid")
eval $cols = panel_cols("bag_equip_detail", "bag_grid")
```

### clock — Unix 时间戳

返回当前 Unix 时间戳（秒精度 float），可用于计时、超时判断。

```
eval $start = clock()
# ... 执行某些操作 ...
eval $elapsed = clock() - $start
log concat("耗时: ", $elapsed, " 秒")
```

### datetime — 格式化时间

返回时间的格式化字符串。支持当前时间或指定时间戳。

```
# 当前时间
eval $time_str = datetime("%H:%M:%S")        # 当前时间
eval $date = datetime("%Y-%m-%d")            # 当前日期
eval $full = datetime()                       # 默认格式: "2026-08-15 14:30:45"

# 指定时间戳（来自 clock()）
eval $start = clock()
# ... 执行某些操作 ...
eval $elapsed = clock() - $start
log concat("开始时间: ", datetime($start, "%H:%M:%S"))
log concat("开始日期: ", datetime($start))     # 默认格式
```

| 调用 | 返回类型 | 说明 |
|------|----------|------|
| `datetime()` | `str` | 当前时间，默认格式 `"YYYY-MM-DD HH:MM:SS"` |
| `datetime("格式")` | `str` | 当前时间，自定义 strftime 格式 |
| `datetime($ts)` | `str` | 指定时间戳，默认格式 |
| `datetime($ts, "格式")` | `str` | 指定时间戳，自定义格式 |

常用 strftime 格式符：`%Y` 年、`%m` 月、`%d` 日、`%H` 时、`%M` 分、`%S` 秒。

---

## 三、玩家档案函数

访问 ProfileDB（玩家档案数据库）的内置函数，支持 quota/regen/stock 三模型。详细说明与示例见 [09-data-channels.md](09-data-channels.md#七profile--玩家档案只读数据源)。

```dsl
# 读取配额
eval $remain = profile_get("niaoniao_of_week")

# 消耗体力（realtime regen，自动处理时间锚点）
eval $tili = profile_inc("tili", -900)

# 查询模型类型
eval $model = profile_model("tili")    # "regen"

# 批量获取
eval $all = profile_all()
```
