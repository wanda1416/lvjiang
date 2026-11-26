# 预置基础属性分层与毕业率多阶段模型

> 状态：已实现（涵盖 PC 配置层、流派配置双子页/三栏编辑、方案默认属性绑定、备战方案双源展示、Web 资产导出与无扫描反推试算；当前系统随包内置已校验的 `鸣金·虹` 115 级 1 阶段预置，其余流派待校验补齐）。

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
  content_version: 1
  school: 牵丝·霖
  presets:
    - id: "115_s1"
      level: 115
      stage: 1
      playstyle: null
      label: "115级 1阶段（默认）"
      stats:
        min_outer: 1500.0
        max_outer: 2800.0
  ```
- **口径硬边界**：`stats` 与 `session/yysls/play_styles.json` 完全同口径——均为**扣除 8 件装备（固有值 + 40 条词条 + 定音）与弓玦后的纯角色基础属性**。PC `PlanScoringContext` 与 Web `calculateGraduation` 直接沿用现有 `base + gongjue + equipment` 公共计算链，不产生重复叠加。

### 3.2 毕业率模型多阶段扩展（`yysls/graduation/`）

- 模型 JSON 顶层支持可选整数 `"stage"`（缺省兼容为 `1`，存量 110/115 模型零改动即可加载）与可选 `"default_base_attribute"`（绑定该方案的默认预置引用，如 `preset:115_s1`）。
- `model_filename(school, scheme, version, stage=...)` 在 `stage == 1` 时保持 `{流派}_{方案名}_v{version}.json`，`stage > 1` 时生成 `{流派}_{方案名}_s{stage}_v{version}.json`。
- `list_graduation_models` 与 `select_graduation_model` 按 `(school, scheme, level, stage, version)` 去重与排序，默认自动选出不超过世界等级的最高 `(level, stage, version)`，并支持按 `stage` 显式筛选。

## 4. 双源引用与 PC / Web 闭环

### 4.1 引用键约定

- **个人实测（`config/session/yysls/play_styles.json`）**：保持原有裸名称字符串，存量方案零迁移（新建个人名称禁止以 `preset:` 开头）；
- **系统/本地预置（`yysls/base_attributes/*.yaml`）**：使用稳定前缀 **`preset:<id>`**（例如 `preset:115_s1`、`preset:115_s2_纯奶`）。

### 4.2 PC 端双源展示与自动绑定

- `resolve_base_attribute_data(school, ref)` 与 `list_all_base_attributes(school)` 作为统一解析入口，服务于 `PlanScoringContext`、备战方案界面（`attrs_tab.py`）、正向推导对照（`attr_derive_panel.py`）、Agent MCP（`core/agent/service.py`）与手机端备战视图（`ondevice/loadout_view.py`）；`format_base_attribute_label` 负责面向用户展示可读标签而不裸露 `preset:` 前缀。
- 流派配置（`SchoolPanel`）右侧拆分为「流派方案」（方案管理 / 预置基础属性 / 个人基础属性三栏 + 数值卡片）与「流派属性」两页；方案管理表新增「默认属性」列，支持双击或右键设置方案绑定的 `default_base_attribute`，预置列表支持新建、改元信息、实时改数值、从个人属性「另存为预置」与用户模式下删除守卫。
- 备战方案「当前属性」下拉框同时展示当前流派的 `[预置]` / `[本地预置]` 条目与 `session/play_styles.json` 个人条目（通过 `itemData` 保存规范引用键）；方案未指定 `base_attribute` 时，按「方案 `default_base_attribute` → 玩法/流派及当前等级默认预置」自动绑定。

### 4.3 Web 端同源导出与无扫描试算

- `scripts/yysls/export_yysls_web_assets.py` 直接读取 `system` 层 `base_attributes`（不受本机 `local` 同 ID 覆盖影响）导出至 `server/web/public/assets/yysls/config.json`，并在 `manifest.json` 的 `ModelEntry` 输出 `stage` 与可选 `default_base_attribute`。
- `server/web` 端实现与 PC 同口径的 `RuleBook.resolveBaseAttributeData` / `resolveDefaultPreset`、`selectModel(..., stage)` 与 `deriveBaseAttributes(panelAttrs, equipped, gongjueContrib, rules)`（面板减装备与弓玦，穿透保留面板值），并在备战页与模拟计算器提供预置/个人双源选择及「新建/反推」弹窗；当前前提是角色已在 PC 同步过 `loadouts` 备战方案，Web 端无需 PC 面板扫描或同步 `play_styles` 即可选择预置或反推试算。

## 5. 已知基线与后续边界

- 当前系统随包内置已校验的 `鸣金·虹` `115_s1` 预置；其余 11 个流派待结合实测样本与 [基础属性来源模型](../../01-architecture/03-models/06-base-attr-model.md) 校验后逐一补齐。
- 后续可在 Web 端继续扩展纯浏览器本地角色/装备池与显式 `stage` 切换控件，本决策已为两者预留数据结构与解析接口。
