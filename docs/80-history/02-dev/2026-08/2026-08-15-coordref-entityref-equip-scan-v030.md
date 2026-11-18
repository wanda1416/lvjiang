# 开发日志 2026-08-15

> 接续 08-14 DSL int/float 双类型改造、装备背包视图合并、感知指令文档拆分。
> 本轮主题：**DSL CoordRef/EntityRef 坐标与表达式体系 + 装备扫描/背包筛选增强 + 调律进度 Tab 整合 + v0.3.0 发布**。

---

## 一、装备扫描与背包筛选增强

### 1.1 fill_equipment_type 子过程（`7da44ca`）

- 锚点为空时立即结束当前槽位扫描，不再继续扫描后续行；
- 新增 `fill_equipment_type` 子过程，用于填充装备部位类型；
- 新增 `config/system/workflows/subcall/equipment.wf`，装备相关子过程模块化；
- `ocr_rules.yaml`、`equip_analysis.wf` 随之更新；`leoq7_export.py` import 排序修复。

### 1.2 mypy 修复：quality 可能为 None（`24a04c6`）

- `equip_status_tab.py` 两处 `quality` 变量可能为 `None`，改用 `or ""` 兜底为字符串。

### 1.3 fill_equipment_type 调用顺序修复（`f9fffe5`）

- 空槽不应写入：先检查 OCR 是否有效数据，确认有数据后再调用 `fill_equipment_type`。

### 1.4 等级/词条筛选下拉框（`8a4a04d`）

- 导出数据按钮左侧新增等级、词条两个筛选下拉框；
- 等级选项从 `game_config` 动态填充（降序），含"全部"；
- 词条选项为全部/定音/满调律，定音天然包含满调律；
- 筛选配置持久化到 `session.json` 的 `settings.equip_filter`，导出时同步应用（仅背包物品，已装备不受影响）；
- 配置读写入口收拢为 `load_equip_filter` / `save_equip_filter`；
- UI 遵循显示文本与数据值分离规范（`addItem` + `currentData`），初始化期间 `blockSignals` 避免冗余写入。

---

## 二、DSL 场景引用语义收拢（`41091ae`）

- `grammar.lark` 移除 `const_or_var` 与 `scene_name` 中的 `STRING` 分支；
- parser 移除 `_resolve_const_or_var` / `_resolve_scene_name` 的 STRING 处理逻辑；
- 语义模型简化为两种引用形式：`[name]` 表示配置引用（场景/Area/Action/Panel 名），`$var` 表示变量引用；字符串字面量仅用于 `eval`/`log`/`by` 匹配/函数参数；
- 强调 panel 索引与配置引用的语义区分：`row`/`col` 不是 area 引用；
- 同步更新测试用例与文档中"三种形式"改为"两种形式"的表述；顺带补充 i18n 部分翻译。

---

## 三、批处理进度页整合（`6265a00`）

- 子 Tab 顺序调整为：脚本 / 配置 / 进度；
- 进度表条目列优先显示 `user_column` 对应的值，未配置时回退为拼接所有列；
- `batch_runner._format_label` 同步改用 `user_column`。

---

## 四、DSL CoordRef 坐标统一体系（`3d4622e`）

分六个阶段推进：

- Phase 1：新建 `coord_types.py`，定义 `CoordRef` / `RectCoordRef` / `CircleCoordRef` / `Offset` 类型层次；
- Phase 2：AST 重命名 `SceneRef` → `EntityRef`（字段 `region` → `entity`）；
- Phase 3：布局模型扩展 `to_coord_ref()`，覆盖 Region/Point/Panel/FoundRegion；
- Phase 4：引擎表达式求值支持 CoordRef 运算与 tuple 隐式转换；
- Phase 5：click/drag 解析更新，支持 CoordRef 点击，Point 用于 drag 时报错；
- Phase 6：文档梳理 Entity 层次、CoordRef 类型体系与运算示例。

---

## 五、调律进度 UI 整合：独立窗口合并到 Tab（`66f8715`）

- 新增 `tuning_progress_widget.py`，从 `TuningProgressDialog` 提取出可嵌入的 `QWidget`；
- 右侧 Tab 栏注册"调律进度" Tab，位于"其他信息"之后；
- `tuning_tab.py` 删除"自动打开调律进度"复选框与"打开进度"按钮，新增 `_find_progress_widget()` 按类型查找右侧 Tab；
- 删除 `tuning_progress_dialog.py`（441 行死代码）；
- `overlay.py`：装备选中边框 `pen_width` 提升至 10；
- `fast_test.py` 映射表同步更新 `tuning_progress_dialog` → `tuning_progress_widget`。

---

## 六、DSL 支持 EntityRef 表达式（`9cf870c`）

- grammar 新增 `entity_ref` 规则并加入 `factor`，支持形如 `$a = [scene].[region]` 的赋值；
- parser 新增 `entity_ref` transformer，生成 `EntityRef` AST 节点；
- engine `_resolve` 新增 EntityRef 分支，查布局后返回 CoordRef；
- engine `_eval_arith` 新增 FoundRegion → RectCoordRef 隐式转换；
- `static_check` 新增 `_collect_from_expr`，递归收集表达式内的 EntityRef 引用。

---

## 七、v0.3.0 发布（`fabbeeb`）

- 版本号升级至 0.3.0；发布文档 `docs/50-releases/v0.3.0.md`；
- 发布流程补充"修复收录原则"；
- 版本主题：DSL 坐标体系（CoordRef/EntityRef）、国际化框架、装备扫描与背包增强、调律进度 UI 整合；
- 发布说明记录：216 commits（自 v0.2.5）、209 files changed（+12,498/-3,789）、pytest 1654 例全绿。

---

## 八、DSL 深化：click/drag 时序 + 泛化元组 + clock/datetime 拆分

### 8.1 功能改动（`937dd98`）

- click/drag 显式传入 `wait_clause` 时抑制默认的 before/after_click_wait；
- `click_screen`/`drag_screen` 扩展 `pre_delay`/`post_delay` 参数，`**kw` 贯穿 `click_any`/`click_region`/`click_point`/`drag_arrow`；
- `wait_range`/`range_literal` 泛化为支持混合的 `number|var_ref`；新增 `TupleLiteral` AST 节点与 `__tuple__` data_ops 处理器；
- 新增 `wait_clauses` 规则，允许 before/after 任意组合；parser 将 `around` 展开为 before+after 并强制语义顺序；
- `clock()` 拆分为 `clock()`（返回时间戳）与 `datetime()`（格式化），`datetime()` 接受可选时间戳参数用于格式化 `clock()` 结果；
- engine 新增 `WorkflowUserError`，用于 tuple wait 中未定义变量的报错。

### 8.2 测试补充（`a9d900f`）

- `test_builtins` 新增 `TestClock` / `TestDatetime` 用例，覆盖 `datetime()` 带时间戳与格式参数的场景；
- `test_parser` 新增混合元组测试（`wait(\,\)` / `eval \=(1,\)`）、单一 wait_clause 的 `suppress_defaults` 断言、before+after 组合测试；
- `test_auto_tuning_flow` 适配 `FakeWF` 的 `click_region`/`click_panel` 以支持 `**kw`。

### 8.3 文档更新（`7fd3f6b`）

- `03-1-interaction.md`：补充 `suppress_defaults` 说明与 `wait_clause` 组合语法；
- `06-functions.md`：函数总数更新为 52，`clock`/`datetime` 归入时间类；
- `06-2-system-interaction.md`：clock 章节拆分为 clock + datetime 两节；
- `01-basics.md`/`05-control-flow.md`：将"范围元组"改名为"泛化元组"，补充混合引用示例；
- README 同步更新函数计数与时间类分类行。

---

## 九、配置与场景同步（`89739b5`）

- `general_control` 场景新增区域与布局绑定；
- `workflows.yaml` 新增一条工作流条目；新增 `daily_zhayu.wf`；
- i18n 补充少量翻译条目；`canvas_poi.py` 小幅调整。

---

## 十、结果

- 本日提交 14 commits；
- DSL 层完成坐标体系（CoordRef）、表达式体系（EntityRef）、时序控制（click/drag timing）、泛化元组、clock/datetime 拆分等多项能力增强；
- 装备扫描与背包筛选完成闭环；调律进度窗口并入主界面 Tab；
- v0.3.0 正式发布。

---

## 十一、关键设计决策（用户确认）

1. **CoordRef 类型层次**：`CoordRef`/`RectCoordRef`/`CircleCoordRef`/`Offset` 统一坐标运算，支持向量加减，`AST SceneRef` 同步重命名为 `EntityRef` 以呼应新的实体语义。
2. **EntityRef 可直接参与表达式**：`$a = [scene].[region]` 赋值后可与 CoordRef 做算术运算，FoundRegion 隐式转换为 RectCoordRef。
3. **场景引用语义简化**：废弃字符串形式的场景引用，统一为 `[name]`（配置引用）与 `$var`（变量引用）两种形式，字符串字面量仅保留给数据场景使用。
4. **调律进度窗口并入 Tab**：独立对话框改造为可嵌入 QWidget，注册到右侧 Tab 栏，删除原有 441 行独立窗口代码。
5. **click/drag 显式 wait_clause 优先**：显式传入等待子句时抑制默认前后等待，避免叠加延迟；`clock`/`datetime` 拆分为职责单一的两个函数。
