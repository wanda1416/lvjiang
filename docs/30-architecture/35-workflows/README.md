# 流程编排（Workflows）

> 最后更新：2026-11-10（基线 v0.15.12）。本层描述如与代码或配置不符，以代码与配置为准。

流程编排描述如何组合多个场景来完成一个完整的业务目标。

## 与场景的关系

- **场景**（`docs/34-scene/01-scene-layout-definition.md`）：定义单个游戏界面的逻辑结构（Scene / Area / Point / Region）
- **场景实现**（`docs/31-models/02-scene-implementations.md`）：各场景的具体字段定义
- **流程**：编排多个场景的执行顺序，完成复杂任务

## 已有流程

| 编号 | 文件 | 主题 | 依赖场景 | 插件 |
|------|------|------|----------|------|
| 01 | [01-current-equip-analysis.md](01-current-equip-analysis.md) | 用户当前装备分析 | bag_equip_detail, equip_weapon_detail, equip_armor_detail | yysls |
| 02 | [02-auto-tuning.md](02-auto-tuning.md) | 自动调律（背包滚动遍历） | bag_equip_detail, equip_weapon_detail, equip_armor_detail | yysls |

> 注：以上流程均为燕云十六声插件（`src/lvjiang/apps/yysls/`）专属工作流。通用 DSL 语法见 [32-grammar](../32-grammar/README.md)。

## 系统脚本库分层（`config/system/workflows/`）

系统脚本不是一堆平铺的「每日任务」，而是一个带内部依赖的小类库。分层比文件名重要：
`workflows/` 一棵树里混着三类东西，注册与否只由文件头声明决定（见
[07.1-metadata.md](../32-grammar/07.1-metadata.md)），**目录不表达语义**。

| 层 | 位置 | 进入日常下拉 | 职责 |
|---|---|---|---|
| 可运行脚本 | 顶层与功能分组子目录（`gather/`、`stake/`、`standalone/` 等） | 是（`runnable: true`） | 一段用户能自己点开跑完的完整业务流程 |
| 内部过程库 | `subcall/`（含 `loadout/`、`record/`、`fengsha/` 子目录）、`lib/` | 否 | 被上面的脚本 `import` 后调用，自身没有入口 |
| 批量生命周期钩子 | `batch/` | 否 | 批次/条目准备、收尾与异常恢复，由调度器按批量配置调用 |

功能分组子目录（`gather/` 刷怪、`stake/` 木桩、`standalone/` 独立活动）只用于归类，
既不进默认 `id`，也不改变可见性——把脚本挪进或挪出子目录不会改变它的任何行为。

### 过程库的职责边界

`subcall/` 按「能安全复用的最小语义」切分，不按业务切分。各文件头部注释是权威清单，
这里只记边界：

| 文件 | 职责 | 关键约束 |
|---|---|---|
| `page_detection.wf` | 判断当前在哪个页面 | 纯判定：不导航、不点击，读不到就是「不在」，不抛异常、不重试 |
| `navigation.wf` | 页面之间的原子导航 | 返回 `>=0` 成功、`<0` 失败；页面判定依赖 `page_detection.wf` |
| `page_action.wf` | 跨业务重复的页面动作（确认/取消弹窗等） | 只如实返回是否命中，业务语义由调用方给出；点按钮还是按键由布局决定 |
| `visual_state.wf` | 用区域像素的通道关系判断视觉状态（奖励已领/未领、装备锁定等） | 不依赖 OCR 文案；比例落在中间带时返回 `unknown`，把不确定交给调用方 |
| `login.wf` | 账号切换、角色选择、退出登录，以及登录页现场判定 | 与批量调度的 `$batch_state` 约定见下 |
| `game_profile.wf` | 周进度等档案字段的解析与同步 | 按本周期单调规则写入，识别回退时不覆盖已有数据 |
| `profile_registry.wf` | Profile 数据模型的唯一登记处 | 业务脚本只声明自己依赖的 key，不各自重复写 label/cap/周期 |
| `result_records.wf` | 任务结果摘要 | 调用方 `global $record_result` 后由 `collect` 交回引擎；只写结果与日志，不碰游戏、不写配置 |

`lib/` 只收纳不依赖页面或用户资料的纯逻辑过程（集合去重与分块、排序与二分查找、
整数运算），可被任何脚本安全复用，边界见
[07-subworkflows.md](../32-grammar/07-subworkflows.md)。

`subcall/loadout/` 收纳备战方案相关的一组过程：装备详情统一扫描、指定方案的装备扫描、
游戏内备战方案导航、指定方案的基础属性扫描。`subcall/record/` 收纳录制产出的固定路线
——纯按键时间线，不做页面判定与到位校验，起点或地图一变即失效，改动前先确认它还是不是
唯一可用的走法。

### 平台差异的两种既有写法

同一个业务目标在两端动作不同时，现有脚本只有两种做法，不要再发明第三种：

- **条目级环境守卫**：一份脚本内用 `env:"desktop" -> …` / `env:"android" -> …` 只分叉
  真正不同的那一步，其余动作共享（刷怪采集类多用这种）。
- **双端过程文件**：入口脚本同时 `import` 两端的平台过程，再按 `env(...)` 分派
  （`subcall/fengsha/` 即此形态）。过程文件只放 `def`，不放顶层语句。

两端差异不总是「同一个动作换绑定」：桌面 `press` 方向键与设备端推摇杆是两个不同动作，
`drag` 在桌面也不会被转换成按键。想当然地复用一端的写法，是本类脚本最常见的失效原因。

### 批量钩子的接入点

`batch/` 下四个文件对应调度器的四个阶段，行为规范写在
[批量任务](../../60-userguide/03.04-batch-tasks.md)，`batch_check` / `batch_unit_prepare`
的元数据语义见 [07.1-metadata.md](../32-grammar/07.1-metadata.md)：

| 文件 | 阶段 | 说明 |
|---|---|---|
| `prepare_item.wf` | 条目准备 | 从登录页进入该用户的角色，启动时校验 `account` / `role` / `role_index` |
| `prepare_item_by_attr.wf` | 属性单元条目准备 | 声明 `batch_unit_prepare`，返回值须给出选定的 `username` |
| `finish_item.wf` | 条目收尾 | 退出并回到登录页，可选结束时直接停止应用 |
| `recover_to_login.wf` | 异常恢复 | 仅无人值守模式：任务撞上异常暂停后把游戏收回登录主页 |

条目准备与收尾通过共享的 `$batch_state` 交接现场：`login.wf` 就地更新它的
`account` / `role` / `page_state`，并以 `{"status", "message", "state"}` 结果字典汇报；
纯页面判定过程仍只返回 `1` / `0`，不起草结果字典。调度器不解析 `batch_state` 里的业务
字段，只按 `status` 决定后续阶段——所以游戏现场只能由钩子自己解释，不要让控制层去猜。

### 配置层、更新与枚举

- `local` 下同路径文件整份替换 `system` 版本，`import` 也按层解析（local 优先）。因此
  覆盖一个 `subcall` 会影响所有调用它的脚本，包括系统脚本——覆盖面远大于「改一个文件」。
- `.wf` 不参与 `content_version`：系统脚本随安装包与在线更新整份替换，过程库与钩子同理。
- 文件清单不要在本层维护，会漂移。全量以命令行 `--list` 与脚本编辑器的脚本树为准；
  本层只维护分层与契约。

## 流程文档模板

```markdown
# 流程：<名称>

## 目标
<流程要完成什么>

## 依赖场景
<需要哪些场景的区域定义>

## 执行步骤
<具体步骤，包括点击、截图、识别、判断>

## 输出
<流程产出什么数据或结果>
```

## 遍历与决策契约

- [03-bag-traversal.md](03-bag-traversal.md)：背包遍历、指纹引用、去重与回收补位。
- [04-tuning-decisions.md](04-tuning-decisions.md)：扫描、材料、调律、重置与回收的实际处理链。

## 任务入口与参数编辑

- [05-execution-feedback.md](05-execution-feedback.md)：任务入口的反馈归属、启动失败提示与参数保存边界。
  需求见[任务入口状态与启动反馈](../../20-requirements/60-editor/11-execution-feedback.md)（仅源码检出）和
  [日常参数保存边界](../../20-requirements/60-editor/12-parameter-save-boundaries.md)（仅源码检出）。
