# 全任务采集与离线工作流模拟（设计）

已有模拟框架与受控WF库首版实现；桌面仅提供可选的最近帧诊断，不再提供全量录制开关。尚未完成真实游戏采集与全量WF验收。
实现入口：[simulation包](../../../../src/lvjiang/core/simulation/)（仅源码检出）、
[CLI](../../../../scripts/simulate_workflows.py)（仅源码检出）、
[回归测试](../../../../tests/core/simulation/)（仅源码检出）。本工具不实现AI接管，也不上传截图。

## 1. 目标与验证边界

工具组合真实 `WorkflowEngine`、真实RapidOCR与模拟设备。设备只返回当前状态的PNG；只有匹配的
动作或显式虚拟计时边才能迁移。点击错误不会“播放下一张正确图”。相同画面可以对应不同状态，
未录分支、未接入能力、非终态和业务失败均不能记通过。现有单元/契约测试继续保留；它们与场景模拟互补。

目前提供工具菜单的最近帧诊断、CLI/显式宿主的不可变全任务原始记录与人工标注侧车转换、受控WF依赖打包、任务/fixture发现及多进程CLI。
没有真实游戏fixture，未建模的领域能力仍拒绝执行。合成例子只证明真实任务装配、引擎/OCR/输入/虚拟时间接缝。
不能通过给定OCR答案、mock成功返回或跳过未知分支宣称全量验证完成。

## 2. 与识别帧诊断的分工

两条链路复用引擎旁路（GUI诊断仅用capture observer，全量录制另包装capture/input），但**用途、保留策略与产物相反**，不共用队列、编码或丢弃策略：

| 维度 | 识别帧诊断（内存环已实现，诊断包仍规划） | 全任务采集与模拟（本层，已实现首版） |
|---|---|---|
| 目的 | 定位一次偶发失败或人工介入的现场 | 把一次真实跑通固化成可反复回放的回归夹具 |
| 保留策略 | 默认最近100帧、50 MB双限FIFO，允许丢帧并按原因聚合 | 从启用起全量，不轮转；任何丢失标 `complete:false` 并拒绝转换 |
| 图像 | 有损 WebP 诊断副本（候选参数，未校准） | 无损 PNG，保留识别像素 |
| 产物 | 当前仅内存帧与元数据；incident ZIP/查看器尚未实现 | 原始 tape + 人工侧车 → 状态图 fixture → 覆盖报表 |
| 资源 | 内存环，受编码与元数据预算约束 | 流式落盘，磁盘 512MB / 原图 32MB / 单编码 worker |
| 文档归属 | [诊断方案](../../../20-plans/13-debug-frame-history.md)（仅源码检出） | 本页（30-design，已实现） |

## 3. 实现结构

| 模块 | 职责 |
|---|---|
| [recording.py](../../../../src/lvjiang/core/simulation/recording.py)（仅源码检出） | `FullTaskRecorder`：准入复制、有界队列、单编码 worker、配额与完整性记账、`wrap_capture`/`wrap_input`/`observer` |
| [scenario.py](../../../../src/lvjiang/core/simulation/scenario.py)（仅源码检出） | `Scenario.load` 校验与 `SimulatedDevice`：状态图、动作匹配、虚拟时间推进 |
| [runner.py](../../../../src/lvjiang/core/simulation/runner.py)（仅源码检出） | 每 case 独立子进程/运行根、审计门禁、watchdog、结果汇总 |
| [catalog.py](../../../../src/lvjiang/core/simulation/catalog.py)（仅源码检出） | 静态能力预检、fixture/入口哈希关联、覆盖报表（catalog v2） |
| [bundle.py](../../../../src/lvjiang/core/simulation/bundle.py)（仅源码检出） | 受控 WF 依赖打包与哈希清单 |
| [policy.py](../../../../src/lvjiang/core/simulation/policy.py)（仅源码检出） | 纯函数白名单与不可用节点名单（见 §6） |
| [host.py](../../../../src/lvjiang/core/simulation/host.py)（仅源码检出） | 显式宿主的 `recording_scope` 装配；非当前GUI入口，不把 UI 引入 core |

引擎侧只新增两个可选接缝：`capture_observer`（旁路收新帧/复用事件，不替换识别输入）与
`runtime_clock`（只替换已接入等待原语的单调时钟/睡眠，默认真实时钟）。核心仅接受通用
capture/input/clock/observer，不硬编码游戏或插件名；未来领域能力由宿主按声明 hook 装配。

## 4. 原始记录与显式装配

`FullTaskRecorder`与诊断方案的100帧FIFO分开：全任务模式保存从启用起全部后端取图，不轮转删除开头。
用无损PNG保存识别像素，不用诊断WebP替代识别输入。默认磁盘512,000,000 bytes、自有原图
32,000,000 bytes、有界队列32项、一个编码worker。队列还承载元数据；这些是可调上限，不是总RSS。
原始后端缓冲、编码临时对象、PNG bytes、OCR及Python运行时另计。准入复制有成本，编码/磁盘不在识别线程等待。

以下是显式宿主的低层全量集成示意；GUI最近帧诊断不调用`recording_scope`。CLI `--record`全量录制能力保留，尚无真实设备采集：

```python
from pathlib import Path
from lvjiang.core.simulation.recording import FullTaskRecorder
from lvjiang.workflows.engine import WorkflowEngine

recorder = FullTaskRecorder(
    Path(output_directory), workflow=Path(workflow_path), task_run_id=existing_task_run_id,
    context={"engine_revision": source_revision},
)
capture = recorder.wrap_capture(existing_capture_backend, engine_epoch="engine-1")
input_ctrl = recorder.wrap_input(existing_input_backend, capture)
engine = WorkflowEngine(
    capture=capture, input_ctrl=input_ctrl, ocr=existing_ocr, layout=existing_layout,
    capture_observer=recorder.observer("engine-1", capture=capture),
)
try:
    engine.execute(workflow_path)
finally:
    recorder.close()  # 有限等待；在任务后台线程收尾，不在Qt主线程调用。
```

- 同时使用`wrap_capture`与`capture_observer`：前者记录后端getter（含wait-stable与lossless），
  后者将引擎`frame_seq/source/reused`关联到后端`device_seq`。只传observer不等于全后端采集。
  scope同时重绑/恢复缓存的KeyStateRegistry；真实按键释放仍在封口前记录。输入包装显式透传target_hwnd写入，不能令窗口重绑只改包装器。
- `engine_epoch + device_seq`标识原始样本；引擎`frame_seq`是消费快照，不是全局录像帧号。
  from-last只记消费，不重复复制；wait-stable新取图不能篡改旧快照的绑定。
- 异步准入前复制BGR uint8原图，编码失败不改变工作流识别输入。录制失败停止准入、标记不完整，
  不轮转、不静默丢帧后声称完整，也不自动停止业务任务。宿主可读`status`显示原因。
- 输入旁路只记录请求坐标、有限参数与分发/失败，不把“调用返回”当作游戏执行成功，不冒称后端随机偏移后的真实落点。
- `context`只接受显式版本/哈希/目标元信息，复用既有task_run_id；不扫描session、凭据或用户数据库。
  一个recorder对应一个显式任务/目标；多个目标必须用不同目录与实例。额度按任务计算，整个批次及历次录制累计另计，当前无自动清理，需主动管理磁盘，不能理解为所有录制合计仅512MB。
- 自动OCR结果/业务状态标注尚未接入；`event()`可由宿主补有界事件，日志不能自动推导可靠状态图。
- 队列/原图/元数据/磁盘/复制/编码异常、收尾超时都会留下`complete:false`及原因；转换器拒绝此记录。
  进程崩溃/磁盘故障不能保证落盘，编码原生调用无法强制取消，不完整记录只能用于人工诊断。

原始目录包含`manifest.json`、`events.jsonl`、`frames/*.png`。清单有格式版本、完整性、录制UUID、可选任务ID、
WF哈希、帧/事件计数、事件流SHA256及Python/OpenCV/numpy环境版本。事件有顺序号与单调相对时间。
显式`recording_scope`宿主额外保存启动时内存布局的`layout.json`及哈希（≤2,000,000 bytes），仅结构坐标/识别定义，不遍历配置树；模板图片资源尚未打包。
`.wf`入口与递归WF导入按当次resolver的local优先/system后备解析到`workflow_bundle/`，限制根内路径、≤512文件、每个≤1,000,000 bytes、合计≤8,000,000 bytes。
库与布局计入宿主磁盘额度；不自动复制session、账户、用户数据库或参数原值。参数/领域场景须人工声明。Python导入会将bundle标为unsupported，但不妨碍真实任务继续采集截图。

## 5. 工具 → 任务诊断（开发预览，默认关闭）

主窗口与批量页不再显示全帧采集开关。工具菜单打开非模态「任务诊断」对话框：

- **诊断调试**：唯一主开关「启用开发诊断模式」，默认关闭；输入默认100帧、50 MB（十进制50,000,000 bytes）。只存本进程，不写用户配置；重启恢复关闭。
- **模拟运行**：暂为空白页签，没有完整GUI，不把CLI伪装成可用按钮。
- 设置在下次单任务/批次启动时冻结；后续切页、关闭对话框、中途启停或修改限额不影响已运行的目标、任务与本批次后续脚本。不补录过去帧。
- 关闭时不构造诊断scope、线程、设备包装、目录或参数/布局bundle，不额外截图；输入、KeyStateRegistry、target_hwnd、clock、OCR路径与原任务执行一致。CLI全量tape仍走独立实现。

最近帧实现位于[task_diagnostics.py](../../../../src/lvjiang/core/task_diagnostics.py)（仅源码检出）：

- 仅旁路任务引擎实际消费的原始识别画面，不截ROI、不画标注，不采集UI预览或遍历后端视频帧。独立`screenshot`导出不计入；随后`from last`用于识别时仍可收录该帧。压缩副本绝不替换识别输入。
- 每次执行独立capture_scope，绑定现有task_run_id、batch_run_id和目标；每scope以frame_seq去重，重复消费不重复压缩。批量使用history子任务ID；生命周期/恢复脚本沿用所属运行ID并以capture_scope区分，不伪造历史。
- 每任务独立WebP内存环（quality80，待真实样本校准），按FIFO淘汰直到帧数与「压缩图+UTF-8帧元数据」字节数**同时不超限**；恰好100帧或50 MB可保留。单帧压缩后大于整个配额则丢弃该帧，不清空已有帧。
- 一个编码worker；准入即复制稳定BGR uint8原图，最多4个原图（含编码中）且合计不超过32,000,000 bytes。原图超限、队满、编码失败均计数降级，不等待编码/队列空位，不改变业务结果。复制本身有开销。
- 计数器、队列、元数据均有界；源码对象/编码器临时内存、Python对象开销另计，50 MB不是总RSS。并行任务各有预算；宿主只保留最近启动任务的缓存，其他任务退出后释放，不能把每任务配额当全程序内存上限。
- worker在任务线程退出前最多等待2秒收尾，超时丢弃排队帧及迟到结果并标记incomplete，不能强杀原生编码调用。业务completed/failed/cancelled单独记录；采集错误不改变返回值、不自动确认弹窗。
- 当前通过宿主`_task_diagnostics.latest.snapshot()`查看有界元数据/丢帧统计，`frames()`取得只读WebP和关联元数据；调用方若额外保留这些对象，其内存自负。封口日志记录任务ID、scope、帧数、字节和丢帧原因。
- 不落盘、不生成ZIP、不上传；退出即清空。异常包、反馈收集仍是[后续诊断方案](../../../20-plans/13-debug-frame-history.md)（仅源码检出），本次不扩展。此有损缓存不适合作为全任务模拟fixture。
- 离屏Qt真实WorkflowWorker/BatchScheduler、真实引擎与合成OCR场景已验证装配；未连接真机，未测量Windows录制负载/游戏小字质量。

## 6. 录完后标注和转换

录制只证明已观察路径，不自动知道所有分支。原始tape保持不变，用绑定其哈希的侧车文件选择代表帧、声明状态与动作边：

```bash
.venv/bin/python -B scripts/simulate_workflows.py annotation-template /tmp/task-tape /tmp/task-annotations.json
# 人工查看frames与events.jsonl，编辑侧车中的frames/start/edges。
.venv/bin/python -B scripts/simulate_workflows.py compile /tmp/task-tape /tmp/task-case-new --workflow /tmp/source-task.wf --terminal done --annotations /tmp/task-annotations.json --context /tmp/reviewed-fixture-context.json
.venv/bin/python -B scripts/simulate_workflows.py run /tmp/task-case-new --output /tmp/task-case-results-new
```

`--context`为可选、人工审查的≤2,000,000 bytes JSON；不指定时仍可转换。只允许`layout`、`params`、`environment`、`run_env`。
默认复用已校验的录制布局；context可显式覆盖。`params`作为真实引擎初始变量，`environment`只支持`delay_params/session/attributes/unattended`；
延迟为非负有限数值，unattended必须布尔值。`attributes`为按用户名声明的字段快照，真实`user_get`读取该数据；未知用户/字段报unsupported，已知不存在须显式`null`，不回退真实用户文件。
`session`目前仅绑定隔离引擎上下文，不会因此开放被禁止的领域函数。场景用`env/check_env`时必须明确`run_env`，不能空环境走空分支后冒充通过。
例如：`{"params":{"synthetic_input":7},"run_env":"desktop","environment":{"unattended":true,"attributes":{"synthetic-user":{"level":7}}}}`。
这些是合成字段，不是自动提取的真实账户配置。context会成为fixture内容，必须自行确认没有凭据。

侧车是UTF-8 JSON，字段如下（哈希和事件号必须换成实际值；不是可直接执行的完整fixture）：

```json
{
  "format_version": 1,
  "events_sha256": "原始事件流SHA256",
  "workflow_sha256": "录制时WF的SHA256",
  "frames": {"1": "start", "8": "loading", "15": "done"},
  "start": "start",
  "edges": [
    {"source": "start", "target": "loading", "action": "click", "match": {"box": [70, 60, 200, 130], "button": "left"}},
    {"source": "loading", "target": "done", "action": "timer", "after": 1}
  ]
}
```

`frames`键是capture事件的`event_seq`，不是列表下标/日志行猜测，也不是引擎frame_seq；值是人工定义的状态ID。
未选择帧仍留在原始记录，不能据此声称其分支已覆盖。两个语义状态允许选择同一视觉内容。
侧车的图为唯一标注来源，不与原tape的边叠加；错误哈希、缺失事件、缺帧/损坏会拒绝转换。
不提供侧车时可使用采集时显式`state_id/edge()`标注；同状态的不同像素变体会拒绝转换，要求人工选代表或拆状态。
转换记录原manifest/事件/标注哈希、原始总帧数及代表状态数，不修改raw tape。

## 7. 图契约与失败判定

转换后的`scenario.json`字段与动作实现以[scenario.py](../../../../src/lvjiang/core/simulation/scenario.py)（仅源码检出）的
`Scenario.load`、`SimulatedDevice`为准。最低要求是版本、来源、WF文件及哈希、状态图、起点与终点。
可显式补`layout/params/expected_return/limits/run_env`；`run_env`只能为空、`desktop`或`android`，不按宿主OS猜设备。
布局/参数仍须人工审查，不允许夹带真实账户凭据。

- 每个state引用带SHA256的PNG；相同画面不必同状态。路径必须在fixture内，元数据、WF、像素、文件与总帧字节均有限额。
- 动作支持click/drag/move/scroll/key_down/key_up/mouse_button，以及显式timer。
  坐标box是包含边界的像素矩形，按该状态真实尺寸验证；不使用“整屏都是有效点击”的全局容差。
- 点击需指定button与box；拖动需起终点box。hold/duration/interval缺省只匹配未指定值，非任意时长通配。
  可显式声明数值或范围，以及状态进入后的min_elapsed/max_elapsed时间窗；多边同时命中报错。
- 等待推进虚拟时间，timer必须正值、同一状态最多一条；纯截图不推进时间。
  纯循环靠事件上限终止，显式等待靠虚拟时限，OCR/native卡住靠父进程墙钟watchdog杀子进程。
- 只有显式等待/输入持续时间推进虚拟时间；真实OCR的CPU耗时不计入虚拟时间。靠无等待忙轮询/未建模外部异步才能前进的路径不在当前等价保证内。
  动作边表示请求被接受，随后推进其显式持续时间；需要释放边界时用key_down/key_up或mouse_button分边，不推测游戏物理过程。
- 默认零隐式输入延迟、零随机偏移；显式前后延迟区间取中点以便重现。真实随机扰动需另建有证据的fixture范围，不冒充生产时序等价。
- 不知道的输入、错误点击、未支持参数、遗漏分支会记录失败/unsupported，不自动等待后跳到正确图；失败是粘性的，WF捕获异常也不能抹掉。
- 必须终态、无设备失败、符合既有业务返回协议；有expected_return时还要相等。负数/失败dict不因“没抛异常”而成功。

## 8. 能力边界：支持什么、明确拒绝什么

模拟worker在导入产品前换LVJIANG_ROOT/HOME/临时/缓存目录，配置、history、日志均进本case运行根。
父进程不向子进程透传账号/模型token等环境项。审计门禁拒绝网络socket、创建外部进程、运行根外写文件/数据库，
读取限于fixture、源码、解释器/系统依赖及本case根；不扫描真实配置。
这是可信WF的意外I/O防护，**不是恶意代码的操作系统级沙箱**，不要运行来历不明脚本或插件。

以下节点在静态预检阶段直接拒绝执行（`policy.UNSUPPORTED_NODES`），不做「猜一个合理结果」的降级：

| 拒绝节点 | 原因 |
|---|---|
| `Paste` | 输入文本未建模，且会把用户内容带进 fixture |
| `Timeline` / `TimelineEntry` / `ReplayInputTrace` | 输入轨迹/并发手势时间线未建模 |
| `Align` | 对齐动作依赖真实画面反馈，无等价模拟 |
| `AndroidAppAction` | Android 应用生命周期未建模 |
| `Place` | 依赖游戏内位置状态，未建模 |

其余受限能力：外部/Python过程、插件转换/副作用工具、profile账本、wall-clock/datetime、剪贴板、
人工交互（不自动回答）均未建模。`user_get`仅支持`environment.attributes`声明的只读快照，不等于开放profile或插件。
只能运行已支持能力的WF，不保证现有所有WF可直接加载；真实游戏接入应逐类为能力声明fixture/adapter和隔离契约，
不靠放开任意插件执行或返回固定成功补齐。

已支持受控、完整哈希固定的WF库导入，使用真实引擎原有resolver执行；不替换库函数返回值。路径逃逸、循环、缺库/改库会拒绝。

## 9. 工具入口与命令

在仓库根使用已有 `.venv`；命令中的目录由使用者选择，须为新的临时目录，不放入真实配置目录。
Windows将解释器路径换为 `.venv\Scripts\python.exe`。以下数字均为工具参数，不是实测性能承诺。

```bash
.venv/bin/python -B scripts/simulate_workflows.py demo /tmp/wf-demo-new
.venv/bin/python -B scripts/simulate_workflows.py run /tmp/wf-demo-new /tmp/wf-demo-new --output /tmp/wf-results-new --jobs 2 --timeout 30 --record
.venv/bin/python -B scripts/simulate_workflows.py package /path/to/task.wf /tmp/task-bundle-new --root /path/to/local/workflows --root /path/to/system/workflows
.venv/bin/python -B scripts/simulate_workflows.py catalog --workflows-root config/system/workflows --fixtures-root /tmp/reviewed-wf-fixtures --output /tmp/wf-catalog.json
.venv/bin/python -B scripts/simulate_workflows.py run-discovered /tmp/wf-catalog.json --output /tmp/wf-coverage-new --jobs 2 --timeout 30
```

`demo`生成明确标为`synthetic`的640×160画面和WF：识别12345→点击识别位置→确认同画面的加载态→显式等待1秒→识别67890→返回0。
开始态与加载态画面相同，但加载完成必须经过计时边；截图调用本身不前进。
`--record`显式把此次模拟运行的全部后端取图另存到`case-*/recording/`，来源仍是synthetic，不冒充游戏采集。
不加该参数则不录制；采集完整性在`recording_complete`单独报告，不反写任务执行状态。
`run`为每个输入案例建独立Python子进程/运行根、OCR实例、随机状态、日志和结果。
相同fixture也可作为两次独立案例并行运行。`--jobs`限制并行数，实际CPU/RSS需在目标机器测量。

每个`case-*`下有`result.json`、`events.jsonl`、有限轮转的`workflow.log`、`worker.log`与隔离`runtime/`。
父目录`run-*.json`汇总；退出码0要求全部案例`passed`，否则1。
`unsupported`与`timeout`都不是通过。开启`--record`后录制不完整也令CLI退出1，任务结果与录制结果仍分开。结果中的访问状态/边仅代表**此fixture声明的图**，不代表所有业务路径覆盖率。

`package`只复制实际入口/递归WF依赖；独立调用后需在fixture里声明`workflow_bundle`相对路径，入口哈希必须一致。
桌面采集与`compile`已有该步骤，无需手工重复打包。根的顺序就是显式覆盖优先级，不递归发现插件或用户数据。
`catalog`仅静态读取所给WF/fixture根，不执行游戏任务、不扫描session；只对有效且入口SHA匹配的fixture建立关联。
静态能力预检保守检查可达过程/语句，未按任意条件消除所有未走分支；不支持时可进一步为真实分支建模，不能当作通过。
静态能力状态、是否有录制、实际运行结果是不同维度：supported只表示该入口可达语句通过能力检查，不表示游戏已通过或领域上下文已声明。
`run-discovered`在独立子进程并行执行已匹配fixture，输出`coverage.json`：passed/failed/unsupported/timeout/missing_recording及独立capability_counts。
库不当成独立任务；缺记录、无任务、无效fixture或任一非通过结果均使总覆盖失败，返回码1；不是把有fixture的少数全绿就宣称所有WF通过。
入口与依赖哈希都与catalog时的源文件比对；仅入口未变而源库变化的旧fixture会明确拒绝，不拿旧库的通过冒充当前回归。catalog v2固定显式源根、WF文件清单/哈希与fixture整个scenario清单哈希（包含布局/参数/环境/状态图），分发前后重新核对当前源树，变化/缺失均拒绝作为当前源码覆盖。旧catalog须重建；不会因fixture仍与旧catalog一致就放行。
布局是录制/人工声明的场景，不自动读取真实用户当前配置；它是否仍代表实际游戏页面须重新录制或人工复核。

回归中有两条未修改的现有系统库证据：`lib/numeric.wf`真实gcd过程与`subcall/page_detection.wf`真实菜单识别过程。
后者使用仓库内明确合成的「包裹/培养」PNG经过真实RapidOCR，不预设OCR答案；不是游戏截图，也没有宣称真实页面布局/中文OCR已全覆盖。

## 10. 对外部「全量录制 + 并行帧跳转」方案的取舍

外部曾提出 v1.0「一次录制 → 状态机建模 → 多进程并行回放」方案。本表只落定**采纳/不采纳**；
标「未实装」的条目已按优先级移到
[离线 WF 模拟的回归门禁增强（计划）](../../../20-plans/14-wf-simulation-hardening.md)（仅源码检出），
本层不重复写计划、也不把它们当已有能力：

| 外部建议 | 处置 | 理由与现状 |
|---|---|---|
| 原分辨率、无损录制（不缩放、不转 WebP） | 采纳，已实装 | 布局比例裁剪与灰度模板匹配强依赖原始画布尺寸 |
| 图片按内容寻址去重（sha256/缩略图判同图） | 暂不采纳，见计划 P2 | 现状每帧独立 `frames/NNNNNNNNN.png` 且已能跑通；去重只共享字节、不合并事件与帧号，收益待样本量证明 |
| MAD/感知哈希自动生成状态节点与边 | 不采纳 | 会把多轮循环合并、把「点了没反应」抹成自动边；状态与边必须人工侧车确认（§6），MAD 至多作提示 |
| 比例坐标 + 欧氏容差（`x_ratio/tol`）、`match_direction_only` 作默认匹配 | 不采纳为默认，宽容档见计划 P1 | 现状是 `box` 像素矩形按状态真实尺寸校验；放宽会吞掉「按钮偏移 5% 也该失败」的回归，宽容档只能是非门禁档 |
| `mismatch_policy: stay`（点空不判失败） | 未实装 → 计划 P1 | 现状失败粘性；若做需重试/步数上限与 `tolerated_mismatch` 计数 |
| `paste` / `key_hold` 动作与输入轨迹回放 | 不采纳 | `Paste`/`Timeline`/`ReplayInputTrace` 在 `UNSUPPORTED_NODES` 硬拒（§8）；输入文本也不该进 fixture |
| `ui_script`：无人值守下误用 `confirm` 即判失败 | 未实装 → 计划 P0 | 现状人工交互不自动回答；这条能把「无人值守误用 confirm 卡死整批」变成回归门禁 |
| `must_visit_states` / `forbid_states` 断言 | 未实装 → 计划 P0 | 现状只在 `result.json` 输出 `visited_states`/`visited_edges`，没有断言 |
| 失败红绿 Diff 标注图 | 未实装 → 计划 P1 | 若做必须复用诊断方案的坐标语义（请求坐标≠实际落点、几何不可信只出文字） |
| 两档 OCR 缓存（`--ocr-cache` / 发布门禁强制真推理） | 未实装 → 计划 P1 | 键至少要含模型 hash、OCR 清洗版本与布局；报告须标 `cached`，不进发版门禁 |
| Profile 初始切片 + `profile_expected` 断言 | 不采纳；能力前置见计划 P2 | profile账本未建模；真实用户数据不入仓，只能走 `environment.attributes` 显式只读快照 |
| 注入虚拟 clock 即可模拟跨小时/quota/每日刷新 | 未实装 → 计划 P2 | 现状只有显式 `wait`/输入时长推进虚拟时间；`datetime` 类逻辑未建模，会走 unsupported |
| 「彻底替代现有全 Mock 假测试」 | 不采纳 | 二者互补：mock 覆盖 DSL 控制流与未录分支的异常路径，模拟只覆盖录过的画面路径 |
| GitHub LFS 私有仓作为前置 | 不采纳为前置，存储策略见计划 P2 | 仓库当前无 LFS 配置；现状用合成小图 |
| `sanitize` 一键打码后即可安全提交 | 不采纳为承诺 | 固定区域遮罩会破坏被测识别，且不能保证删尽敏感像素；只能做人工辅助 + 人工复核并注明已遮区域 |
| 12MB / 长边 1280 / Q75、整份夹具 8–18MB、1.5–2 天工期、30 秒抓出、4 Worker 连续 3 轮 100% | 不采纳为事实 | 前者属诊断方案的候选参数，其余均未实测；本文只写工具参数与可复现结论 |


## 11. 未完成与验收

分阶段验收：

1. 保持单元/契约测试；工具层验证全量超过100帧、稳定副本、限额不完整、哈希校验、动作不匹配不前进、终态/业务结果与取消超时。
2. 合成PNG通过真实OCR+真实WF闭环，两个进程并行且结果/状态/配置不串；不把synthetic来源改为recorded。
3. 桌面改为工具菜单的可选最近帧诊断；全量采集仍保留CLI/显式宿主接口。真实游戏fixture仍需后续授权采集，检查窗口/ROI/布局/参数版本，补已观察分支和失败反例。
4. WF依赖打包已支持；逐步为其他领域能力补真实隔离适配。每个WF/目标/分支明确supported或unsupported，批量覆盖表不能把unsupported当成功。
5. 用真实中文游戏画面和Windows测量OCR、小字保真、采集吞吐/队列、峰值RSS/磁盘/并行上限。尚未做此测量，不承诺性能或成功率。

没有真机fixture时，第3步真实采集、第4步全部领域能力及第5步不可勾完成；本次实现不代表100帧诊断FIFO、
自动反馈上传或AI恢复已完成。[计划](../../../20-plans/14-wf-simulation-hardening.md)（仅源码检出）
里的门禁增强、OCR 缓存档、宽容匹配档与差异图也都还没实装，不能当现状。
