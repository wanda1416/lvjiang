# 全任务采集与离线工作流模拟工具

> 最后更新：2026-11-23（基线 v0.15.19，未提交的本地实现）。已接桌面/批量任务装配与受控WF库；不是全量游戏WF验收。

## 能验证什么

工具组合真实 `WorkflowEngine`、真实RapidOCR与模拟设备。设备只返回当前状态的PNG；只有匹配的
动作或显式虚拟计时边才能迁移。点击错误不会“播放下一张正确图”。相同画面可以对应不同状态，
未录分支、未接入能力、非终态和业务失败均不能记通过。现有单元/契约测试继续保留；它们与场景模拟互补。

目前提供桌面/批量显式录制入口、不可变原始记录与人工标注侧车转换、受控WF依赖打包、任务/fixture发现及多进程CLI。
没有真实游戏fixture，未建模的领域能力仍拒绝执行。合成例子只证明真实任务装配、引擎/OCR/输入/虚拟时间接缝。
不能通过给定OCR答案、mock成功返回或跳过未知分支宣称全量验证完成。

实现入口（仅源码检出）：[simulation包](../../src/lvjiang/core/simulation/)、
[CLI](../../scripts/simulate_workflows.py)、[回归测试](../../tests/core/simulation/)。
诊断FIFO、任务反馈联动的后续边界见[诊断方案](../20-plans/03-platform/30-debug-frame-history.md)。
本工具不实现AI接管，也不上传截图。

## 最小可运行示例

在仓库根使用已有 `.venv`；命令中的目录由使用者选择，须为新的临时目录，不放入真实配置目录。
Windows将解释器路径换为 `.venv\Scripts\python.exe`。以下数字均为工具参数，不是实测性能承诺。

```bash
.venv/bin/python -B scripts/simulate_workflows.py demo /tmp/wf-demo-new
.venv/bin/python -B scripts/simulate_workflows.py run /tmp/wf-demo-new /tmp/wf-demo-new --output /tmp/wf-results-new --jobs 2 --timeout 30 --record
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

## 桌面与批量入口（默认关闭）

- 主窗口运行控制区勾选「本次任务全帧采集」，启动下一个本机任务时消费该选项；后台worker在真正执行前启用，结束后封口。
- 批量页勾选「本次批量全帧采集」，启动时冻结到本批次`BatchContext`并清空勾选；后续每个实际任务独立目录。
- 不持久化开启状态；不补录过去帧。开始后改变勾选/切换所看目标/关闭页面，不改变已启动运行的任务或目标身份。
- 数据在客户端目录`logs/recordings/<capture UUID>/`，日志提示路径、封口状态和不完整原因；不上传。
- 复用真实`task_run_id`；批量使用history创建的子任务ID和`batch_run_id`，生命周期/恢复阶段使用所属运行ID与独立录制UUID，不伪造子任务历史。
- 业务completed/failed/cancelled与采集complete/incomplete分开；录制启动、额度、编码或封口失败不改变业务结果。停止后仍在任务worker有限收尾，不阻塞Qt主线程。
- 该入口在离屏Qt真实`WorkflowWorker`、真实`BatchScheduler`与假硬件边界下已验证；未连接真机或测量Windows采集负载。

## 全任务采集：显式装配，不扫描真实数据

`FullTaskRecorder`与诊断方案的100帧FIFO分开：全任务模式保存从启用起全部后端取图，不轮转删除开头。
用无损PNG保存识别像素，不用诊断WebP替代识别输入。默认磁盘512,000,000 bytes、自有原图
32,000,000 bytes、有界队列32项、一个编码worker。队列还承载元数据；这些是可调上限，不是总RSS。
原始后端缓冲、编码临时对象、PNG bytes、OCR及Python运行时另计。准入复制有成本，编码/磁盘不在识别线程等待。

以下是非Qt宿主的低层集成示意；生产桌面/批量通过`recording_scope`装配，不会因勾选而自行连接设备。本轮没有执行任何真实设备采集：

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
桌面/批量额外保存启动时内存布局的`layout.json`及哈希（≤2,000,000 bytes），仅结构坐标/识别定义，不遍历配置树；模板图片资源尚未打包。
`.wf`入口与递归WF导入按当次resolver的local优先/system后备解析到`workflow_bundle/`，限制根内路径、≤512文件、每个≤1,000,000 bytes、合计≤8,000,000 bytes。
库与布局计入宿主磁盘额度；不自动复制session、账户、用户数据库或参数原值。参数/领域场景须人工声明。Python导入会将bundle标为unsupported，但不妨碍真实任务继续采集截图。

## 录完后标注和转换

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

## 图契约与失败判定

转换后的`scenario.json`字段与动作实现以[scenario.py](../../src/lvjiang/core/simulation/scenario.py)的
`Scenario.load`、`SimulatedDevice`为准（仅源码检出）。最低要求是版本、来源、WF文件及哈希、状态图、起点与终点。
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

## 隔离、能力限制与后续验收

模拟worker在导入产品前换LVJIANG_ROOT/HOME/临时/缓存目录，配置、history、日志均进本case运行根。
父进程不向子进程透传账号/模型token等环境项。审计门禁拒绝网络socket、创建外部进程、运行根外写文件/数据库，
读取限于fixture、源码、解释器/系统依赖及本case根；不扫描真实配置。
这是可信WF的意外I/O防护，**不是恶意代码的操作系统级沙箱**，不要运行来历不明脚本或插件。

已支持受控、完整哈希固定的WF库导入，使用真实引擎原有resolver执行；不替换库函数返回值。路径逃逸、循环、缺库/改库会拒绝。
外部/Python过程、插件转换/副作用工具、profile账本、wall-clock/datetime、Android应用生命周期、剪贴板、timeline/输入轨迹等仍未建模；
人工交互也不自动回答。`user_get`仅支持上述显式只读快照，不等于开放profile或插件。只能运行已支持能力的WF，不保证现有所有WF可直接加载。
真实游戏接入应逐类为能力声明fixture/adapter和隔离契约，不靠放开任意插件执行或返回固定成功补齐。
核心仅接受通用capture/input/clock/observer，不硬编码游戏或插件名；未来领域能力由宿主按声明hook装配。

分阶段验收：

1. 保持单元/契约测试；工具层验证全量超过100帧、稳定副本、限额不完整、哈希校验、动作不匹配不前进、终态/业务结果与取消超时。
2. 合成PNG通过真实OCR+真实WF闭环，两个进程并行且结果/状态/配置不串；不把synthetic来源改为recorded。
3. 桌面/批量开关和真实任务装配已接入；实际采集仍待用户在真机主动开启，检查窗口/ROI/布局/参数版本，补已观察分支和失败反例。
4. WF依赖打包已支持；逐步为其他领域能力补真实隔离适配。每个WF/目标/分支明确supported或unsupported，批量覆盖表不能把unsupported当成功。
5. 用真实中文游戏画面和Windows测量OCR、小字保真、采集吞吐/队列、峰值RSS/磁盘/并行上限。尚未做此测量，不承诺性能或成功率。

没有真机fixture时，第3步真实采集、第4步全部领域能力及第5步不可勾完成；本次实现不代表100帧诊断FIFO、自动反馈上传或AI恢复已完成。


## 受控依赖、发现与覆盖报表

```bash
.venv/bin/python -B scripts/simulate_workflows.py package /path/to/task.wf /tmp/task-bundle-new --root /path/to/local/workflows --root /path/to/system/workflows
.venv/bin/python -B scripts/simulate_workflows.py catalog --workflows-root config/system/workflows --fixtures-root /tmp/reviewed-wf-fixtures --output /tmp/wf-catalog.json
.venv/bin/python -B scripts/simulate_workflows.py run-discovered /tmp/wf-catalog.json --output /tmp/wf-coverage-new --jobs 2 --timeout 30
```

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
