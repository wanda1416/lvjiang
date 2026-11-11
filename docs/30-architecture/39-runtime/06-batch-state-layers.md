# 批量的状态分层

> 产品语义（四层各回答什么问题、用户能感知到什么变化）见
> [20-requirements/30-batch/04-batch-state-layers.md](../../20-requirements/30-batch/04-batch-state-layers.md)（仅源码检出）。
> 本文只描述数据归属、协调算法与迁移契约。

## 四层与存放位置

| 状态 | 对象 | 存放位置 | 谁能改 |
|---|---|---|---|
| 配置组定义 | `BatchConfigItem` | `batch.json` | 只有「批量配置」窗口 |
| 配置编辑器草稿 | 对话框内部 `_cfg` | 内存 | 对话框自己，点保存才落盘 |
| 本次运行草稿 | `BatchRunDraft` | `interface.json` 根节点的 `batch` | 只有主页面批量页 |
| 执行快照 | `BatchRunSpec` | 内存（并进执行历史） | 点「开始」时冻结，之后不可变 |

## 字段归属

定义层：可见任务与初始顺序 `task_ids`、可见用户与初始顺序 `usernames`、默认勾选
`default_task_ids` / `default_usernames` / `default_units`、调度单元
`execution_unit_key`、Profile 排序键与方向、五个生命周期 wf 路径、
`skip_lifecycle_for_single_item`。定义层**不保存**生命周期参数。

运行草稿：本次勾选与实际顺序（`BatchSelection.order` / `checked`）、本次轮数、本次
是否无人值守、本次是否允许竖屏启动（`allow_portrait`）、生命周期参数
`workflow_params`（按阶段存取值），以及主页面当前配置组 `active_group_id`。草稿按
稳定配置组 ID 隔离，存在会话里只是为了应用重启后恢复页面状态。

旧版 `batch.json` 里的 `workflow_params` 不再读取，也不写迁移：`from_dict` 忽略该键，
下次在配置窗口保存配置组时自然不再写出，参数回落到 wf 文件头的默认值。

两者同在根节点 `batch` 下。活动组与运行草稿对所有实例持久化，应用重启后恢复
上次页面状态；跨实例并发由 interface.json 的跨进程锁串行化。

## 协调与快照

- `BatchSelection.reconcile`：丢弃已不在候选里的条目 → 保留仍在候选里的勾选与顺序
  → 新出现的候选按定义层顺序追加、勾选状态取定义层默认值；只有用户点「恢复默认」
  才走 `from_defaults` 完整重建；切换配置组只换对应草稿。
- `BatchRunSpec.build(定义, 草稿)` 在点「开始」时合成，运行期不再读 `batch.json`、
  不读 session、不看控件。
- `unattended` 存的是有效值：草稿勾了无人值守、且定义层确实配了异常恢复 wf，才为真；
  这个与运算只做一次。
- `allow_portrait` 也存有效值：草稿勾了允许竖屏、且定义层配了异常恢复 wf，才为真。
  调度器用它判断安卓起步画面是否为“交换宽高后才匹配布局”的临时竖屏；是则先执行
  一次 `recover_unattended`（只回登录主页），并在本次强制补上生命周期。
- 生命周期参数随快照深拷贝自运行草稿，按阶段存。参数定义仍从 wf 文件头实时读取，
  快照合成时用实际声明的参数名过滤：换工作流后草稿里的残留键不会生效。wf 脚本文件
  不缓存，运行中修改脚本仍作用于后续阶段。

## batch.json v2 迁移

`batch.json` 升到 version 2：按稳定 ID 索引配置组，`active_group` 移出，
`selected_*` / `rounds` / `unattended` 不再是定义层字段。读取 v1 时一次性转换：补一个
ID、`selected_*` 读进 `default_*`（只保留「初始值」这一半语义）、`rounds` 与
`unattended` 丢弃。写出去只有新格式，所以是单向读取，不是常驻兼容层。

## 实现位置

| 关注点 | 位置 |
|---|---|
| 定义层模型与持久化 | `core/batch_config.py` |
| 运行草稿、协调规则、session 读写 | `core/batch_run.py` |
| 执行快照 | `core/batch_scheduler.py`（`BatchRunSpec`） |
| 定义层唯一入口 | `ui/batch/batch_config_dialog.py` |
| 生命周期参数编辑面板 | `ui/batch/lifecycle_params.py` |
| 运行草稿唯一入口 | `ui/batch/batch_tab.py` |

一个可以直接检查的结构性证据：`batch_tab.py` **不再导入** `save_batch_config`，
`batch_config_dialog.py` **不再有**任何读盘反向合并。

公共调度器与报告位于 `core/batch_scheduler.py`、`core/batch_report.py`。桌面 `BatchWorker` 只适配 Qt 线程及信号，手机适配器使用后台线程与同一套编排；详情见 [Android 批量执行架构](../38-platform/10-android-batch.md)。
