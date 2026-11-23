# TODO

> 状态：部分实现。待办清单，各条目状态以条目描述为准。
>
> 已完成/废弃条目定期清出本文件；2026-10-06 之前的历史段已迁到
> [archive/backlog-history-2026-10-06.md](../80-history/01-meta-archive/03-backlog-history-2026-10-06.md)。
> 本文件只留**仍然有效**的待办；当前版本与发布状态以
> [发布说明索引](../50-releases/README.md) 为准，不在此维护版本快照。
> 协作授权、执行环境与验证门禁见根目录 `AGENTS.md`，本文件不再重复。

---

## 功能待办

1. **OCR 三态语义分层**：`_to_equipment`（`apps/yysls/workflows/builtins/equipment.py`）
   的两条失败路径（输入为空、解析异常）**都返回 `{}`**，于是「OCR 失败但有装备」与
   「空槽位」无法区分：后者应正常终止扫描，前者应记 error 并继续下一件。v0.9.0 已在
   装备等级/类型这一层做了部分区分（两者均无法确定按空槽，能定等级但类型未知记异常并
   跳过），但 `_to_equipment` 这一层仍未拆，判空信号也仍来自解析结果而非 scan 层。
   需拆为三层：
   - 层1 可解析：字段提取成功，有明确语义 → 正常处理
   - 层2 不可解析但存在数据：有内容但无法提取语义 → 记 error，跳过当前件，继续下一件
   - 层3 无法解析：完全空/无内容 → 视为空槽位，正常终止扫描
   涉及 `scan_equipped.wf` / `scan_unequipped.wf` / `subcall/loadout/equipment_scan.wf` /
   `apps/yysls/workflows/implementations/auto_tuning.py` 门控逻辑。
2. **统计报表面板**：跨任务的数据汇总视图（处理装备数、评级分布、材料消耗、
   狗粮投入），与叙事型调律说明文档互补。数据源已在 v0.10.0 齐备
   （`config/session/tuning_history.db` 结构化历史 + 实时总览 + 历史详情），
   缺的是**跨任务聚合**这一层，`apps/yysls/ui/` 下目前没有任何相关模块。
3. **转律 / 装上执行**：目前已有转律建议、模拟转律和评级潜力计算，
   但建议结果不会自动进入游戏完成转律；毕业装备替换穿戴也仍需人工操作。
   若要实现执行链路，须先核对最新的转律资格和装备保护规则。
4. **货币追踪补完**：`scan_wallet.wf` 已完成识别侧（整页 OCR、数字清洗、
   货币名匹配、桌面 4×6 / 安卓 3×6 自适应与补扫、容差防误识、写入 Profile）；
   缺 `CurrencySnapshot` 历史留存（JSONL）、90 天过期清理与趋势折线图。
5. **心力 / 体力管理**：**部分实现**（需求文档状态 2026-10-09；律准石库存与记账策略
   已在 `apps/yysls/workflows/implementations/tuning/stone_stock.py`）。剩余项见
   `docs/20-plans/02-profile/03-stamina-management.md` 的未完成清单；
   依赖定时轮询与多账号批量，工程量比看上去大。
6. **局内地图目标闭环导航与撤离**：为渡尘墟、觉障林及后续同类玩法提供
   可复用的玩家/目标地图定位、目标锁定、短脉冲移动、重新定位、防卡和到达交互能力。
   导航核心不硬编码玩法名称，不直接关闭应用；具体机制、安全边界与分阶段计划见
   [`07-in-match-navigation.md`](03-platform/02-in-match-navigation.md)。

7. **AI 异常导航恢复**：规划，尚未实施；仅在已声明的安全检查点按需调用识图模型，
   程序验收后再继续，不授权购买、领奖或账本写入。范围、前置修正与分阶段验收见
   [AI 异常导航恢复与安全续跑方案](03-platform/29-ai-exception-recovery.md)；实施另行授权。

8. **识别帧历史调试与异常诊断包**：规划——可选本地保存最近最多 100 张识别帧，
   以编码总字节与有界背压控制开销，显式开启并关联任务状态，补充动作时间线与离线查看；
   主动反馈自动准备对应日志/包，提交才发送。实施与验收见
   [识别帧历史调试与异常诊断包方案](03-platform/30-debug-frame-history.md)；与AI恢复独立，诊断/反馈后续实施另行授权。
   另一条已分流的链路不在此条内：全任务采集与离线WF模拟的首版实现见
   [设计](../30-design/01-architecture/09-workflows/06-workflow-simulation.md)，
   其未实装的门禁增强见[计划](03-platform/31-wf-simulation-hardening.md)。

> 上面 8 条是当前唯一清单；下方 P1/P2 是同一批事项的详情，重复项只留一处指针。

---

## 历史（截至 2026-10-06）

2026-10-06 之前的进度快照、历史记录与完成清单已迁到
[archive/backlog-history-2026-10-06.md](../80-history/01-meta-archive/03-backlog-history-2026-10-06.md)，只作追溯；
本文件不再保留历史段。

---

## 待办明细（P1 / P2 详情）

> 逐条详情按优先级组织，状态以「功能待办」为准；勾选框只表示该条内部子步骤，
> 带「外部依赖」的条目要真机或作者本人操作，AI 不可推进。

---

## 📊 总体进度（核对于 2026-11-17）

| 模块 | 阶段 | 状态 | 优先级 |
|------|------|------|--------|
| **安卓端** | 手机端独立执行 + 离线配置 | ✅ 可用（真机全流程回归待做） | 🟡 中 |
| **macOS 支持** | Phase 1（ADB 模式）| 🔶 代码完成，真机未验（外部依赖） | 🟡 中 |
| **在线配置下发** | `config/remote` + `content_version` | ✅ 桌面端可用 | 🟡 中 |
| **匿名统计 / 数据回收** | 客户端 + Workers/D1 + 本地控制台 | ✅ 已上线 | — |
| **调律 / 任务历史** | SQLite 持久化 + 总览 + 筛选 | ✅ | — |
| **连接方案（plans）** | 机器级方案 + 随包预置三套 | ✅ | — |
| **脚本工作台** | 完成 + 打磨中 | ✅ | 🟢 低 |
| **图色原语** | 完成 + 真机验证待（外部依赖） | ✅ | 🟡 中 |
| **窗口模式** | PC 端 / 投屏窗口（含后台兼容） | ✅ 可用 | — |
| **键位分区** | 菜单 F1~F7 / 全局热键 F8~F12 可配 | ✅ | — |

---

## 🔴 P0 关键阻塞（当前）

当前无 P0 关键阻塞。

---

## 🟡 P1 高优先级

### 1. 设备端手势验证 {#p1-device-gesture}

**位置**: `docs/80-history/01-meta-archive/01-android-platform-progress.md`「设备端代理通道」§、
`docs/80-history/02-dev/2026-08/2026-08-22-vision-primitives-hold-gesture.md§七·待办`

**内容**:
- 设备端推住摇杆持续移动验证（`drag … hold` 已在 Kotlin 修正，待机上跑）
- `press "ESC"` 退子界面验证
- 截图尺寸与输入坐标系是否一致（不同机型系统 UI 裁边）

**待办**:
- [ ] 真游戏上跑一条 `.wf`（推摇杆 hold 验证停住）——作者手机上做
- [ ] 验证 ESC 键映射到 BACK 的行为
- [ ] 核对坐标系偏差（截图分辨率 vs 触控坐标）
- [ ] 通过真机验证后更新设备端图色函数文档

**优先级**: 🟡 **高**（直接影响设备端可用性）｜**外部依赖**：需真机或作者本人操作，AI 不可推进

---

### 2. 屏幕标定收尾 {#p1-screen-calib}

**位置**: `docs/80-history/01-meta-archive/01-android-platform-progress.md`「屏幕标定（2026-08-23）」

代码与合成图验证均已通过（两地标自动定位、画布解算残差 0px、保存/恢复覆盖文件）。
缺的是真机产物与第二台机型：

- [ ] 作者手机生成正式参照图，提交 `config/system/layouts/android/_reference.png`
- [ ] 第二台不同挖孔/宽高比的真机上跑一遍「屏幕标定 → 跑 `.wf`」

**优先级**: 🟡 **高**（换机适配的唯一路径）｜**外部依赖**：需真机或作者本人操作，AI 不可推进

---

### 3. macOS ADB 模式真机验证 {#p1-mac-adb}

**位置**: `docs/00-meta/01-platforms/01-macos.md` Phase 1 · P1-g

**状态**:
- ✅ P0-a ~ P0-d：依赖矩阵 + pytest 全绿（macOS 12.7.6 验证）
- ✅ P1-a ~ P1-f：平台门控 + 适配层重构完成
- ✅ macOS 侧已能出 Android 包（`a1d359b5`，Gradle 8.10.2 wrapper + buildPython 按平台解析）
- ⏳ P1-g：真机验证待启动

**内容**: 连接 Android 设备 → scrcpy 流截图 → OCR → 跑一条完整调律工作流

**验证清单**:
- [ ] macOS 实机上使用 scrcpy 连接手机
- [ ] 流截图性能测试（期望可接受的帧率）
- [ ] OCR 完整链路测试
- [ ] 运行完整的调律工作流（`scan_unequipped.wf` + 自动调律）
- [ ] 记录实测依赖版本矩阵差异（若有）

**优先级**: 🟡 **高**（开启 macOS 支持）｜**外部依赖**：需真机或作者本人操作，AI 不可推进

---

### 4. 安卓端上机完整测试 {#p1-android-onsite}

**位置**: `docs/80-history/01-meta-archive/01-android-platform-progress.md` Phase 4

**状态**:
- ✅ 参数 UI 路线 B 实现、部位选择与 LMK 杀进程两个 bug 已修
- ✅ 江湖号令实机跑通（2026-07-31）
- ✅ `versionCode` 已到 41（v0.10.4）
- ⏳ 剩余业务流上机验证

**当前待办**（Phase 4 进行中）:
- [ ] 透明配置页下游戏存活测试
- [ ] 只调勾选部位功能验证
- [ ] 装备扫描（`scan_equipped` / `scan_unequipped`）首跑测试
- [ ] 悬浮面板 UI 长时间稳定性测试

**待办的业务工作流上机**:
- [ ] 江湖号令（已完成初步验证，需完整流程覆盖）
- [ ] 自动购买心法 / 自动购买不肝
- [ ] 百业货运、炸鱼、签到
- [ ] 盟主争锋（当前在 `archived/`）

**优先级**: 🟡 **高**（设备端主功能完整性）｜**外部依赖**：需真机或作者本人操作，AI 不可推进

---

### 5. OCR 三态语义分层 {#p1-ocr-tristate}

详见「功能待办 1」。实现要点：`_to_equipment`
（`apps/yysls/workflows/builtins/equipment.py`）两条失败路径仍都 `return {}`，
需拆成三层并把判空信号移到 scan 层。

**优先级**: 🟡 **高**（当前会把 OCR 失败误当背包到底，静默提前结束扫描）

---

## 🟢 P2 计划阶段

### 1. 装备词条硬编码提取 {#p2-equipment-rules}

**位置**: `src/lvjiang/apps/yysls/workflows/builtins/equipment.py`（`_HIGH_VALUE_KEYWORDS`）

**内容**: 全仓唯一一处真 `TODO` 标记。

```python
# TODO: 从调律规则配置中读取，当前为硬编码
```

**设计**:
- [ ] 在 `config/system/yysls/` 中新建词条索引配置文件（YAML 或 JSON）
- [ ] 通过 ConfigResolver 加载（与场景/布局同套机制），并注册 `content_version` 参与在线下发
- [ ] 设备端 + PC 端都能读取
- [ ] 支持本地覆盖（用户可在 `config/local/yysls/` 中自定义）

**优先级**: 🟢 **中等**（代码健壮性而非功能性）

---

### 2. 统计报表面板 {#p2-stats-panel}

详见「功能待办 2」。数据源（`config/session/tuning_history.db`）齐备，
缺跨任务聚合视图；`apps/yysls/ui/` 下仍无相关模块。

**优先级**: 🟢 **中等**（数据现成，只差视图）

---

### 3. 转律 / 装上执行 {#p2-transmute-equip}

详见「功能待办 3」。judge 已能预测潜力，缺真实点击转律与「装上」链路。

**优先级**: 🟢 **中等**（链路是断的）

---

### 4. 货币追踪补完 {#p2-currency}

详见「功能待办 4」。识别侧已完成；缺 `CurrencySnapshot` JSONL 留存、
90 天过期清理与趋势折线图。

**优先级**: 🟢 **中等**

---

### 5. 心力 / 体力管理 {#p2-stamina}

详见「功能待办 5」。**部分实现**（需求文档 2026-10-09 状态；
`apps/yysls/workflows/implementations/tuning/stone_stock.py` 已有律准石库存与
记账策略），剩余项见需求文档清单。

**优先级**: 🟢 **低**

---

### 6. macOS 窗口模式支持 {#p2-mac-window}

**位置**: `docs/00-meta/01-platforms/01-macos.md` Phase 2

**范围**: 8~10 天工作量，可无限期后置

- [ ] P2-a：`core/desktop/` 拆分为 `desktop/windows/` + `desktop/macos/` 子包
- [ ] P2-b：macOS 输入后端（Quartz `CGEvent`，pyobjc-framework-Quartz）
- [ ] P2-c：macOS 窗口枚举 & 定位（`CGWindowListCopyWindowInfo`）
- [ ] P2-d：Retina 坐标换算（物理像素 ÷ backingScaleFactor）
- [ ] P2-e：辅助功能权限引导（`CGEventPost` 需用户授权）
- [ ] P2-f：mac 真机联调

**依赖**: P1-g macOS ADB 验证完成

**优先级**: 🟢 **低**（ADB 模式优先级更高）

---

### 7. 脚本工作台二次迭代 {#p2-workbench-v2}

**已完成**: 文本编辑 + 语法高亮、可视化调试面板、快捷指令式「选操作」、
配置分层合并后的工作流文件树（`f6bb661e`，v0.9.0）

**已知小瑕疵**（非阻塞）:
- 块语句（`loop`/`if`）头节点单步停在块内第一行（既有文法缺陷）
- 参数化槽位还不够丰富（设备端 `auto_tuning` 的复杂参数无法在 UI 配置）

**计划**:
- [ ] 块语句头定位修正（较大改动，单独规划）
- [ ] 参数 schema 扩展（支持多选、联动下拉）——`bool` / `checkgroup` 已实现并有文档
- [ ] 调试面板性能优化（变量表超大时卡顿）

**优先级**: 🟢 **低**（已满足日常使用需求）

---

### 8. stats-client 阶段二 / 三 {#p2-stats-client}

**位置**: `ops/stats-client/README.md`§「已知的第一阶段范围（MVP）」

MVP 已可用（总览 / 调律分析 / 数据同步）。明确不在第一阶段的：

- [ ] 交互式筛选联动
- [ ] 报告快照与前后对比
- [ ] 结构化「结论卡」（带可信度）

**优先级**: 🟢 **低**（当前是唯一能看聚合遥测的口子，但手工流程已被替代）

---

### 9. 日常任务统一编排 {#p2-daily-orchestrator}

签到、江湖号令、百业货运、炸鱼、补肝商店、心法购买、兑换码等脚本已各自成型，
批量框架（`batch/prepare_item.wf` + 四阶段生命周期）也在，但没有「一键按序跑完
当日全部日常并汇总结果」的编排入口。

**待办**:
- [ ] 日常任务集合定义（按用户/角色）
- [ ] 顺序执行 + 失败继续 + 汇总报告
- [ ] 与任务历史（v0.10.0）打通

**优先级**: 🟢 **低**（属于新方向，不在既有需求文档内）

---

### 10. 「武学天赋」并进武学配置 {#p2-martial-art-attrs-merge}

**位置**: `docs/30-design/01-architecture/models/06-base-attr-model.md`§界面·名册从哪来

武学名册已经以 `game_config.yaml` 的 `martial_arts` 为准（属性配置那页
只是数值覆盖层，增删按钮已禁用）。名册在武学配置那边，数值理应也在
那边填，两处来回切没有道理。

**计划（后续）**:
- [ ] 「武学天赋」页并入武学配置，属性配置页面只留心法与其余来源

**优先级**: 🟢 **低**（开发阶段先不动，两处的迭代节奏还不一样）

---

> P2 #11（profile 记账判定三份重复）与 #12（meta 锁 SQL 三处维护）已于
> 2026-11-15 收口（`e4f7de37`、`a271849d`、`3c9ea1fc`），按本文件约定清出；
> 细节见 git 历史与 `docs/80-history/02-dev/`。
