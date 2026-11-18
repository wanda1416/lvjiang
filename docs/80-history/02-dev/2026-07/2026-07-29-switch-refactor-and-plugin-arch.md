# 开发日志 2026-07-29

> 接续 07-28 架构拆分与术语统一。
> 本轮主题：**调律规则开关化重构（四档评级 + 条件原语 + when 开关）** 与
> **UI 插件化注入架构落地**，配套潜力判定重构、江湖号令活动工作流、
> 崩溃防护加固。
> pytest ~650 → **~720 例全绿**。

---

## 一、拆分四个主体大文件为分层/Mixin 包（`5b5e3d3`）

### 1.1 背景

继 07-28 DSL 引擎拆包后，继续拆分剩余四个主体大文件：

- `src/apps/yysls/ui/rules_editor/rules_dialog.py`（800+ 行）；
- `src/apps/yysls/workflows/implementations/auto_tuning.py`（700+ 行）；
- `src/apps/yysls/evaluator/tuning_rules/generic.py`（600+ 行）；
- `src/apps/yysls/equip_parser/parser.py`（500+ 行）。

### 1.2 改动

- `rules_dialog.py` → `rules_editor/dialog/` 包（主对话框 + Tab 容器 + 新增规则子对话框）；
- `auto_tuning.py` → `auto_tuning/` 包（主流程 + 文档插桩 + 材料管理 + 调律执行）；
- `generic.py` → `tuning_rules/generic/` 包（归一化 + 模式构建 + 穷举匹配）；
- `parser.py` → `equip_parser/parser/` 包（类型解析 + 基础属性 + 词条 + 定音 + 品阶）。

导入路径均保持不变（`__init__.py` 重导出）。

---

## 二、词条归属分类与可复用选择排序对话框（`f68c9af`）

### 2.1 背景

调律规则编辑器中「可用词条库」与「可选槽词条库」需要按词条归属分类
（攻击/防御/特殊/属攻）筛选，旧版 checkbox 网格无法排序。

### 2.2 改动

- 新增 `AffixClassificationDialog`（可复用选择排序对话框）：
  - 左侧分类列表（QListWidget，攻击/防御/特殊/属攻/全部）；
  - 右侧词条列表（QListWidget，支持上下移动排序）；
  - 底部已选词条标签（QLabel，灰色提示）。
- `pool_page.py` / `part_pattern_page.py` 接入该对话框。

---

## 三、品阶门槛按部位锁死 + 规则级覆盖（`92d5505`）

### 3.1 背景

装备品阶门槛（如「至少紫色」）原本全局统一，但不同部位门槛不同：
武器/首饰通常要求金色，防具可放宽到紫色。同时某些规则需要覆盖全局门槛
（如纯奶规则对武器要求放宽）。

### 3.2 改动

- `tuning_base.yaml` 新增 `quality_threshold_by_part` 段（部位 → 最低品阶）；
- 规则 YAML 顶层可选 `quality_threshold_override` 字段（覆盖部位门槛）；
- 判定器读门槛时：规则覆盖 > 部位门槛 > 全局默认；
- 调律部位文案标准化（「主武器」/「冠胄」等统一用语）。

---

## 四、修复 scene_ops 中 QLabel 未导入 + 新增江湖活动场景配置（`d1f5197`）

- `scene_ops.py` 补 QLabel 导入（NameError 修复）；
- 新增江湖活动场景配置（`config/system/scenes/jianghu_activity.yaml`）。

---

## 五、调律规则开关化重构（`6db9b32`）

### 5.1 背景

详见方案定稿文档 `docs/40-development/2026-07/2026-07-26-tuning-switch-refactor.md`。
核心动机：`keep_pvp` 是全局复选框 + 引擎硬编码覆盖层（judge.py 三处特判），
业务语义藏在 if 分支里，规则 YAML 读不出来，判定结果说不清楚。

用户洞察：**PVP 不是特殊机制，只是一个布尔开关 + 几条带开关前提的普通条件**。
因此彻底废弃 PVP 专用语义，改为**通用开关机制**。

### 5.2 评级四档

- `Rating` 枚举：`TOP="顶级"` / `EXCELLENT="优秀"` / `NORMAL="一般"` / `JUNK="垃圾"`；
- `Rating.USABLE`（"能用"）→ `NORMAL`（"一般"），全库 grep 同步（UI 配色 /
  清理工作流 / 调律文档 / 测试断言）；
- 规则 YAML 新增顶层字段 `default_rating`（缺省 `excellent`）；
- 判定顺序：垃圾 → 一般 → 优秀 → 顶级，先命中先得；全不命中 → `default_rating`。

### 5.3 条件原语收敛为 4 个

| 原语 | 语义 |
|------|------|
| `contains_all` | 必须同时出现 |
| `not_together` | 不得同时出现 |
| `count_max` | 计数不得超过 |
| `count_min` | 计数不得低于 |

- 均支持 `include_first: true` 修饰符（含首词条）；
- `not_contains` 废弃（等价于 `count_max: 0`）。

### 5.4 通用开关机制

- `tuning_base.yaml` 新增 `switches` 段（开关注册表）：
  ```yaml
  switches:
    keep_pvp: {name: "保留 PVP 等价词条"}
    skip_tuning: {name: "跳过实际调律（测试）"}
  ```
- 规则 YAML 条件组新增 `when` 字段（开关前提）：
  ```yaml
  patterns:
    冠胄:
      normal:
        - when: {keep_pvp: true}
          contains_all: [单体类奇术增伤]
  ```
- 引擎判定前先按开关状态过滤条件组（`when` 不匹配则跳过该组）。

### 5.5 UI 改动

- 基础配置页新增「开关设定」区块（用户可增删 key+name 的全局开关）；
- 规则设置页新增「装备评级」区块（四档下拉 + default_rating）；
- 条件编辑器支持 `when` 下拉（选择开关前提）。

### 5.6 测试

- `test_rule_loader.py`：switches / when / 四原语 / default_rating 解析校验；
- `test_judge.py`：四档顺序、开关过滤、include_first 扩展、is_pvp 删除；
- `test_tuning_doc.py`：开关行输出；
- UI 测试：keep_pvp 引用清理。

---

## 六、通用判定 + 装备验证两级武器选择 + 规则编辑器导航优化（`9a636f2`）

- 通用判定（judge）与装备验证（equip_judge_dialog）两级武器选择：
  - 通用判定：从规则 YAML `weapons` 表读；
  - 装备验证对话框：下拉选择武器类型（从 attributes.yaml `weapon_types` 读）。
- 规则编辑器导航优化：左侧导航树支持折叠/展开，当前项高亮。

---

## 七、装备部位统一为武器 + 词条部位选择框（`ee09595`）

- 装备部位描述统一为「武器 {weapon}」（如「武器 唐横刀」）而非单独的部位名；
- 词条部位选择框（`AffixSlotPicker`）：主武器/副武器/冠胄/胸甲/胫甲/腕甲/环/佩。

---

## 八、潜力判定重构为填充式（`ea83bf4`）

### 8.1 背景

旧潜力判定基于「已有条数 + 品阶」粗判，无法准确评估「当前 1-4 条词条」的
潜力（是否值得继续调律到 5 条满）。

### 8.2 填充式判定

- 将规则 `patterns.{part}` 的 `first` / `required` / `optional_n` 视为「填空槽」；
- 已有条词逐个匹配填空槽（价值序优先）；
- 剩余空槽数 = 还能调出多少好词条；
- 转律模拟后复用完整定级（`judge()` 直接调用）。

---

## 九、新增江湖号令活动刷新工作流（`8f0d6b6`）+ 合影动作（`cc0651f`）

- `config/system/workflows/jianghu_activity.wf`：江湖号令活动刷新工作流；
- `config/system/workflows/jianghu_photo.wf`：江湖号令合影动作工作流；
- 配套场景配置 `jianghu_activity.yaml`。

---

## 十、滚轮防御回退为原生控件 + 全局 WheelGuard（`b68a0de`）

### 10.1 背景

此前为防滚轮误触，将 QComboBox / QSpinBox 等控件替换为自定义无滚轮版本，
但自定义控件行为不完整（如键盘导航失效）。

### 10.2 改动

- 回退为原生控件；
- 统一走全局 `WheelGuard` 单层防御（eventFilter 拦截滚轮事件，仅当控件获焦
  且鼠标在控件内时放行）；
- 插件化入口补装 guard（`30534d2` 配套）。

---

## 十一、词条库列表默认展示高度按行数固定（`71e2ffa`）

- 转律词条库列表默认展示 7 行（QListWidget.fixedHeight 按 7 行计算）；
- 可用词条库列表默认展示 10 行；
- 避免不同分辨率下列表高度漂移。

---

## 十二、UI 插件化注入架构落地（`30534d2`）

### 12.1 背景

主窗口 `MainWindow` 原本硬编码所有 Tab / 对话框 / 菜单项，新增功能（如
江湖活动入口）需要改主窗口代码。

### 12.2 改动

- 新增 `src/ui/plugins/` 包：
  - `base.py`：`UIPlugin` 抽象基类（`inject(main_window)` 方法）；
  - `registry.py`：插件注册表；
  - `loader.py`：从 `src/apps/yysls/ui/plugins/` 自动加载。
- `MainWindow.__init__()` 末尾调用 `load_plugins(self)`；
- 各插件通过 `inject()` 挂载 Tab / 菜单项 / 热键。

### 12.3 配套

- loguru 文件落盘与崩溃防护安装位置修正（`3c1f4b5`）：
  - 入口迁移到 `src/app.py` 后，loguru 配置与崩溃防护安装需在新入口执行；
  - 修复遗漏。

---

## 十三、调律规则清除具体属攻词条 + 裂石规则更名会心（`0ef8ad2`）

- 调律规则 YAML 清除具体属攻词条（如「最大鸣金攻击」），改回动态词条
  （「最大无相攻击」）——属攻归一化由引擎 `_normalize` 处理；
- 裂石规则 `lieshi_big.yaml` 更名会心规则 `huixin_big.yaml`（历史遗留命名）。

---

## 十四、结果

- pytest 全绿；
- 全部改动已提交并推送至 `origin/master`（最新 `cc0651f`）。

---

## 十五、关键设计决策（用户确认）

1. **废弃 PVP 专用语义**：改为通用开关机制（`switches` + `when`）。
2. **评级四档定名**：垃圾 / 一般 / 优秀 / 顶级（"能用" 改名 "一般"）。
3. **条件原语收敛为 4 个**：contains_all / not_together / count_max / count_min。
4. **潜力判定填充式**：已有条词逐个匹配填空槽，剩余空槽数 = 潜力。
5. **滚轮防御回退**：原生控件 + 全局 WheelGuard 单层防御。
6. **UI 插件化注入**：新增功能不改主窗口代码。

---

## 十六、用户关键指令索引

| 指令 | 影响范围 |
|------|----------|
| 「调律规则开关化重构」 | 方案文档 + tuning_rules/ + judge.py + UI |
| 「四档评级」 | Rating 枚举 + 全库 grep |
| 「条件原语收敛」 | condition_editor.py + generic/ |
| 「潜力判定填充式」 | judge.py + auto_tuning.py |
| 「品阶门槛按部位锁死」 | tuning_base.yaml + generic/ |
| 「江湖号令活动工作流」 | .wf + scenes/*.yaml |
| 「滚轮防御回退」 | WheelGuard + 各 *_panel.py |
| 「UI 插件化注入」 | src/ui/plugins/ + MainWindow |
| 「词条归属分类对话框」 | AffixClassificationDialog |
| 「装备部位统一为武器」 | part_label() + UI 文案 |
| 「裂石规则更名会心」 | tuning_rules/*.yaml |
