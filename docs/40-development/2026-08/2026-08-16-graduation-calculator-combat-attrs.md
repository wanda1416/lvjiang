# 开发日志 2026-08-16

> 接续 08-15 DSL EntityRef 表达式 + click/drag 时序细化 + 调律进度合并到右侧 Tab + macros 模块迁移至 ui 层 + v0.3.0 发布。
> 本轮主题：**战斗属性系统 + 全流派毕业率计算器从 0 到 1 + 流派配置 UI 重构 + v0.3.1 发布**。

---

## 一、战斗属性系统

### 1.1 装备指纹共享函数与自动调律委托（`7a95105`）

- `equip_parser/models.py` 新增 `make_fingerprint` 共享函数，文档说明定音词条不参与指纹计算；
- `auto_tuning.py` 的 `_make_fingerprint` 改为委托给共享函数，消除重复实现。

### 1.2 战斗属性数据模型与玩法配置存储（`50fdca6`）

- 新增 `combat_attrs.py`：战斗属性核心模型，含穿透计算、字段分组、占位符映射；
- 新增 `config/play_styles.py`：玩法配置存储，提供 get/save/delete/rename；
- `config/manager.py` 新增 `get_base_attr_values`、`get_school_attr` 方法。

### 1.3 战斗属性 Tab 与装备状态页 UI 重构（`d0ab8a9`）

- 新增 `combat_attrs_tab.py`：战斗属性展示面板，含穿透显示、反推计算、游戏风格卡片；
- 新增 `equip_status_panel.py` 装备状态面板组件；
- `equip_status_tab.py` 重构为卡片式 UI，支持右键菜单；
- `main_window.py` 新增 `equipment_changed` 信号，用于跨 Tab 同步。

---

## 二、流派配置 UI 重构（`24b4baa`）

- `school_panel.py`：玩法管理占据剩余空间、字段平铺展示、右键菜单删除、卡片可编辑；
- `affix_caps_panel.py` 左侧面板宽度统一为 150px；
- 同步更新 `test_game_config_ui.py`。

后续 `abd7ab6` 补充词条部位展示三档逻辑：全选显示「全部」；选中 ≥4 个部位时展示「非 XX/XX」（未选部位更精简）；<4 个时展示已选部位。

---

## 三、全流派毕业率计算器

### 3.1 场景与批处理配置收尾

- `50717de` 营生购买格子前增加内容扫描确认，避免误购非营生商品（v0.3.0 起存在的问题）；
- `11772bb` 装备详情场景/布局配置更新，批处理表格列宽改为自适应+拉伸，同步移除过时的 `docs/user-guide.md`。

### 3.2 全流派毕业率计算器与数据提取（`66abea2`）

- 新增 `GenericCalculator` 替代原来单流派的 `MingjinHongCalculator`，改为从 JSON metadata 读取参数驱动计算；
- 新增 `SCHOOL_ELEMENT` / `SCHOOL_WEAPON` 映射，桥接函数按流派动态设置主属性（鸣金/裂石/牵丝/破竹）与主武器；
- `_calc_element_bonus` 泛化，支持全部武器类型的增伤计算；
- 工厂函数注册全部 11 个流派：鸣金·虹/影，裂石·威/钧，牵丝·玉/霖/翊，破竹·尘/风/鸢/樽；
- 新增 `scripts/extract_graduation_data.py`，从原始 Excel 计算器自动提取技能轴/技能/增益数据；
- 新增 `data/graduation/` 下 11 个流派的 JSON 数据文件（含各流派基准 DPS，如鸣金·虹 120570.64）。

### 3.3 战斗属性面板集成毕业率实时展示（`53375a6`）

- `combat_attrs_tab.py` 新增毕业率实时展示卡片（DPS + 毕业率百分比），随属性变化联动刷新。

---

## 四、调律报告结构化数据（`612a7de`）

- `TuningDocWriter` 在 `run_summary` 末尾追加 HTML 注释包裹的 JSON 数据块，记录每件装备的初始词条、每轮狗粮/新词条、最终评级、结束原因等结构化数据；
- 新增 `scripts/analyze_tuning_affixes.py` 词条分布分析脚本：优先解析 JSON 数据块，fallback 到 markdown 正则解析（兼容旧报告）；
- 统计维度覆盖按部位/品阶/评级、按位置词条分布、首词条条件概率、合并统计。

---

## 五、毕业率 Excel 公式模型重构（`48a77f0`）

原 `66abea2` 的硬编码 JSON 数据（技能轴/增益写死在 Python 里）改为从 Excel 计算器直接派生的公式模型：

- 新增 `evaluator/excel_formula.py`：Excel 公式子集运行时引擎；
- 新增 `evaluator/graduation_converter.py`：工作簿 → JSON 模型转换器；
- `graduation.py` 重构为由 `GenericCalculator` 驱动 Excel 模型计算 DPS/毕业率，不再依赖手工提取的技能轴数据；
- `combat_attrs.py` 抗性函数支持参数化 `resistance` 值（从配置读取，而非硬编码）；
- 新增 11 个流派的基础方案 JSON 模型，落地到 `config/system/yysls/graduation/`；删除旧的 `data/graduation/` 硬编码数据；
- UI 集成：方案选择下拉框、导入 Excel 按钮、缓存失效机制；
- `config/manager.py` 新增 `get_affix_aliases`、`get_graduation_schemes` 等查询方法；
- 新增 `openpyxl` 依赖用于解析 Excel；
- 新增架构文档 `docs/30-architecture/03-graduation-formula-model.md`。

这次重构幅度大：3.2 提取的静态 JSON 数据文件被整体替换为体积大得多的 Excel 派生模型 JSON（单流派模型可达数万行）。

---

## 六、v0.3.1 发布（`5b62a1e`）

- 版本号升级 0.3.0 → 0.3.1；
- 发布说明 `docs/50-releases/v0.3.1.md`：17 commits（自 v0.3.0），87 files changed（+49,381 / -1,952），pytest 1781 例全绿；
- 内容涵盖毕业率计算系统、战斗属性 Tab 与装备状态页 UI 重构、流派配置 UI 重构、DSL 引擎增强（click/drag 时序、通用元组语法、clock/datetime 拆分）。

### 6.1 需求/架构文档格式统一（`53c9896`）

- 统一 `02-player-profile.md`、`05-graduation-rate-calc.md`、`03-session-and-context.md` 等文档中的示例数据格式。

---

## 结果

- 本日提交 12 commits；
- v0.3.1 版本发布完成；
- 毕业率计算系统完成第一轮迭代：从静态 JSON 数据（3.2）演进为 Excel 公式模型驱动（五）。

---

## 关键设计决策（用户确认）

1. **毕业率计算引擎两阶段演进**：先用提取自 Excel 的静态技能轴 JSON 快速跑通 11 个流派（`66abea2`），当天内即重构为 Excel 公式子集运行时直接驱动计算（`48a77f0`），避免长期维护两套数据口径。
2. **装备指纹计算集中到共享函数**：`make_fingerprint` 统一定义在 `equip_parser/models.py`，定音词条不计入指纹，`auto_tuning.py` 委托调用而非各自实现。
3. **调律报告双格式**：markdown 可读报告 + 尾部 HTML 注释包裹的结构化 JSON 数据块，兼顾人读与脚本分析，且向后兼容旧报告的正则解析。
4. **词条部位展示按数量分档**：≥4 个选中部位时展示排除项（「非 XX/XX」）而非全部列出，控制 UI 文本长度。
