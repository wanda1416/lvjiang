# 输入时间线 — 描述严格并发的操作序列

> **状态**：已实现（待真机验证并发手势；安卓端 `press` 不支持）
> **依赖**：设备端代理协议（a11y `dispatchGesture`）、PC 输入后端、DSL 语法层

---

## 1. 背景与目标

DSL 目前是**严格线性**的：一条指令执行完才有下一条。这让一类真实操作无法表达——
「推住摇杆后退的同时跳三次」。

现实例子是随包之外的 `刷虎骨酒.wf`，桌面端靠交叠的按键 down/up 实现：

```wf
press "S" down after wait 0.4
press "SPACE" after wait 1
press "SPACE" after wait 1
press "SPACE"
press "S" up after wait 0.5
```

`press "KEY" down/up` 只接受字面按键，安卓端没有对应物（`keyevent` 是一次性的，
所有 Android 路径都不可能真正保持按键）。而移动在安卓端是虚拟摇杆，必须靠 `drag`
推住才会行走，单纯点击不触发移动。于是这类脚本**天生只能在桌面跑**。

目标：提供一种能描述「若干路输入各自在时间线上何时开始、持续多久」的语法，
使同一份脚本在 PC 与设备端手势两个通道上都能按同样的时序落地。

### 1.1 为什么不做作用域块（`hold ... end`）

早期方案是「声明某一路在整块期间保持按下，块内仍线性」。它能覆盖上面的例子，
但覆盖不了两路摇杆同时推、或三路输入互相错开的情况；而且 `hold` 与现有的
`press ... hold t` / `click ... hold t` 同名不同形（块 vs 修饰符），读起来有歧义。
过渡形态会成为将来的负担，直接做时间线。

---

## 2. 后端能力边界

**这是本需求的硬约束，不是实现细节。**

| 通道 | 并发输入 | 机制 |
|---|---|---|
| PC（SendInput / PostMessage） | ✅ | 按键与鼠标各有独立状态，已支持 `down`/`up` |
| 设备端手势（a11y） | ✅ | 一个 `GestureDescription` 可含多条 `StrokeDescription`，每条带独立 `startTime` |
| ADB `shell input` | ❌ | `input swipe` 单指；`input motionevent` 无法交错两个 pointer |
| Shizuku shell | ❌ | 同上，走的是同一套 `input` |

因此：**时间线是「设备端手势」独占能力**，ADB 通道不做静默降级——降级的现象是
「脚本点了没反应」，比直接拒绝更难排查。

### 2.1 a11y 的已知限制

| 限制 | 影响 |
|---|---|
| 单次 `dispatchGesture` 总时长上限约 60 秒 | 时间线总长超限整组失败，需在下发前拒绝并说明 |
| `GestureDescription.getMaxStrokeCount()`（AOSP 为 10） | 同时最多 10 路，超限在下发前拒绝 |
| 真实触摸会取消正在执行的手势（`onCancelled`） | 用户碰屏幕则整组失败，必须如实报错而不是当成完成 |
| 手势由系统按帧调度 | 时序精度优于 `adb shell input`，这也是时间线只在此通道有意义的原因 |

---

## 3. 语法

```wf
timeline
    @0.0  drag [general_move].[move_backward] duration 0.1 hold 2.1
    @0.5  click [general_combat].[tiaoyue]
    @1.5  click [general_combat].[tiaoyue]
    @2.0  click [general_combat].[tiaoyue]
end
```

- `@<秒>` 是**相对本块起点的绝对偏移**，不是间隔。写偏移而不是间隔，是因为并发
  时序的正确性只取决于各路何时开始，用间隔表达会让「第三路相对谁」变得含糊。
- 块内条目按偏移排序执行，允许多条同偏移。
- 偏移与时长都按现有 `wait` 的数值形式书写，支持 `(min, max)` 区间与变量。

### 3.1 块内只允许输入类语句

允许：`click` / `drag` / `press`。

`place` 与 `move` 实现时移除：`place` 只挪光标不产生输入，`move` 的 `place+move`
语法糖会展开成多节点序列，与"一路输入"的模型不符，留着就是半成品语义。

禁止：`scan` / `find` / `recognize` / `if` / `loop` / `for` / `call` / `goto` /
`return` / `eval`。识别要几百毫秒且时长不确定，一旦允许，「严格时序」就只是幌子。
这条在**语法层**拦（解析错误直接指向那一行），不放到语义检查里。

### 3.1.1 跨端差异拆到块外（实现期确认）

块内禁止 `if`，所以两端打法不同时要写两个 `timeline` 块放在 `is_device()` 分支下。
移动在两端**不是同一个动作换绑定**：桌面按 WASD，安卓推摇杆；`general_move` 的方向
是 arrow，而 arrow 没有 `activation_key`，`drag` 在桌面不会转成按键。

这也是块内保留 `press` 的理由——桌面块用 `press`，设备块用 `drag`。

### 3.2 与现有 `hold` 的关系

`drag ... hold t` 与 `click ... hold t` 在块内照常可用，含义不变——它们描述的是
**这一路自己**的持续时长；`@偏移` 描述的是这一路**何时开始**。两者正交。

---

## 4. 能力声明

不在解析期反查后端，而是由脚本自己声明所需能力，加载时即可判定：

```wf
#% requires: [device_gesture]
```

- 声明的能力当前连接通道不满足时，**加载该脚本即失败**并说明原因（例如
  「本脚本要求设备端手势，当前连接方式为 ADB shell input，请在设置里启用
  『安卓输入 → 设备端手势』」）。
- 不满足时不进入执行，也不逐条尝试。脚本列表里应能看出它为何不可用。
- 用了 `timeline` 却没声明 `requires` 视为元数据错误，加载即报——避免「在 PC 上
  写完能跑，换到手机上跑到一半才失败」。

---

## 5. 各后端的落地方式

| 通道 | 落地 |
|---|---|
| PC | 按偏移排程：单调时钟为基准，到点下发对应的 down/up 或鼠标动作。各路互不阻塞 |
| 设备端手势 | 整块编译成**一个** `GestureDescription`：每路一条 `StrokeDescription(path, startTime, duration)`，一次 `dispatchGesture` 下发。按键类步骤不支持——安卓 `keyevent` 保持不住 |
| ADB / Shizuku | 不实现。由第 4 节的能力声明在加载期拦住 |

### 5.1 协议新增 op

```
{"op": "gesture", "strokes": [
    {"start_ms": 0, "duration_ms": 2100, "points": [[x1,y1],[x2,y2]]},
    {"start_ms": 500, "duration_ms": 50, "points": [[x3,y3]]}
]}
```

- 坐标同现有 op：设备截图坐标系像素，经 `ScreenMap` 注入口统一映射。
- 单点 `points` 表示按住不动（与 `long_press` 同形）；两点表示直线滑动。
- 超出 stroke 数或总时长上限时返回 `ok=false` 并在 `error` 里点明是哪一条限制，
  不截断、不拆成多次下发——拆开就不是同一个手势，并发语义会丢。
- 协议版本 2 → 3，两端实现必须同步改。

---

## 6. 必须钉死的语义

1. **整块原子。** a11y 多 stroke 手势任一路失败，系统回调是整组 `onCancelled`，
   所以只能整块成功或整块失败。绝不允许留下「摇杆推住了、跳跃没点到，而手指还
   按着」的半截状态；异常路径也必须抬指/松键。
2. **停止必须先释放。** 时间线执行期间按停止，要先抬指/松键再返回。不释放则游戏里
   角色会一直后退。PC 端 `hold` 已有此先例，时间线沿用同一条规则。
3. **失败不吞。** 设备端返回 `ok=false` 要向工作流传播，不能把未执行的手势当成功。
4. **不做时长补偿。** 实际耗时略长于声明总长是正常的（系统调度）；引擎不追赶、
   不补发，只在超出预期时记一行日志。
5. **推杆没有滑动过程（实现期确认）。** 一条 stroke 只能匀速走完整条 path，表达不了
   "滑到位再停住"；`continueStroke` 的续接段要另起一次 `dispatchGesture`，放进同一个
   `GestureDescription` 就不是并发了。所以设备端取"直接按在目标点上保持"，
   `duration` 并入总时长。对推摇杆来说这正是要的效果，但需真机确认游戏接受。

---

## 7. 验证计划

| 项 | 方式 |
|---|---|
| 语法层 | 解析用例：合法块、块内出现 `scan`/`if` 必须报错且指向该行、缺 `requires` 报错 |
| PC 排程 | 用假输入后端记录 (时刻, 动作) 序列，断言偏移顺序与容差 |
| 协议编译 | 断言一个 `timeline` 块编译出的 strokes 列表（起点、时长、点位）与预期一致 |
| 能力门禁 | ADB 通道加载声明了 `device_gesture` 的脚本必须被拒绝并给出可行动原因 |
| 设备实测 | **需要真机**：两路并发手势能否被游戏接受、`getMaxStrokeCount()` 实际值、
  真实触摸取消的表现。PC 侧提供一条探针命令便于单独验证，不混在工作流里 |

---

## 8. 首个消费者

`刷虎骨酒.wf`（在 `config/local`）：桌面端交叠 down/up 改写成 `timeline`，
`#% env` 补 `android`，`press "F"` 换成 `click [general_combat].[assassinate]` /
`[pickup]`（区域已在 `general_combat` 场景定义，坐标由使用者在本地标定）。

盲等也一并收掉：注释写着「直到出现 F 触发任务」，而脚本是 `wait (4, 4.5)`。两个新
区域都是 `is_text: true`，可以改成扫描到提示再动手，两端都受益。
