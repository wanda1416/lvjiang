# 预置基础属性分层与毕业率多阶段模型

> 状态：已实现（涵盖 PC 配置层、流派配置双子页/三栏编辑、方案默认属性绑定、备战方案双源展示、Web 资产导出与无扫描反推试算；系统随包内置 12 个流派的 `115_s2`（115 级 2 阶段）预置，115 级上半赛季 `s1` 已结束、不再补录 `s1`）。

## 1. 背景与设计目标

此前角色基础属性仅存储在 `config/session/yysls/play_styles.json`（历史命名，实际保存由实测面板减去装备与弓玦反推得到的纯角色基础属性），存在两类结构性限制：

1. **Web 端强依赖 PC 同步**：未在 PC 端完成扫描并同步 `play_styles` 文档的用户，在网页端填写装备后无法计算毕业率；
2. **跨服（国际版）与同等级多阶段难以表达**：同一装备等级（如 115 级）在不同养成阶段的基础属性与毕业率方案并不相同，而同一阶段内社区计算器表格又会迭代多个修订版（`v1/v2/v4/v6`），不能用 `vN` 混指阶段；同时国际版落后国服多个赛季，选型逻辑不能绑定国服赛季日历。

本设计在**不改名 `session/yysls/play_styles.json`、不改动云同步 `kind="play_styles"` 与 `loadouts` Schema** 的前提下，引入 `system/local` 分层预置基础属性与通用阶段维度 `stage`，打通 PC 与 Web 两条路径。

## 2. 统一四维模型（与赛季日历解耦）

| 维度 | 字段 | 类型 / 示例 | 默认解析规则 |
| --- | --- | --- | --- |
| 等级 | `level` | `int`（`100` / `110` / `115`） | 未指定时自动取已配置数据的最高 `level`；指定个人世界等级时取不超过该等级的最高 `level` |
| 阶段 | `stage` | `int`（`1` / `2` / `3`…） | 同一等级内的养成/版本阶段（不限两段）；同等级默认取最高 `stage`，存量缺省视为 `1` |
| 归属 | `school` + `playstyle` | 流派 + 可选玩法 | 选玩法后优先匹配 `playstyle` 专属条目，缺省回落同 `school` 默认条目（`playstyle: null`） |
| 表格修订版 | `version`（仅 `graduation`） | `int`（`v1` / `v2` / `v4`…） | 同一 `(level, stage)` 内自动取最高 `version` |

## 3. 存储分层与 Schema 契约

### 3.1 预置基础属性（`yysls/base_attributes/*.yaml`）

- **分层位置**：
  - 随包系统预置：`config/system/yysls/base_attributes/{流派}.yaml`
  - 本地用户覆盖：`config/local/yysls/base_attributes/{流派}.yaml`
- **版本化与合并**：在 `apps/yysls/config/merge_policy.py` 注册为 `depth=1` 的版本化实体目录（带 `content_version`，参与在线配置下发）。读取时按 `preset.id` 做 `system ← local` 同键覆盖合并；开发模式保存落 `system`，用户模式落 `local`，用户模式删除系统预置抛 `SystemContentProtected`，删除本地覆盖时经 `revert_entity_to_system` 回落系统层。
- **文件结构**：
  ```yaml
  content_version: 2
  school: 牵丝·霖
  snapshot:
    inner_ways: [君臣药, 易水歌, 征人归, 四时无常]
    arsenal: 本系
    excludes: [装备固有主属性, 词条与定音, 装备套装（两件套）, 弓玦]
  presets:
    - id: "115_s2"
      level: 115
      stage: 2
      playstyle: null
      label: "115级 2阶段（默认）"
      stats:
        min_outer: 1500.0
        max_outer: 2800.0
        dim_jin: 328.0
        dim_min: 328.0
        dim_shi: 328.0
        dim_ti: 328.0
        dim_yu: 328.0
  ```
- **口径硬边界**：`stats` 与 `session/yysls/play_styles.json` 完全同口径——均为**扣除 8 件装备（固有值 + 40 条词条 + 定音）与弓玦后的纯角色基础属性**。PC `PlanScoringContext` 与 Web `calculateGraduation` 直接沿用现有 `base + gongjue + equipment` 公共计算链，不产生重复叠加。
- **五维随快照一起存**：预置写 `dim_jin/dim_min/dim_shi/dim_ti/dim_yu`（115 阶公共底子五个都是 `328`，快照里的攻击/三率已经按这组五维算好）。评分链把「基础五维 + 装备五维词条」相加后重算**依赖总五维的项**：武学天赋按 `MIN(总五维 / 属性需求, 1)` 缩放，系数登记在 `game_config/schools.yaml` 的 `talent_scaling`（属性需求分母 528）。这样玩家身上叠 劲/敏/势 词条时武学天赋能跟着补满，而不再被基础快照里的比例锁死。
- **兼容边界**：基础属性**没写五维**（历史条目、个人实测、旧版本配置）时不参与上述补算，行为与旧版逐字段一致；装备自身的五维词条一直走 `convert_five_dims` 的线性换算，不受影响。

### 3.2 毕业率模型多阶段扩展（`yysls/graduation/`）

- 模型 JSON 顶层支持可选整数 `"stage"`（缺省兼容为 `1`，110/115 级随版本更新自然推进的方案无需额外写 `stage`）与可选 `"default_base_attribute"`（绑定该方案的默认预置引用，如 `preset:115_s2`）。
- `model_filename(school, scheme, version, stage=...)` 在 `stage == 1` 时保持 `{流派}_{方案名}_v{version}.json`，`stage > 1` 时生成 `{流派}_{方案名}_s{stage}_v{version}.json`。
- `list_graduation_models` 与 `select_graduation_model` 按 `(school, scheme, level, stage, version)` 去重与排序，默认自动选出不超过世界等级的最高 `(level, stage, version)`，并支持按 `stage` 显式筛选。

## 4. 双源引用与 PC / Web 闭环

### 4.1 引用键约定

- **个人实测（`config/session/yysls/play_styles.json`）**：保持原有裸名称字符串，存量方案零迁移（新建个人名称禁止以 `preset:` 开头）；
- **系统/本地预置（`yysls/base_attributes/*.yaml`）**：使用稳定前缀 **`preset:<id>`**（例如 `preset:115_s2`、`preset:115_s2_纯奶`）。

### 4.2 PC 端双源展示与自动绑定

- `resolve_base_attribute_data(school, ref)` 与 `list_all_base_attributes(school)` 作为统一解析入口，服务于 `PlanScoringContext`、备战方案界面（`attrs_tab.py`）、正向推导对照（`attr_derive_panel.py`）、Agent MCP（`core/agent/service.py`）与手机端备战视图（`ondevice/loadout_view.py`）；`format_base_attribute_label` 负责面向用户展示可读标签而不裸露 `preset:` 前缀。
- 流派配置（`SchoolPanel`）右侧拆分为「流派方案」（方案管理 / 预置基础属性 / 个人基础属性三栏 + 数值卡片）与「流派属性」两页；方案管理表新增「默认属性」列，支持双击或右键设置方案绑定的 `default_base_attribute`，预置列表支持新建、改元信息、实时改数值、从个人属性「另存为预置」与用户模式下删除守卫。
- 备战方案「当前属性」下拉框同时展示当前流派的 `[预置]` / `[本地预置]` 条目与 `session/play_styles.json` 个人条目（通过 `itemData` 保存规范引用键）；方案未指定 `base_attribute` 时，按「方案 `default_base_attribute` → 玩法/流派及当前等级默认预置」自动绑定。

### 4.3 Web 端同源导出与无扫描试算

- `scripts/yysls/export_yysls_web_assets.py` 直接读取 `system` 层 `base_attributes`（不受本机 `local` 同 ID 覆盖影响）导出至 `server/web/public/assets/yysls/config.json`，并在 `manifest.json` 的 `ModelEntry` 输出 `stage` 与可选 `default_base_attribute`。
- `server/web` 端实现与 PC 同口径的 `RuleBook.resolveBaseAttributeData` / `resolveDefaultPreset`、`selectModel(..., stage)` 与 `deriveBaseAttributes(panelAttrs, equipped, gongjueContrib, rules)`（面板减装备与弓玦，穿透保留面板值），并在备战页与模拟计算器提供预置/个人双源选择及「新建/反推」弹窗；当前前提是角色已在 PC 同步过 `loadouts` 备战方案，Web 端无需 PC 面板扫描或同步 `play_styles` 即可选择预置或反推试算。

## 5. 已知基线与后续边界

- 系统随包内置全部 12 个流派的 `115_s2` 预置，由 `scripts/yysls/export_reference_base_attributes.py` 生成：脚本下载当前使用的社区计算表的公式单元格（站点地址在运行时通过 `--site` 或 `LVJIANG_REFERENCE_SITE` 提供，不写进仓库与随包数据），用本仓 `graduation.excel_formula` 自己求值，因此数值与那份计算表逐项一致，也不依赖图形界面读数。
- 预置文件只记口径，不记来源：`snapshot` 段写明该快照采用的默认心法、武库选择与排除项；站点地址、表版本号与哈希都留在脚本的控制台输出里，不进入随包配置。
- 快照口径：**排除** 8 件装备固有主属性、词条与定音、装备套装（两件套）、弓玦；**保留** 等级/天赋/地图收集/突破、四条叠音、默认心法、武学天赋、武库（本系）。仓库当前没有心法选择概念，各流派先用一组默认心法，后续迭代再改为可选。
- 网页端（`server/web` 的 `RuleBook.talentScaling` 与 `buildGraduationAttrs`）实现同一份五维补差口径：`config.json` 随资产下发 `talent_scaling` 与预置里的 `dim_*`，两端对同一份数据算出的面板逐字段一致（`.tmp` 里的对拍结果：势 +83.1 → 会意 +0.015775、大外 +96.7297）。
- 115 级上半赛季（`s1`）已结束，不再补录 `s1`。毕业率方案本身随下半赛季更新自然推进，不再按 `s1`/`s2` 拆分或写死阶段字段。
- 后续可在 Web 端继续扩展纯浏览器本地角色/装备池与显式 `stage` 切换控件，本决策已为两者预留数据结构与解析接口。
