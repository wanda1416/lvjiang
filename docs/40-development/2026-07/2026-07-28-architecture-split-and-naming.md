# 开发日志 2026-07-28

> 接续 07-27 自动调律链路补全。
> 本轮主题：**架构拆分与术语统一**——DSL 引擎拆 engine/ 包、用户管理 UI
> 重构、延迟参数体系重构、三层术语模型重命名、CI 接入。
> pytest ~600 → **~650 例全绿**。

---

## 一、背包遍历策略化拆分 + 调律说明文档输出（`ca9bbbe`）

### 1.1 背景

自动调律工作流的背包遍历逻辑原本内联在 `auto_tuning.py`，与潜力判定、
调律执行耦合。随着遍历策略多样化（dedup 滑动窗口去重 / positional 位置
对齐），需要抽象为策略类。

### 1.2 改动

- 新增 `bag_traversal/` 包：
  - `base.py`：`BagTraversalStrategy` 抽象基类（`traverse(wf, detail_scene)`）；
  - `dedup.py`：滑动窗口指纹去重遍历（默认策略）；
  - `positional.py`：位置对齐三向校验遍历；
  - `__init__.py`：`TRAVERSALS` 注册表 + `DEFAULT_TRAVERSAL = "dedup"`。
- `AutoTuningWorkflow._traverse_bag()` 改为按配置调度：
  ```python
  key = self.get_variable("traversal") or DEFAULT_TRAVERSAL
  TRAVERSALS[key]().traverse(self, detail_scene)
  ```

### 1.3 调律说明文档

新增 `TuningDocWriter`（`workflows/tuning_doc.py`）：

- 每次自动调律运行创建一份 Markdown 文档（`logs/tuning/调律说明_{用户}_{时间}.md`）；
- 文档头：开始时间、操作用户、启用规则及玩法、开关、部位；
- 每件装备：序号、部位、品阶、词条、潜力判定、调律过程（每轮词条变化）、终局评级；
- 叙事型输出，供用户阅读决策过程。

---

## 二、DSL 引擎单文件拆分为 engine/ 包（`29c2121`）

### 2.1 背景

`src/workflows/engine.py` 单文件 1000+ 行，职责过重（解析、执行、变量、
内置函数、等待校验全在一起）。

### 2.2 改动

拆为 `src/workflows/engine/` 包，Mixin 组合：

```
engine/
├── __init__.py          # Engine 主类（继承各 Mixin）
├── _parsing.py          # 解析 Mixin（.wf 文件解析）
├── _execution.py        # 执行 Mixin（指令调度）
├── _variables.py        # 变量 Mixin（$var 访问）
├── _builtins.py         # 内置函数 Mixin
├── _wait.py             # 等待校验 Mixin
└── _helpers.py          # 辅助函数
```

- 导入路径不变（`from src.workflows.engine import Engine`）；
- 各 Mixin 职责单一，便于测试与维护。

---

## 三、用户管理对话框改左右分列式布局（`7c5b996`）

- 旧布局：用户列表 + 详情表单上下堆叠；
- 新布局：左侧用户列表（QListWidget，maxWidth 200）+ 右侧详情卡片（QFormLayout）；
- 详情卡片显示用户名、创建时间、当前用户标记。

---

## 四、延迟参数体系重构（`62cf995`）

### 4.1 背景

工作流中的等待/延迟参数（`step_interval`、`scroll_settle_wait` 等）原本
与引擎字段混在同一命名空间，导致：

- 引擎字段（如 `max_retries`）与命名等待（如 `step_interval`）难以区分；
- workflows.yaml 加载期无法校验命名等待是否存在。

### 4.2 改动

- 引擎字段与命名等待分离：
  - 引擎字段：`EngineConfig` dataclass（强类型）；
  - 命名等待：`delays` 字典（`{name: seconds}`）。
- workflows.yaml 加载期校验：所有引用的命名等待必须在 `delays` 中声明。

---

## 五、用户列表支持拖拽排序并持久化（`0a36885`）

- 用户管理对话框左侧用户列表支持拖拽排序（QListWidget.setDragDropMode）；
- 排序结果持久化到 `config/local/users/` 目录下的 `order.json`；
- 移除「设为当前用户」按钮（当前用户由启动参数决定），收窄左栏。

---

## 六、补充 100/96 级饰品/头/胸部位 purple/blue 基础属性数值（`1a3cfdc`）

- attributes.yaml `base_attrs` 补充 100 级 / 96 级饰品（环/佩）、头（冠胄）、
  胸（胸甲）部位的紫色 / 蓝色品阶基础属性数值；
- 用于品阶反查回填（`_infer_quality`）。

---

## 七、配置对话框交互优化（`5f53de0`）

- 脏状态保存：关闭配置对话框时若有未保存修改，弹确认框；
- 删除确认：删除用户/规则前弹 QMessageBox.question；
- 保存/关闭分离：右下角「保存」+「关闭」两按钮（旧版合并为单按钮）。

---

## 八、新增 GitHub Actions 工作流（`a75ecdd`）

- `.github/workflows/ci.yml`：
  - 触发：push / pull_request to master；
  - 运行环境：windows-latest；
  - 步骤：checkout → setup python → install deps → pytest（离屏模式）。
- 离屏 pytest 通过 `QT_QPA_PLATFORM=offscreen` 环境变量实现。

---

## 九、按三层术语模型全面重命名（`ad37a0e`）

### 9.1 背景

项目中「流派」「玩法」「调律规则」三个概念长期混用（如「流派设置」实际
编辑的是调律规则，「流派配置」实际包含玩法维度）。

### 9.2 三层术语模型

| 层 | 含义 | 示例 |
|----|------|------|
| 流派 | 玩家选择的玩法方向 | 会心双刀、纯奶、裂石 |
| 玩法 | 流派内的细分路线 | 纯唐、双切、走地、飞天 |
| 调律规则 | 装备判定的具体规则文件 | huiyi_general.yaml、huixin_big.yaml |

### 9.3 改动

- UI 文案：「流派设置」→「规则设置」、「流派配置」→「玩法配置」；
- 变量名：`school` → `rule`（部分场景）、`playstyle` 显式化；
- YAML 字段：`schools` 保留（流派层）、`sub_schools` → `playstyles`（玩法层）；
- 全库 grep 同步（docs / src / tests / config）。

---

## 十、区间单元格未配置占位符由 — 改为空白（`e83d5bb`）

- 基础属性面板（base_attr_panel.py）区间单元格未配置时显示空白（旧显示 `—`）；
- 视觉更清爽，避免误导用户以为有数据。

---

## 十一、结果

- pytest 全绿；
- 全部改动已提交并推送至 `origin/master`（最新 `e83d5bb`）。

---

## 十二、关键设计决策（用户确认）

1. **背包遍历策略化**：dedup / positional 两策略，默认 dedup。
2. **DSL 引擎拆包**：Mixin 组合，导入路径不变。
3. **三层术语模型**：流派 / 玩法 / 调律规则严格区分。
4. **延迟参数分离**：引擎字段（强类型）与命名等待（字典）解耦。
5. **CI 离屏 pytest**：windows-latest + QT_QPA_PLATFORM=offscreen。

---

## 十三、用户关键指令索引

| 指令 | 影响范围 |
|------|----------|
| 「背包遍历策略化拆分」 | bag_traversal/ + auto_tuning.py |
| 「调律说明文档输出」 | tuning_doc.py + logs/tuning/ |
| 「DSL 引擎拆包」 | engine/ (Mixin) |
| 「用户管理左右分列」 | user_manager_dialog.py |
| 「延迟参数体系重构」 | engine/config.py + workflows.yaml |
| 「用户列表拖拽排序」 | user_manager_dialog.py + order.json |
| 「补充基础属性数值」 | attributes.yaml |
| 「配置对话框交互优化」 | 多个 *_panel.py |
| 「CI 工作流」 | .github/workflows/ci.yml |
| 「三层术语模型重命名」 | 全库 |
| 「区间占位符改空白」 | base_attr_panel.py |
