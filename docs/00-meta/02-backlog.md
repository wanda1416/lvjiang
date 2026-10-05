# TODO

> 已完成/废弃条目定期清出本文件，历史见 git 与 docs/40-development/。
> 开启新对话时先读本文件。
> 下方标为“历史”的进度只记录当时的状态，不能作为当前功能清单。
> 当前发布功能与升级边界见
> [v0.13.11 发布说明](../50-releases/v0.13.11.md)。

## 当前基线（2026-10-06）

- 正式版本为 **0.13.11**；`dev` 在此基础上还有开发期修正，不能把它们写成
  已发布功能。
- 批量配置以可见用户限定范围，执行时可按用户名或用户属性聚合调度单元；
  主页面再勾选本次执行单元。见[批量执行单元](../20-requirements/30-batch/02-batch-execution-units.md)。
- 装备分析已支持 115 阶赛季、按等级与版本选择毕业率模型、模拟转律和装备
  假设视图；智能调律按用户与基础规则组启用。见
  [南吕赛季需求](../20-requirements/10-tuning/05-nanlu-season.md)和
  [智能调律需求](../20-requirements/10-tuning/03-smart-tuning.md)。
- 本页「功能待办」列出仍未完成的方向；具体实施范围以最新代码及各需求文档
  的未完成项为准。下面的 v0.10.4 进度不再用于判断是否已实现。

---

## 功能待办

1. **OCR 三态语义分层**：`to_equipment` 的两条失败路径（输入为空、解析异常）
   **都返回 `{}`**，于是「OCR 失败但有装备」与「空槽位」无法区分：后者应正常
   终止扫描，前者应记 error 并继续下一件。v0.9.0 已在装备等级/类型这一层做了
   部分区分（两者均无法确定按空槽，能定等级但类型未知记异常并跳过），但
   `to_equipment` 这一层仍未拆，判空信号也仍来自解析结果而非 scan 层。需拆为三层：
   - 层1 可解析：字段提取成功，有明确语义 → 正常处理
   - 层2 不可解析但存在数据：有内容但无法提取语义 → 记 error，跳过当前件，继续下一件
   - 层3 无法解析：完全空/无内容 → 视为空槽位，正常终止扫描
   涉及 `scan_equipped.wf` / `scan_unequipped.wf` / `subcall/loadout/equipment_scan.wf` /
   `auto_tuning.py` 门控逻辑。
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
5. **心力 / 体力管理**：零实现。`docs/20-requirements/40-profile/03-stamina-management.md`
   的 26 项待办全部未开始（代码里的 `stamina_*` 是战斗耐力属性，与本需求无关）。
   依赖定时轮询与多账号批量，工程量比看上去大。
6. **局内地图目标闭环导航与撤离**：为渡尘墟、觉障林及后续同类玩法提供
   可复用的玩家/目标地图定位、目标锁定、短脉冲移动、重新定位、防卡和到达交互能力。
   导航核心不硬编码玩法名称，不直接关闭应用；具体机制、安全边界与分阶段计划见
   [`07-in-match-navigation.md`](../20-requirements/50-platform/02-in-match-navigation.md)。

较早的分级与平台上机清单已并入本文件末尾的「待办明细」一节（根目录
[TODO.md](../../TODO.md) 现在只保留一个跳转）
（基线 v0.10.4）；其中的版本号和完成状态不能直接当作当前结论。

---

## 历史状态（2026-09-05，基线 `master` = v0.10.4）

- 主线功能全部就绪：自动调律端到端流水线可用，并已扩展为**结构化可持久化**
  （`tuning_history.db` 增量落盘 + 本次装备总览 + 历史任务管理）。
- 背包批量扫描已落地（`scan_unequipped.wf`，含 slots/min_level 参数与窗口游标去重）。
- 配置四层就位：local > remote（`content_version` 严格更高才生效）> system，
  外加随包分发的 `app.yaml`；`content_version` 由开发者显式提升。
- 匿名统计与调律数据回收上线（客户端 + Cloudflare Workers/D1 + 本机 stats-client 控制台）。
- 连接方案（plans）：图库/环境/布局/连接模式绑成具名整体，随包预置手游/端游/手游投屏三套。
- DSL：CoordRef 坐标体系、click/drag 时序、单行环境分支、全局变量、`scroll interval`、
  实参位字面量常数；`import` 已改根相对并封闭沙盒。
- i18n 框架 + 一致性门禁（key 对称、同一句中文不得多译法）。

---

## 历史版本回顾（2026-08-25 ~ 09-05）

逐日细节见 [docs/40-development/2026-08/](../40-development/2026-08/) 与
[2026-09/](../40-development/2026-09/)，此处只列版本主线：

- **v0.6.0**（08-25）匿名使用统计与调律数据回收从 0 到 1；宏录制补完；图库空间目录化；技能轴查看器。
- **v0.7.0 / v0.7.1**（08-26~27）ui/ 按业务域重构与 `main_window` 拆分；遥测事件粒度改为「一件装备一条」；stats-client 本地统计控制台；最小化到系统托盘。
- **v0.8.0**（08-28）`config/remote` 在线下发 + `content_version` 仲裁；i18n 遮蔽事故修复与门禁；执行前静态校验只查可达过程；写盘纪律（画布死区、OCR 显式保存）；批量可见顺序即执行顺序。
- **v0.9.0**（08-30）可复用引用场景与三段式访问；`import` 根相对与沙盒封闭；后台模式不再抢焦点；工作流文件树；Profile 升格为主引擎模块；江湖号令 PC 端可用。
- **v0.10.0**（09-02）调律历史结构化持久化；场景 schema v2 与页面跳转契约；武学/流派/玩法三层拆分；用户头像与执行用户；自动调律安全性重做。
- **v0.10.1**（09-03）机器级连接方案；Profile 快捷规则可视化；五维换算系数修正。
- **v0.10.2**（09-04）装备冷却识别与到期时间；DSL 全局变量；通用确认弹窗联合判定。
- **v0.10.3 / v0.10.4**（09-05~06）批量 PC 端账号切换与登录页重整；`scroll interval` 与实参字面量；布局孤儿坐标清理；PC 后台兼容调律滚轮策略。

---

## 历史记录（2026-08-15 ~ 16）

- **DSL click/drag 时序增强**：parser 支持 wait_clause（before/after/around）
  任意组合；suppress_defaults 抑制引擎默认延迟；click_screen/drag_screen
  扩展 pre_delay/post_delay 参数；全链路 **kw 透传。
- **DSL 泛化元组语法**：wait_range/range_literal 支持数字/变量混合
  （`($a, $b)`、`(1, $b)`）；TupleLiteral AST 节点 + __tuple__ 引擎处理；
  _exec_wait 增加未定义变量错误保护。
- **DSL clock/datetime 函数**：clock() 返回 Unix 时间戳；datetime() 支持
  当前时间/自定义格式/时间戳格式化四种调用形式。
- **i18n 国际化框架**：核心模块（i18n/__init__.py）+ 翻译文件
  （zh_CN.yaml 1639 行、en_US.yaml 1618 行）+ 25+ 文件 tr() 改造 +
  设置对话框语言选择 + 测试。

## 历史记录（2026-08-15）

- **DSL CoordRef 坐标统一体系**：新建 coord_types.py（CoordRef/RectCoordRef/
  CircleCoordRef/Offset 类型 + 向量运算规则）；AST SceneRef→EntityRef 全局重命名
  （13 文件，字段 region→entity）；布局模型 to_coord_ref()；引擎 _eval_arith
  支持 CoordRef 运算 + tuple 隐式转换；click/drag 解析更新。

## 历史记录（2026-08-02）

- **场景编辑器增量保存**：per-scene dirty 追踪，修改单个场景只写该场景 JSON，
  Tab 标题绿点指示变更，discard 提示列出变更场景名称。
- **画布保存修复**：保存时从当前激活 Tab 获取 canvas 配置，而非字典第一个 Tab。
- **画布尺寸实时刷新**：调整画布时顶部信息栏同步更新。
- **场景/分组重命名**：右键重命名场景或分组，联动 layouts 文件名、截图文件、
  scenes.yaml 配置。
- **批量 Tab 三页子 Tab 重构**：脚本配置、用户配置、条目概览分离。
- **关于对话框**：版本信息、GitHub Release 检查更新、版权信息。
- **打包版本注入**：package.bat 从 pyproject.toml 读取版本注入 _version.py。

## 最近完成（2026-07-31 ~ 08-01）

- **布局存储目录化**（`2856ce5`）：单文件 `默认布局.json`（2001 行）
  拆为 `layouts.yaml`（名册 + canvas 内联）+ `layouts/{布局名}/{场景}.json`
  （每场景独立实体文件），沿用 ConfigResolver 双层「分离写 / 合并读」语义；
  Android `syncSystemConfig` 整目录同步自动适配。
- **重置调律语义修正**（07-31 `9efc158` → 08-01 `4a26c12`）：冷却期 OCR
  三态检查（硬限单件一次）、已满装备走 scan 规则处理、品阶选项统一、
  非首外功攻击改硬门槛（缺大外/小外能用封顶 `1eb5f3f`）。
- **工作流引擎静态预检**（07-31 `219e7c0`）：新增 `validate_only` 预检 +
  系统工作流引用门禁与上机预检脚本，跑脚本前校验区域是否已在布局绑定。
- **Android 独立执行端**（07-30 ~ 08-01）：三通道 PoC 闭环、系统配置随 APK
  分发、设备端工作流引擎装配、原生调律参数配置页（`5d51117`）、悬浮层
  主题修复游戏不再被 LMK 击杀（`92fcb8e`）、release 实机复验通过。
- **打包分发**（07-31 `d415aa6`）：PyInstaller onedir 一键打包（launcher +
  spec + package.bat），内置 adb 随包分发（`fd45daf`），用户免装 platform-tools。
- **调律规则引擎演进**（07-31）：判定语义下沉到逐条处置规则（`282809f`）、
  顶档/普通条件结构演进 contains_all 组合条件（`934fbab`）、仅首词条逐规则化
  + 首词条方向比较（`54fb43e`）。
- **质量门禁**（07-31 `b5d1de0`）：接入 ruff + mypy，修掉全仓存量告警。
- **平台适配**（07-31 `309bb04`）：抽离 `core/platforms.py` 平台适配层；
  macOS 支持 Phase 0（依赖验证 + 退出崩溃修复 `2e72b30`）。

## 较早完成（2026-07-26 ~ 07-30）

- **调律规则开关化重构**（07-29 `6db9b32`）：`keep_pvp` 专用语义废弃，
  改为通用开关机制（tune_config.yaml `switches` 注册表 + 条件组 `when`
  前提）；评级四档定名 垃圾/一般/优秀/顶级（`Rating.USABLE`→`NORMAL`）；
  条件原语收敛为 4 个（contains_all/not_together/count_max/count_min，
  均支持 include_first）；规则级/部位级 `default_rating` 兜底。
- **属攻词条双重身份匹配**（07-30 `a2e3467`）：非武器部位具体属攻以
  字面名 + 动态词条（最大/最小本属攻击、最大/最小外属攻击）双重身份
  参与匹配；武器部位仍引用字面无相。
- **狗粮规则引擎重构**（07-30 `276b916`）：有序规则表替代品阶映射；
  配套调律材料策略配置与规则模型扩展（`c422323`）、材料区同名幽灵槽
  修复（`6f8ddee`）。
- **自动调律链路补全**（07-27 `d380546` 等）：背包遍历策略化
  （bag_traversal 包：dedup 滑动窗口去重为默认 / positional 位置对齐）、
  整行列遍历、指纹漂移容错、狗粮返还二次弹窗兼容、F10 停止输出部分
  结果、「跳过实际调律」测试开关。
- **调律说明文档**（07-28 `ca9bbbe` → 07-30 `cb734e9`）：叙事型 Markdown
  输出至 logs/tuning/，结尾含成品清单。
- **UI 插件化注入架构**（07-29 `30534d2`）：插件通过注入点挂载 UI；
  配套 loguru 落盘与崩溃防护安装位置修正（`3c1f4b5`）。
- **崩溃根治**（07-30）：pynput 退出竞态（`b754132`）、
  UnhandledExceptionFilter 回调防 GC（`48e9ab4`）。
- **三层术语模型重命名**（07-28 `ad37a0e`）：流派/玩法/调律规则全面统一；
  四个主体大文件拆为分层/Mixin 包（`5b5e3d3`）、DSL 引擎拆 engine/ 包
  （`29c2121`）。
- **CI**（07-28 `a75ecdd`）：GitHub Actions windows-latest + 离屏 pytest。
- **品阶门槛按部位锁死 + 规则级覆盖**（07-29 `92d5505`）。

---

## 特别注意事项（下一对话必读）

1. **严禁自动提交**；提交时中文信息须写入 UTF-8 文件后 `git commit -F`。
2. 运行环境：用**全局 python**（.venv 缺 yaml）；pytest 加 `-p no:cacheprovider`
   可避开沙箱对 .pytest_cache 的权限报错；PowerShell 写中文用 `python -X utf8`。
   注意：pytest -q 在本机不打印 "N passed" 汇总行，以进度 100% 且无 F/E 为准。
3. **SearchReplace 陷阱**：任何含 `───` U+2500 制表线的行做锚点必失败，
   用纯代码锚点或（文件 <1000 行时）Write 整文件重写。
4. **Qt 样式级联坑**：容器 QFrame 的 setStyleSheet 必须用 `QFrame#objectName` 选择器，
   否则级联到 QListWidget/QTableWidget（均继承 QFrame）抹掉其边框背景，看似"布局错乱"。
   排查布局问题先离屏 `widget.grab().save()` 截图确认实际渲染。
5. school_panel 信号重入：`_refresh_list` 中 `clear()` 会嵌套触发 `_on_school_changed`，
   后者用 `prev_loading` 保存/恢复 `_loading` 标志，改动时勿破坏此约定。
6. 用户约定：开发期配置重构**不写迁移兼容代码**（旧 schema 直接废弃）。
7. 提交推送遵循 /submit 一次性语义：完成后不自动 commit/push，等用户指令。

## 待办明细（原根目录 TODO.md）

下面是 2026-09-05 整理时的逐条待办与当时的进度快照，条目状态只对当天有效；已完成项已删去，最新进展看上面的「功能待办」。

> 本文档整合了源码注释、开发日志、阶段计划的所有待办事项，按优先级与模块组织。
> 最后整理日期：2026-09-05（基线：`master` = v0.10.4）
>
> 上一版整理于 2026-08-28，其中「安卓上机」一节引用的 `equip_analysis.wf` 早在
> 08-19 就已更名（`equip_scan` → `scan_equipped`、`equip_analysis` → `scan_unequipped`），
> 本次一并更正。

---

## 📊 总体进度

| 模块 | 阶段 | 状态 | 优先级 |
|------|------|------|--------|
| **安卓端** | Phase 4（打包发布） | 🚧 进行中（仅剩上机验证） | 🔴 高 |
| **macOS 支持** | Phase 1（ADB 模式）| 🔶 代码完成，真机未验 | 🟡 中 |
| **在线配置下发** | `config/remote` + `content_version` | ✅ 桌面端可用 | 🟡 中 |
| **匿名统计 / 数据回收** | 客户端 + Workers/D1 + 本地控制台 | ✅ 已上线 | — |
| **调律 / 任务历史** | SQLite 持久化 + 总览 + 筛选 | ✅ | — |
| **连接方案（plans）** | 机器级方案 + 随包预置三套 | ✅ | — |
| **脚本工作台** | 完成 + 打磨中 | ✅ | 🟢 低 |
| **图色原语** | 完成 + 真机验证待 | ✅ | 🔴 高 |
| **窗口模式** | PC 端 / 投屏窗口（含后台兼容） | ✅ 可用 | — |

---

## 🔴 P0 关键阻塞（当前）

当前无 P0 关键阻塞。

---

## 🟡 P1 高优先级

### 1. 设备端手势验证 {#p1-device-gesture}

**位置**: `docs/00-meta/archive/android-platform-progress.md`「设备端代理通道」§、
`docs/40-development/2026-08/2026-08-22-vision-primitives-hold-gesture.md§七·待办`

**内容**:
- 设备端推住摇杆持续移动验证（`drag … hold` 已在 Kotlin 修正，待机上跑）
- `press "ESC"` 退子界面验证
- 截图尺寸与输入坐标系是否一致（不同机型系统 UI 裁边）

**待办**:
- [ ] 真游戏上跑一条 `.wf`（推摇杆 hold 验证停住）——作者手机上做
- [ ] 验证 ESC 键映射到 BACK 的行为
- [ ] 核对坐标系偏差（截图分辨率 vs 触控坐标）
- [ ] 通过真机验证后更新设备端图色函数文档

**优先级**: 🟡 **高**（直接影响设备端可用性）

---

### 2. 屏幕标定收尾 {#p1-screen-calib}

**位置**: `docs/00-meta/archive/android-platform-progress.md`「屏幕标定（2026-08-23）」

代码与合成图验证均已通过（两地标自动定位、画布解算残差 0px、保存/恢复覆盖文件）。
缺的是真机产物与第二台机型：

- [ ] 作者手机生成正式参照图，提交 `config/system/layouts/android/_reference.png`
- [ ] 第二台不同挖孔/宽高比的真机上跑一遍「屏幕标定 → 跑 `.wf`」

**优先级**: 🟡 **高**（换机适配的唯一路径）

---

### 3. macOS ADB 模式真机验证 {#p1-mac-adb}

**位置**: `docs/00-meta/platforms/macos.md` Phase 1 · P1-g

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

**优先级**: 🟡 **高**（开启 macOS 支持）

---

### 4. 安卓端上机完整测试 {#p1-android-onsite}

**位置**: `docs/00-meta/archive/android-platform-progress.md` Phase 4

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

**优先级**: 🟡 **高**（设备端主功能完整性）

---

### 5. OCR 三态语义分层 {#p1-ocr-tristate}

**位置**: `src/lvjiang/apps/yysls/workflows/builtins/equipment.py`（`_to_equipment`）

当前 `to_equipment` 的两条失败路径**都 `return {}`**：

```python
if not isinstance(raw_data, dict) or not raw_data:   # 输入为空
    return {}
except Exception as e:                                # 解析失败
    return {}
```

于是「OCR 失败但有装备」与「空槽位」无法区分：后者应正常终止扫描，前者应记 error
并继续下一件。v0.9.0 已在**装备等级/类型**这一层做了部分区分（等级和类型均无法确定
按空槽处理，能确定等级但类型未知记异常并跳过），但 `to_equipment` 这一层的三态拆分
仍未做，判空信号也仍来自解析结果而非 scan 层。

**待办**:
- [ ] 层1 可解析：字段提取成功，有明确语义 → 正常处理
- [ ] 层2 不可解析但存在数据：有内容但无法提取语义 → 记 error，跳过当前件，继续下一件
- [ ] 层3 无法解析：完全空/无内容 → 视为空槽位，正常终止扫描
- [ ] 判空信号改由 scan 层（raw dict 全空/纯噪声）给出

**涉及**: `scan_equipped.wf` / `scan_unequipped.wf` / `subcall/loadout/equipment_scan.wf` /
`auto_tuning.py` 门控逻辑

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

调律/分析结束后的独立数据汇总视图（处理装备数、评级分布、材料消耗、狗粮投入），
与叙事型调律说明文档互补。

**现状**: 数据源齐备但视图缺席——`config/session/tuning_history.db` 已有完整结构化历史
（v0.10.0），实时总览与历史详情已可查看单次任务，`apps/yysls/ui/` 下**没有任何跨任务
的汇总模块**。

**待办**:
- [ ] 跨任务聚合视图（按时间段 / 按规则 / 按部位）
- [ ] 材料与狗粮消耗统计
- [ ] 评级分布与成功率趋势

**优先级**: 🟢 **中等**（数据现成，只差视图）

---

### 3. 转律 / 装上执行 {#p2-transmute-equip}

当前「转律」全仓只出现在 `auto_tuning.py` 与 `tuning/judge.py`，**纯用于评级模拟**
（judge 预测潜力、穷举可达上限），没有真实点击转律的工作流；毕业装备替换穿戴（装上）
亦未做。

**待办**:
- [ ] 真实转律工作流（场景 + 布局 + `.wf`）
- [ ] 毕业装备「装上」流程
- [ ] 与自动调律的衔接（判定为可转律后是否自动执行，需要开关与二次确认）

**优先级**: 🟢 **中等**（judge 已能预测，但用户仍需手动操作，链路是断的）

---

### 4. 货币追踪补完 {#p2-currency}

**位置**: `docs/20-requirements/40-profile/02-currency-tracking.md`

**已完成**（`scan_wallet.wf`，最难的识别部分）:
- ✅ 整页 OCR + 数字提取与清洗 + 货币名匹配
- ✅ 桌面 4×6 / 安卓 3×6 面板自适应，安卓端上拉补扫缺失货币
- ✅ 容差参数防误识别（通宝/么玉/宝钱/长鸣玉）
- ✅ 写入 Profile

**未完成**:
- [ ] `CurrencySnapshot` 历史留存（JSONL 追加写入）
- [ ] 数据清理（90 天过期）
- [ ] 趋势弹窗 + 折线图

**优先级**: 🟢 **中等**（识别已通，补历史与视图即可收口一个完整需求）

---

### 5. 心力 / 体力管理 {#p2-stamina}

**位置**: `docs/20-requirements/40-profile/03-stamina-management.md`

**现状**: **零实现**。需求文档写得最细，但代码里 `stamina_*` 全部是战斗耐力属性
（`attr_model/models.py` 的 `stamina_max` / `stamina_regen`），与本需求无关。

依赖定时轮询与多账号批量，工程量比看上去大。文档中 26 项待办全部未开始。

**优先级**: 🟢 **低**（排在货币追踪与统计报表之后）

---

### 6. macOS 窗口模式支持 {#p2-mac-window}

**位置**: `docs/00-meta/platforms/macos.md` Phase 2

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

**位置**: `docs/30-architecture/31-models/06-base-attr-model.md`§界面·名册从哪来

武学名册已经以 `game_config.yaml` 的 `martial_arts` 为准（属性配置那页
只是数值覆盖层，增删按钮已禁用）。名册在武学配置那边，数值理应也在
那边填，两处来回切没有道理。

**计划（后续）**:
- [ ] 「武学天赋」页并入武学配置，属性配置页面只留心法与其余来源

**优先级**: 🟢 **低**（开发阶段先不动，两处的迭代节奏还不一样）

---
