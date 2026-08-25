# DSL 交互指令概览

> 基础指令（collect/eval/call/log）见 [03.1-basic-commands.md](03.1-basic-commands.md)。

## 指令分类

| 指令 | 输入通道 | 作用 | 典型用途 |
|------|---------|------|---------|
| **collect** | 数据 | 收集输出 | 存入工作流结果字典 |
| **eval** | 数据 | 赋值/运算 | 变量计算、字典操作 |
| **call** | 控制 | 调用子过程 | 模块化复用 |
| **log** | 调试 | 日志输出 | 调试信息、状态记录 |
| **click** | 鼠标/触摸 | 点击屏幕坐标 | 点按钮、点面板格子 |
| **move** | 鼠标 | 移动鼠标到目标位置 | 悬停触发 tooltip、安全区检查 |
| **scroll** | 鼠标滚轮 | 在目标位置滚动滚轮 | 列表翻页、配合 move 精确定位滚动 |
| **drag** | 鼠标/触摸 | 拖拽（翻页/滑动） | 滚动列表、翻页 |
| **wait** | 时间 | 暂停执行 | 等动画、等加载 |
| **press** | 键盘 | 模拟按键 | 快捷键、长按、组合键 |
| **align** | 图像 | 面板自对齐 | 计算网格坐标（通常自动触发） |
| **screenshot** | 截屏 | 保存当前画面 | 调试记录 |

按输入通道分组：
- **基础指令**（collect / eval / call / log）→ 数据操作与控制流
- **鼠标操作**（click / mouse / move / scroll / drag）→ 通过坐标注入鼠标/触摸事件；`mouse` 保留桌面鼠标键的原始 down/up
- **时间与辅助**（wait / wait stable / align / screenshot）→ 节奏控制与校准
- **键盘输入**（press）→ 通过 VK 码注入键盘事件

## 隐藏延迟共性

click 和 drag 共享同一套隐式延迟机制：

```
[before_click_wait] → 操作 → [after_click_wait]
```

| 参数 | 默认范围 | 适用 |
|------|----------|------|
| `before_click_wait` | 0.05 ~ 0.2s | click / drag |
| `after_click_wait` | 0.05 ~ 0.2s | click / drag |
| `mouse_move_duration` | 0.4 ~ 0.6s | drag（移动到起点） |
| `click_random_offset` | ±5px | click / drag |
| `region_jitter_ratio` | 0.25 | click / drag |

**显式 wait_clause 抑制默认延迟**：指定 `before`/`after`/`around` 任一子句后，`before_click_wait` 和 `after_click_wait` 全部置零。press 也支持后缀等待子句（见下文），但它没有默认延迟，因此不需要抑制机制。

## press 状态管理概览

press 是唯一的键盘指令，引入 **KeyStateRegistry** 管理按键状态：

```
press "M"                # 完整按键（down + up）
press "W" hold 2.0       # 按住 2 秒后释放
press "SHIFT" down       # 按下保持
press "SHIFT" up         # 释放
press "A" after wait 0.5 # 按 A 后等待 0.5 秒
```

| 模式 | 语义 | 状态变化 |
|------|------|---------|
| `press "KEY"` | 一次完整按键 | down → up（瞬间） |
| `press "KEY" hold N` | 按住 N 秒 | down → sleep(N) → up |
| `press "KEY" down` | 按下保持 | down（状态留在 registry） |
| `press "KEY" up` | 释放 | up（从 registry 移除） |

**严格校验**：
- 重复 `down` 同一键 → 报错（DSL 作者错误）
- `up` 未 `down` 的键 → 报错
- `hold` 时长必须 > 0

**自动清理**：工作流退出时（正常/异常/取消/超时），`execute()` 的 `finally` 块自动调用 `release_all()` 释放所有仍处于按下状态的键。单键释放失败不阻塞其他键。

详见 [03.4-keyboard.md](03.4-keyboard.md)。

## 组合键模式

press 的 down/up 模式支持显式时序控制，可构造任意组合键：

```
press "CTRL" down        # 按住 CTRL
press "C"                # 按 C（完整按键）
press "CTRL" up          # 释放 CTRL
```

> 注意：普通 `press "KEY"` 模式自带 down + up，不需要手动管理状态。只有组合键场景才需要 down/up。

## 失败语义分类

交互指令的失败按原因分两类：

**配置错误 → 抛错终止**（脚本或布局配错，继续执行只会在错误的页面上乱操作）：

| 情况 | 指令 |
|------|------|
| Region / Point / Panel 未绑定坐标 | click / drag |
| Point 单独作为 drag 目标 | drag |
| `$var` 未定义或类型不匹配 | click / drag |
| Panel 未在布局中定义 | click / drag / align |
| `wait @<delay>` 命名延迟未定义 | wait |
| press 键名不在 VK 映射表中 | press |
| press 重复 down 或未 down 就 up | press |
| press hold 时长 ≤ 0 | press |

**运行时状态 → 记日志后跳过**（不是配置问题，脚本靠它判断边界）：

- Panel 索引越界（脚本遍历网格的终止条件）
- Panel 对齐失败（页面未加载完）

配置错误在**执行前**就会被静态检查拦下（解析 `.wf` 时比对布局），不等执行到那一行。详见 [33-engine/02-static-check.md](../33-engine/02-static-check.md)。

## 详细文档

- [03.1-basic-commands.md](03.1-basic-commands.md) — collect / eval / call / log 完整语法
- [03.2-interaction.md](03.2-interaction.md) — wait / align / screenshot 完整语法
- [03.3-mouse.md](03.3-mouse.md) — click / drag 完整语法、后缀等待子句、隐藏延迟
- [03.4-keyboard.md](03.4-keyboard.md) — press 完整语法、KeyStateRegistry、键名表
