# Dev Log: 2026-07-16 DSL 去隐式化重构与崩溃防护体系

> 日期：2026-07-16（续）
> 涉及模块：`lvjiang/workflows/grammar.lark`、`lvjiang/workflows/ast_nodes.py`、`lvjiang/workflows/parser.py`、`lvjiang/workflows/engine.py`、`lvjiang/workflows/base.py`、`lvjiang/workflows/builtins.py`、`lvjiang/core/input.py`、`lvjiang/core/capture.py`、`lvjiang/core/crash_handler.py`、`lvjiang/ui/main_window.py`、`lvjiang/__main__.py`、`config/system/workflows/single_tuning.wf`、`config/system/workflows/equip_analysis.wf`
> 关键词：DSL 去隐式化、scan as 强制、click_match 显式化、collect dict 化、return 语法、pyautogui 替换、SendInput、崩溃防护、faulthandler、Qt 跨线程 access violation

---

## 一、本日完成

本日三个提交方向：**DSL 语法去隐式化重构**、**pyautogui 全量替换**、**崩溃防护与 Qt 跨线程修复**。

---

## 二、DSL 去隐式化重构

### 2.1 问题背景

用户评审 `BaseWorkflow` 后指出三个设计缺陷：

| 缺陷 | 具体表现 |
|------|----------|
| `collect` 输出为 list | 别的工作流无法通过 key 定位数据，list 索引无语义 |
| `scan` 有双机制 | 既有 `scan as [var]` 又有隐式 `last_scan`，`scan [scene] as [last_scan]` 语义混乱 |
| `click_match` 依赖隐式状态 | 内部读 `self.last_scan`，与上一步 scan 隐式耦合 |

### 2.2 语法变更对照

| 旧语法 | 新语法 |
|--------|--------|
| `scan [scene].[fields]`（无 as） | **废弃**，as 子句变为必须 |
| `scan [scene].[fields] as [var]` | `scan [scene].[fields] as [var]`（唯一形式） |
| `click_match "text"` | `click_match [scene].[var] "text"` |
| `collect [var]` | `collect [var]`（key = 变量名） |
| `collect [var] as [label]` | `collect [var] as [label]`（key = label） |
| `self.output` 为 list | `self.output` 为 dict |

### 2.3 实现细节

#### grammar.lark

```diff
- scan_stmt: "scan"i bracket_expr field_list? as_clause? _NL
+ scan_stmt: "scan"i bracket_expr field_list? as_clause _NL    # as_clause 去掉 ?

- click_match_stmt: "click_match"i STRING error_clause? _NL
+ click_match_stmt: "click_match"i bracket_expr "." bracket_expr STRING error_clause? _NL
```

#### ast_nodes.py

- 删除 `ScanAs` 类，将 `target` 字段合并到 `Scan`
- `ClickMatch` 新增 `scene` 和 `var` 字段
- **踩坑**：`Scan` 类中 `fields`（有默认值 `None`）排在 `target`（无默认值）前面，违反 Python dataclass 字段顺序规则，报 `TypeError: non-default argument 'target' follows default argument 'fields'`。修复：将 `target` 移到 `fields` 之前

```python
@dataclass(frozen=True)
class Scan:
    scene: Any
    target: Any     # 无默认值，必须在有默认值字段之前
    fields: list | None = None
    line_no: int = 0
```

#### engine.py

- `_exec_scan`：scan 总是有 target，直接存变量
- `_exec_click_match`：从 `variables[var]` 读取 OCR 结果，在 `scene` 中点击
- `_exec_collect`：output 改为 dict 赋值，key 为 alias 或变量名

#### base.py

- 删除 `self.last_scan`、`self.last_scan_scene`
- `self.output` 从 `list` 改为 `dict`
- `ocr_scene` 不再设置隐式变量
- `click_match_text` 签名改为 `(scene_key, scan_result, target_text, error_msg)`，不再依赖隐式状态

### 2.4 工作流文件更新

**single_tuning.wf** 关键变更：

```
# 旧
scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4]
click_match "调律" error "未找到调律按钮"

# 新
scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4] as [tune_scan]
click_match [equip_weapon_detail].[tune_scan] "调律" error "未找到调律按钮"
```

---

## 三、DSL return 语法

### 3.1 需求

调律流程中需要"装备已调律完毕，提前退出"逻辑。最初尝试 `goto` + `@label`，但标签在文件末尾无法被解析器识别（标签后必须有语句才能被 Transformer 处理）。

### 3.2 实现

新增 `return` 语句，独立 `_ReturnSignal` 信号类（与 `_HaltSignal` 分离）：

| 信号 | 语义 | 处理 |
|------|------|------|
| `_ReturnSignal` | 正常退出 | 返回 output |
| `_HaltSignal` | 异常终止（click_match 失败） | 返回空 |
| `_BreakSignal` | 循环跳出 | 跳出当前循环 |

**踩坑**：最初 `return` 用 `_HaltSignal` 实现，被 `_exec_body` 的 `except BaseException` 捕获并 log error。修复：在 `BaseException` 前加 `except _ReturnSignal: raise`，后又改为独立的 `_ReturnSignal` 类。

---

## 四、pyautogui 全量替换为 ctypes + SendInput

### 4.1 动机

用户判断 pyautogui 库本身导致 QThread 中卡死（GDI 超时后程序无响应）。

### 4.2 替换方案

`lvjiang/core/input.py` 全部鼠标操作替换为 ctypes + Win32 SendInput API：

```python
def _send_mouse_event(flags: int, dx: int = 0, dy: int = 0):
    mi = _MouseInput(dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0,
        dwExtraInfo=PUL(ctypes.c_ulong(0)))
    ii = _InputUnion(mi=mi)
    inp = _Input(type=_INPUT_MOUSE, ii=ii)
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
```

### 4.3 涉及文件

| 文件 | 变更 |
|------|------|
| `lvjiang/core/input.py` | 全部替换为 ctypes + SendInput |
| `lvjiang/core/capture.py` | 移除无用 pyautogui import，新增 `get_capture_size()` |
| `pyproject.toml` | 移除 `pyautogui>=0.9` 依赖 |
| `lvjiang/__main__.py` | DPI 注释中移除 pyautogui 引用 |
| `lvjiang/ui/app.py` | DPI 注释中移除 pyautogui 引用 |

### 4.4 附带优化：_region_to_screen 去截屏

用户发现 `click` 操作内部每次都截屏只为获取屏幕分辨率。新增 `get_capture_size()` 直接读 `_monitor` 配置，`_region_to_screen` 改用该方法，避免无谓截屏。

---

## 五、崩溃防护体系

### 5.1 问题

程序运行中 Python 进程无故消失，无异常日志、无弹窗。日志最后一条正常，之后无任何输出。

### 5.2 根因分析

通过 `faulthandler` 捕获到崩溃日志：

```
Windows fatal exception: access violation

Current thread 0x00000694 (most recent call first):
  File "lvjiang/ui/main_window.py", line 321 in write        ← QtSink.write()
  File "loguru/_simple_sinks.py", line 16 in write
  File "loguru/_handler.py", line 206 in emit
  File "loguru/_logger.py", line 2066 in _log
  File "lvjiang/workflows/engine.py", line 69 in run         ← logger.info() 从 QThread 调用
```

**根因**：`QtSink.write()` 在工作流线程（QThread）中被调用，直接执行 `text_edit.append()` — Qt 控件只能在主线程访问，跨线程访问导致 access violation，进程立即终止。

### 5.3 修复

用 Qt 信号/槽机制做线程安全转发：

```python
class _LogBridge(QObject):
    _append = pyqtSignal(str)

bridge = _LogBridge(self)
bridge._append.connect(self.log_text.append)  # QueuedConnection（跨线程自动排队）

class QtSink:
    def write(self, message):
        self._bridge._append.emit(message.strip())  # 信号发射线程安全
```

### 5.4 三层崩溃防护

为防止未来再出现"进程消失无日志"，新增 `lvjiang/core/crash_handler.py`：

| 层 | 机制 | 捕获场景 |
|----|------|----------|
| Layer 1 | `faulthandler` | segfault / SIGABRT / 硬崩溃，dump 到 `logs/crashes/`；每 30 秒定期 dump（排查死锁） |
| Layer 2 | `sys.excepthook` | 未捕获 Python 异常 |
| Layer 3 | Windows `SetUnhandledExceptionFilter` | C 扩展 native crash（mss / GDI 崩溃） |

崩溃日志统一写入 `logs/crashes/crash_{timestamp}.log`。

---

## 六、踩坑记录

### 6.1 dataclass 字段顺序

Python dataclass 要求有默认值的字段必须在无默认值字段之后。`Scan` 类中 `fields: list | None = None` 排在 `target: Any` 前面导致 `TypeError`。

**教训**：dataclass 字段声明顺序即构造参数顺序，必须把必选参数放前面。

### 6.2 return 与 halt 信号混淆

最初 `return` 用 `_HaltSignal` 实现，被 `except BaseException` 捕获并记录为 error 日志。正常退出和异常终止不应共享信号类型。

**教训**：控制流信号必须职责分离——正常退出、异常终止、循环跳出各用独立信号类。

### 6.3 goto + label 的解析限制

`goto done` + `@done` 标签在文件末尾时，标签后无语句，Transformer 不处理尾部独立标签。

**教训**：DSL 控制流应优先使用结构化语法（return / break），goto 只作为兜底。

### 6.4 Qt 跨线程访问

Qt 控件只能在创建它的线程（主线程）中访问。后台线程需要更新 UI 时，必须通过信号/槽排队到主线程。这个规则适用于所有 Qt 控件操作，包括 `text_edit.append()`、`label.setText()`、`button.setEnabled()` 等。

**教训**：凡是在 QThread 中可能执行的代码路径（包括日志 sink），都不能直接操作 Qt 控件。

---

## 七、文件变更汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `lvjiang/workflows/grammar.lark` | 修改 | scan as 必须、click_match 新语法 |
| `lvjiang/workflows/ast_nodes.py` | 修改 | 删除 ScanAs、Scan 加 target、ClickMatch 加 scene/var |
| `lvjiang/workflows/parser.py` | 修改 | 适配新语法 |
| `lvjiang/workflows/engine.py` | 修改 | 更新执行逻辑、新增 _ReturnSignal |
| `lvjiang/workflows/base.py` | 修改 | 删除 last_scan、output→dict、click_match_text 新签名 |
| `lvjiang/workflows/builtins.py` | 修改 | docstring 更新 |
| `lvjiang/core/input.py` | 重写 | pyautogui → ctypes + SendInput |
| `lvjiang/core/capture.py` | 修改 | 移除 pyautogui、新增 get_capture_size() |
| `lvjiang/core/crash_handler.py` | 新增 | 三层崩溃防护 |
| `lvjiang/ui/main_window.py` | 修改 | QtSink 改为信号桥（线程安全） |
| `lvjiang/__main__.py` | 修改 | 安装崩溃防护、移除 pyautogui 引用 |
| `pyproject.toml` | 修改 | 移除 pyautogui 依赖 |
| `config/system/workflows/single_tuning.wf` | 修改 | 新语法 + return |
| `config/system/workflows/equip_analysis.wf` | 修改 | 新语法 |
| `tests/test_parser.py` | 重写 | 适配新 AST 和语法 |
