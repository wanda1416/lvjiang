# 开发日志 2026-08-17

> 接续 08-16 战斗属性系统 + 全流派毕业率计算器从 0 到 1 + 流派配置 UI 重构 + v0.3.1 发布。
> 本轮主题：**模拟装备功能 + 毕业率模型持续打磨 + yysls 模块重组 + DSL panel 范围索引 + 装备解析器严格化**。

---

## 一、模拟装备功能与装备状态页交互增强（`f3b90d5`）

- 新增 `ui/mock_equip_dialog.py`：模拟装备创建/编辑/删除对话框（550 行新增）；
- 装备状态页右键菜单统一：槽位卡片支持卸载/编辑，网格卡片支持装备/编辑/删除；
- 新增类型筛选下拉框，与槽位按钮双向联动，筛选配置持久化到 session；
- 新增全局 `type → group_key` 映射（`get_type_to_group`），武器名称改为从配置动态加载；
- 装备指纹计算规则文档化至 `docs/30-architecture/31-models/01-equipment-models.md`；
- 毕业率配置扩展（加强方案）、流派配置 UI 继续重构。

### 1.1 装备背包遍历行数硬编码消除（`b34d8d7`）

- `equip_scan.wf` / `equip_funcs.py` 中背包遍历硬编码的行数 3 改为读取配置值。

---

## 二、装备状态页 DPS/毕业率状态行（`f46af26`）

- 装备状态页新增 DPS/毕业率状态展示行，复用角色详情页已有的配置数据，无需重复计算；
- 新增 `graduation_updated` 信号，异步计算完成后通知状态行同步刷新；
- 方案管理支持右键删除，实现为先删文件再改配置以保证原子性；
- 三个配置面板左侧列表默认宽度统一缩至 120px（约 10 个汉字宽度）；
- 新增 `_make_tag` 工具函数，标签样式标准化；
- 新增 `evaluator/graduation_program.py`（421 行），配合毕业率配置的程序化编译；
- 本次 Excel 模型 JSON 因公式展开方式调整，体积大幅收缩（多个流派模型从数万行压缩到数千行）。

### 2.1 毕业评估模型与 UI 更新（`1ab6737`）

- `graduation.py` / `graduation_converter.py` 补充评估逻辑；
- `equip_status_tab.py`、`school_panel.py` 同步 UI 调整；
- 11 个流派基础方案 JSON 做小幅数据修正。

---

## 三、文档与基础设施

### 3.1 毕业率计算引擎文档体系（`ff2a2da`）

- 新建 `docs/30-architecture/36-graduation/` 目录，7 篇文档：四层管线总览、数据流、Excel 公式子集规范、JSON v2 Schema 契约、别名解析规则、编译器与运行时、操作指南；
- 更新旧文档 `03-graduation-formula-model.md`：补充导航链接，修正 v2 schema 描述，移除废弃的 RDPS 引用；
- `30-architecture/README.md` 补充毕业率引擎文档索引。

### 3.2 i18n 翻译文件分层拆分（`93d239c`）

- 主体 YAML（`config/i18n/{zh_CN,en_US}.yaml`）只保留 core 翻译；
- 插件专属翻译拆到 `config/i18n/apps/yysls/{zh_CN,en_US}.yaml`，按需加载；
- 消除全部 `k` 编号 key，替换为中文原文作为 key，可读性提升；
- 新增 `load_app_i18n()`，在 `init_i18n()` 之后由 `run_app()` 调用，带幂等保护；
- zh_CN 基准文件解析失败时的日志改进。

---

## 四、yysls 模块重组（`fef4980`）

顶层模块散落问题（多个同级目录、名不副实的单层 `core`）整理为统一结构，重组后顶层仅保留 `core/config/ui/workflows` 四个子包：

- `core/material_recognizer.py` → `core/recognizer/`；
- `profile/` → `core/profile_engine/`（重命名 + 移入）；
- `equip_parser/` → `core/equip_parser/`；
- `evaluator/` 拆分三处：本体 → `core/evaluator/`，`graduation` → `core/graduation/`，`tuning_rules` → `core/tuning_rules/`；
- `combat_attrs.py` → `core/combat/`；
- `leoq7_export.py` → `ui/`；
- `tune_slots.py` → `config/`。

同步修复 89 个文件的导入路径（相对导入深度、绝对路径引用、monkeypatch 字符串路径）；修正 `core/graduation/__init__.py` 中 `_DATA_DIR` 因目录深度变化需要的 parents 索引；修正「鸣金·虹」基础方案 JSON 里的 `graduation_baseline_dps` 数据错误。

---

## 五、DSL 增强

### 5.1 scan/recognize panel 范围索引 + 材料图库重组（`1c33f76`）

DSL 语法增强：
- `scan` / `recognize` 支持 panel 范围索引 `[r1...r2][c1...c2]`，仅扫描指定行列子集；
- `recognize on group` 支持列表常量内联，无需先赋值变量；
- `scan`/`recognize` 文档重写为精确 BNF 语法定义；
- `grammar.lark` 新增 `scan_panel_index` / `recognize_panel_index` 独立规则；
- engine 新增 `_scan_panel_range` / `_recognize_panel_range` 方法。

材料图库：
- 参考图从分类文件夹重组为 `bucket_0/4/8/C` 哈希分桶结构；
- 新增 3 张材料参考图；
- 「手游.yaml」配置适配新目录结构。

毕业率：
- 新增 `graduation_session.py` 独立会话管理模块；
- `reference_matcher` / recognition 相应适配调整。

### 5.2 recognize full by 全量匹配 + panel range by 降级 + 醉意动作（`83c5170`）

- `recognize` 新增 `full by` 修饰：遍历所有 slot 取最高置信度命中项，与原有 `by` 的短路匹配语义并存；`full by` 仅 `recognize` 支持，`scan`/`find` 使用会报 `WorkflowUserError`；
- `recognize` panel range `[r1...r2][c1...c2]` 支持 `by` 子句降级返回位置 dict；
- `daily_jianghu.wf` 实现 `action_zuiyi` 醉意动作：号令 → 退回首页 → 菜单 → 包裹 → 道具栏 → 识别黄泉酿 → 食用 → 返回号令；觉障林未实现时跳过领奖，避免卡页面；
- 新增测试 `test_recognize_full_by`（5 项断言覆盖语法解析）；`test_scene_scan` 更新期望的 scene 集合。

---

## 六、装备解析器严格化（`cce5bd6`）

- `parser.py` 移除 `_infer_quality` 中的 type 反推回填逻辑：`type` 只能从 `equip_type` OCR 文本直接解析得到，解析失败即为 `None`，不再用其他字段反推兜底；
- `_parse_base_attr` 新增 `is_base_attr_2` 参数，`base_attr_2` 固定匹配「外功防御」（仅防具持有），且不参与品阶推断；
- `equip_scan.wf` / `equip_analysis.wf` 删除 `fill_equipment_type` 调用，门控简化为 `if not $equip.type`；
- 删除 `subcall/equipment.wf`（内容仅 `fill_equipment_type`，已无用）；
- `builtins/equipment.py` 删除 `is_valid_equipment` builtin；`models.py` 删除 `is_valid` property；
- `config/manager.py` 新增 `base_attr_names` 属性，暴露合法属性名列表；
- `equip_status_tab.py` 修复 `get("level", default)` 在值为 `None` 时抛出 `TypeError` 的问题；
- `TODO.md` 新增 OCR 三态语义分层待办项；
- 测试同步更新：断言 `type=None`（不再反推）。

---

## 七、收尾（`b8ee87c`，08-18 凌晨）

作为 08-17 工作的自然延续：

- 新增 `subcall/game_profile.wf` 的 `parse_bugan_jindu` 过程，含 OCR 噪声检测与校验；`scan_wallet.wf` / `purchase_bugan.wf` 内联解析统一替换为 `call parse_bugan_jindu`；
- `purchase_bugan.wf` 营生格子购买前增加 OCR 确认含「营生」二字（承接 08-16 `50717de` 的扫描确认修复，进一步收紧校验）；
- `daily_jianghu.wf` 情境保存改用 `wait stable` 等待弹窗渲染完成，修复保存时序问题。

---

## 结果

- 本日（含收尾提交）共 13 commits；
- yysls 模块顶层结构收敛为 core/config/ui/workflows 四层；
- 毕业率计算模型经历二次数据结构调整（`1ab6737` → `f46af26`），体积明显收缩；
- 未发布新版本号（v0.3.1 已在 08-16 发布，本轮内容将计入下一版本）。

---

## 关键设计决策（用户确认）

1. **yysls 顶层模块统一收拢**：消除散落的同级目录与名不副实的单层 `core`，顶层固定为 core/config/ui/workflows 四个子包，为后续模块继续增长定好边界。
2. **装备 type 字段不再反推兜底**：只信任 `equip_type` OCR 直接解析结果，宁可为 `None` 也不用其他字段间接推断，避免错误传播。
3. **recognize full by 与 by 并存**：`by` 保持短路匹配（性能优先），新增 `full by` 遍历取最高置信度（准确优先），按场景选用；`scan`/`find` 不支持 `full by`，避免语义混淆。
4. **i18n 按 core/apps 分层**：核心翻译与插件翻译分离，插件翻译按需加载，且用中文原文替代编号 key，降低维护成本。
