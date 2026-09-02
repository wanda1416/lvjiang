# 调律历史与管理架构

## 1. 数据流

```text
AutoTuningWorkflow
        │ 现有生命周期事件
        ▼
TuningRunSession ── TuningResultProjector
        │
        ├── 每件终态写入 tuning_history.db
        ├── 运行结束写入汇总状态
        ├── 实时 TuningResultStore 使用同一 Projector
        ├── 历史 TuningResultStore 加载不可变快照
        └── TuningHistoryTelemetrySource 生成匿名投影
```

`TuningResultProjector` 是结果分类的唯一实现，负责把槽位进入、装备开始、
扫描决策、重置、逐轮结果和装备结束事件归约为 `TuningEquipmentResult`。
Qt Store 只负责信号和集合；SQLite repository 不依赖 Qt。投影器提供
`split_resets` 边界策略：实时 Store 使用连续模式，历史会话使用拆分模式。

历史拆分模式收到成功 `equipment_reset` 时，立即封存重置前记录，并以
`after_affixes` 建立新的顺序 ID。后一条记录的轮次从零重新累计；随后发生的
冷却、次数耗尽或回收只归属于后一条记录，不回写重置前记录。

逐轮数据由 `round_prepared` 和 `tune_round_completed` 按 `round_no` 合并。
准备事件保留狗粮选择、决策理由、库存和 `will_tune`；完成事件补入新词条、
评级与最终材料状态。准备后停止的记录标记 `completed=false`，保留在本地历史
供用户解释决策，但匿名投影与词条分布分析都会跳过它，因为没有实际产出。

工作流的接入边界只有：

1. 规则和部位解析完成后创建 `TuningRunSession`；
2. `_emit_progress()` 先交给历史会话，再发 Qt 信号；
3. 运行结束或异常退出时封存运行摘要。

装备判断、调律、回收和重置执行器不依赖历史模块。

## 2. 数据库

默认路径为 `config/session/tuning_history.db`，与 `profile.db` 分离。

### 2.1 表

- `schema_version`：已经执行的迁移版本。
- `tuning_runs`：一次自动调律的配置快照、状态和聚合计数。
- `tuning_equipment`：逐件初始/终态快照、顺序、槽位、逐轮详情和处理结论。
- `telemetry_deliveries`：逐件匿名统计的资格、事件 ID、版本、尝试和成功状态。

`tuning_equipment` 使用 `UNIQUE(run_id, sequence_id)`；所有历史详情查询明确
`ORDER BY sequence_id ASC`。

### 2.2 迁移

仓库沿用 Profile repository 的迁移模式：

- `MIGRATIONS = [(version, description, function), ...]`
- 初始化执行 `BEGIN IMMEDIATE`
- 按版本顺序执行并写入 `schema_version`
- 连接开启 WAL、外键和 5000ms busy timeout
- 每个仓库操作使用短生命周期连接

数据库版本不等于匿名事件版本。改变本地列使用数据库迁移；改变上传字段则
提升 `yysls.tuning_session` Schema 版本。

### 2.3 重置终态与识别异常

`tuning_equipment.reset_outcome` 存 `tuning_history.models` 里的 `RESET_*` 码：

| 码 | 含义 | 性质 |
|----|------|------|
| `completed` | 重置成功 | 结果 |
| `cooldown` | 冷却期，本次未重置 | 结果 |
| `exhausted` / `exhausted_recycled` | 次数用尽，按配置转处置 | 结果 |
| `material_shortage` | 传律石不够 | 结果 |
| `failed` | 重置过程中的检查失败 | 结果 |
| `count_unreadable` | **无法识别重置次数** | **异常** |

异常码集中在 `RESET_ANOMALIES`（frozenset）。卡片、历史列表和聚合统计都从这个
集合取值，SQL 占位符按集合大小生成，新增异常态只改这一处。

重置二次确认弹不出来时走 `pause_user()` 而不是记异常直接跳过：那通常是账号
安全锁，需要人工解锁，程序自己取消反而会把界面留在不确定状态。人工处理后复查
一次仍失败才记 `failed` 并跳过。

`TuningResetter.try_reset_tune` 的跳过分支返回 `(outcome, message)` 而不是裸
字符串：调用方直接透传 outcome，不再从中文原因串反推档位——那些串都过了
`tr()`，英文界面下必然认错档。`False` 严格保留给"确定且永久的重置不可用"
（见[自动调律 §6.5](../20-requirements/01-auto-tuning.md#65-重置处理reset-动作的执行)）。

每次运行的异常件数由 `list_runs()` / `get_run()` 的子查询实时聚合，**没有**
落到 `tuning_runs` 的列上，因此不需要迁移。更重要的是 `finish_run` 只在正常
收尾时写聚合计数，崩在半路的运行计数恒为 0——而那正是最该看到异常的运行。

## 3. 实时与历史 UI

`TuningManagementWidget` 包含：

- `TuningProgressWidget`：当前任务；
- `TuningHistoryWidget`：历史汇总和运行列表。

历史详情从 repository 读取 `TuningEquipmentResult`，装入静态
`TuningResultStore` 后打开现有 `TuningResultsDialog`。因此实时与历史共享
槽位导航、结果筛选、搜索、2～4 列卡片、详情面板和顺序规则。

历史列表在「处理结果」和「总轮次」之间有「异常」列：非零标红并带 tooltip，
为零留空。顶部统计条同样带异常总数。装备卡片上异常显示为红色的
「异常：无法识别重置次数」，与琥珀色的「重置结果：…」区分。

删除以 `tuning_runs.run_id` 为边界。repository 删除任务主行，SQLite 外键
级联清理 `tuning_equipment` 与 `telemetry_deliveries`；UI 必须先确认，并在
成功后关闭可能仍展示该任务的详情窗口。该操作只影响本地数据，不调用服务端
删除接口。

## 4. 七天匿名补传

`TuningHistoryTelemetrySource` 是通用 `TelemetrySource` 的插件实现：

1. 将早于当前 UTC 时间 7 天的 `unreported` 行标为 `expired`；
2. 按 `eligible_at, equipment_id` 读取仍在窗口内的记录；
3. 从完整本地记录显式挑选允许字段；
4. 词条名、食物、部位、武器、评级等经过 `vocab` 白名单规范化；
5. 通过 `TUNING_SESSION_SCHEMA` 后形成最多 50 条一批的 `SourceBatch`；
6. 网络成功后使用本地 receipts 回写 `reported_at`。

`receipts` 不进入 HTTP 信封。上传事件携带随机、稳定的 `event_id`，为以后
服务端逐事件幂等预留；当前重试仍由稳定的 `batch_id` 在服务端去重。不上传
本地装备主键、运行 ID、处理顺序、用户名、装备名、路径或完整本地对象。

统计开关关闭时，通用 reporter 不查询任何数据源。重新开启后，设置页调用
现有启动期上报入口，读取最近七天未上报数据。离线或失败不会修改
`reported_at`。

## 5. Markdown

Markdown 是人类可读投影，不承担数据库、恢复或统计队列职责。旧文档中的
`TUNING_DATA_JSON` 可继续留在旧文件里，但新文档不再生成该隐藏块。

## 6. 扩展原则

- 新的历史字段通过数据库迁移添加，禁止在启动时临时 `ALTER TABLE`。
- 新的统计来源实现 `TelemetrySource` 并注册，不修改通用 reporter 的领域逻辑。
- 新上传字段必须同步更新 Schema 字段快照与隐私披露。
- 任何统计投影失败只能拒绝该事件，不得影响历史数据或自动调律。
