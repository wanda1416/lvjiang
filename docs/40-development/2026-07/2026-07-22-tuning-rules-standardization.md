# 开发日志 2026-07-22

> 接续 2026-07-21 项目演进总结，记录其后两轮重构。
> pytest 从 509 → **528 例全绿**，已提交并推送至 `origin/master`。

---

## 一、游戏配置重构与流派配置（2026-07-21）

### 1.1 背景

装备属性管理（attributes.yaml）与调律规则（tuning_rules/*.yaml）是上下游关系：
`_aliases` 是标准词条全称的唯一权威源，调律规则 YAML 里的全称词条必须与之
一致，否则判定静默失配。两者原本各自为政，缺乏统一入口与共享词汇源。

### 1.2 交付

- **新增「游戏配置」对话框**（`AttrManagerDialog`，3 Tab）：
  - Tab1 装备配置（`base_attr_panel.py`）：左侧 8 部位列表；主武器详情页含
    「武器类型」编辑区（标题行 = 标签 + 灰色提示 + 添加/删除按钮，列表
    QListWidget 单独占下方整行 maxHeight 300）；被流派主/副武器引用的武器不可删。
  - Tab2 词条配置（`affix_caps_panel.py`）：未动。
  - Tab3 流派配置（`school_panel.py`，本轮重写为左右分栏）：
    - 左侧：流派列表（可增删、直接编辑重命名）+ 添加/删除按钮；
    - 右侧表单：属性（下拉 鸣金/裂石/破竹/牵丝）、主武器、主武器增效、
      副武器、副武器增效；任一下拉变化即时写盘并刷新 AttrRuleManager 单例；
      showEvent 重载防跨面板脏数据。
- **attributes.yaml 顶层四键**：
  - `base_attrs`（8 部位）、`affix_caps`（15 类别）；
  - `weapon_types`：10 武器注册表，**「唐横刀」为正确名称（非「横刀」）**；
  - `schools`：**新 schema** `流派名 → {attr: 属性, main: {weapon, affix},
    sub: {weapon, affix}}`，预填十大流派（源自 docs/10-game/02-school-system.md
    L7-18）：鸣金·虹、鸣金·影、裂石·威、裂石·钧、牵丝·玉、牵丝·霖、牵丝·翊、
    破竹·风、破竹·尘、破竹·鸢。扇的增效词条是「扇武学增效」（非增伤），
    其余武器为「X武学增伤」。
- **横刀 → 唐横刀 全局改名**（注册表驱动）：constants.py `_DEFAULT_WEAPON_TYPES`
  回退表、attr_rules.py `_TYPE_TO_KEY`、equip_judge_dialog.py `_WEAPON_WUXUE`、
  tuning_rules/huixin_big.yaml 与 huixin_small.yaml 的 weapons 表、
  tests（test_huixin_judge / test_equip_model / test_equip_judge_dialog）。
- **武器类型动态化**：constants.py `WEAPON_TYPES`/`WEAPON_TYPES_SET` = 模块加载时
  读 attributes.yaml 快照（失败回退内置默认）→ **新增武器需重启才参与识别**；
  AttrRuleManager 新增 `get_weapon_types()`/`get_schools()`（reload 后实时生效）。
- **菜单与热键收口**：「装备属性管理」→「游戏配置」（F5）、「装备调律规则」→
  「调律规则」（F6，QAction 窗口级）；全局热键收口为 F8-F10（pynput
  GlobalHotKeys），回调经 `_backend_ready()` 门控；F9 启动共用 `_on_f9_start`
  入口（运行中忽略）。

### 1.3 测试

- `tests/yysls/test_attr_manager_ui.py`：3 Tab 冒烟 + 武器增删往返 + 流派面板
  4 用例（预填 10 流派 / 选中联动表单 / 表单即存 / 增改删往返）；
- `tests/yysls/test_attr_rules.py`：weapon_types/schools 新结构断言 + 绑定合法性校验；
- `tests/ui/test_main_window_hotkeys.py`：热键门控桩测试 4 例（不实例化 MainWindow）。

---

## 二、调律规则标准词条化重构（2026-07-22）

### 2.1 背景

调律规则 YAML 里混用符号（大外/小外/会意/会心/精准/大无相/小无相/小外属）
与标准词条名，且 `variants` 层让 schema 嵌套过深。attributes.yaml 的 `_aliases`
才是标准词条全称的唯一权威源，符号层属于二次映射，易造成静默失配。

### 2.2 三条主线

1. **词条来源收缩**：删除 `SYMBOL_VOCAB`/`SYMBOL_MAP` 符号层，规则 YAML 与 UI
   全部使用标准词条名（来自 attributes.yaml 普通词组 `_aliases` 全集）。保存时
   校验词条名必须在标准全集内。
2. **结构扁平化**：删除 `variants` 层，`transmute_priority / affix_pool /
   optional_pool / junk_rules / patterns` 上提到 YAML 顶层；heal.yaml 拆为
   `heal_pure.yaml`（治疗-纯奶）与 `heal_fire.yaml`（治疗-火拳奶）。
3. **规则可增删**：每个 Tab = 一个 tuning_rule 文件；对话框可新增规则 Tab、
   规则设置页可删除本规则；导航「流派设置」改名「规则设置」。

### 2.3 引擎改动（src/apps/yysls/evaluator/）

- **attr_rules.py**：`AttrRuleManager` 新增 `get_normal_affix_names()`，按 YAML
  声明顺序返回所有非定音词组的 `_aliases` 并集；成为调律规则校验与 UI 候选的
  唯一来源。
- **rules.py**：删除 `SYMBOL_VOCAB`/`SYMBOL_MAP`/`RuleVariant`；`_check_symbols`
  /`_parse_condition` 改为校验「标准词条全集（+槽位处的 DMG）」，越界名报
  `RuleValidationError`；`parse_school_rule` 读顶层字段；放宽校验允许空
  `patterns`/`affix_pool`（新建规则的空骨架可保存）；`TuningRuleManager` 新增
  `create_rule(key, name)`（写最小骨架 YAML + reload）与 `delete_rule(key)`
  （删文件 + reload）；模块级 `standard_affix_names()` 包装。
- **generic.py**：`_normalize` 简化为仅做属攻归一化（非武器且属名在 own_attrs
  → 最大/最小无相攻击），删除符号映射与 小外属 分支；**错位属攻加 `(错位)` 标记**
  （武器上流派属攻、非武器字面无相攻击 → 必然落在词条库外 → 判垃圾），保持旧
  语义等价；`allowed_divine` = 必选槽候选并集 ∪ req_damage ∪ keep_pvp 扩展；
  删除 `_active_variants` 与变体遍历；`_build_attempts` 直接用 `rule.patterns`。

### 2.4 规则 YAML 迁移（config/system/yysls/tuning_rules/）

- `huiyi_general.yaml`/`huixin_big.yaml`/`huixin_small.yaml`：variants.default
  上提扁平化 + 全部符号替换为标准名（对照 docs/10-game/10-tuning-rules 校对
  huixin_small 的 小外属 展开为 4 个具体 `最小X攻击` 标准名）。
- 删除 `heal.yaml`，新建 `heal_pure.yaml`（key heal_pure，name 治疗-纯奶，
  order 40）与 `heal_fire.yaml`（key heal_fire，name 治疗-火拳奶，order 41）：
  `needs_sub_school: false`、无 sub_schools、own_attr 牵丝、weapons 沿用
  （扇:扇武学增效 / 副 伞），patterns 分别取原 pure/fire 变体。
- **语义约定**：规则中写 `最大无相攻击 / 最小无相攻击`：判定时装备上的本流派属攻
  （own_attr 展开，如鸣金流的 最大鸣金攻击）归一化为对应无相词条后匹配。武器
  部位属攻不归一化（保留全称，即错位判垃圾），与现行为一致。

### 2.5 UI 改动（src/apps/yysls/ui/tuning_rules/）

- **condition_editor.py**：删除 `SYMBOL_ORDER`/`DIVINE_CANDIDATES`；
  `SymbolPickerDialog` 改名 `AffixPickerDialog`（候选=标准词条名，数量 ~40，
  改为带滚动的多列复选网格，_COLS=3，minWidth 520 / scroll minHeight 320）；
  `ConditionEditor`/`_ConditionRow` 候选改用标准全集（构造时注入）。
- **variant_pool_page.py → pool_page.py**：`_SymbolGrid` checkbox 网格删除；
  可用词条库、可选槽词条库改为「已选词条列表 + 添加（弹 AffixPickerDialog）/
  移除」；转律优先级沿用列表+上移/下移，添加改用标准词条候选；`load()` 直接
  读顶层 dict。
- **part_pattern_page.py**：首词条 checkbox 行改为 与必选槽一致的「点击选择」
  按钮 + AffixPickerDialog；`_SLOT_CANDIDATES` = [DMG] + 标准全集；增伤要求
  下拉 = （无）/DMG/标准全集；PVP 顶替与允许神力候选同标准全集；数据路径从
  variant 子树改为顶层 `patterns.<part>`。
- **school_rule_panel.py**：删除变体 QTabBar 及 `_current_variant`；导航第一项
  改「规则设置」；pool/part 页 load 传顶层 `self._data`；构造时取标准全集注入
  PoolPage/PartPatternPage/ConditionEditor；新增 `rule_key` property 与
  `on_delete` 回调转发。
- **school_settings_page.py**：标题与标签改「规则设置」语义（流派名称→规则名称）；
  新增「删除本规则」按钮（红字样式，QMessageBox.question 确认后回调）。
- **rules_dialog.py**：`_NewRuleDialog` 输入 key（英文标识）与名称，本地 `_KEY_RE`
  校验；Tab 角落区改为容器（「＋ 新增规则」+「装备调律验证」）；新增规则弹小
  对话框输入 key 与名称，`create_rule` 后追加 Tab；删除规则按 `panel.rule_key`
  找 tab 移除。

### 2.6 测试更新

- `tests/yysls/test_rule_loader.py`：新扁平 schema、标准名校验、create/delete
  测试、`standard_affix_names()` 守护全部规则字段。
- `tests/yysls/test_heal_judge.py`：heal → heal_pure/heal_fire 两规则独立判定，
  `TestVariantSelection` → `TestIndependentRules`。
- `tests/yysls/test_huiyi_judge.py`：流派注册表/实现标志/配置声明改
  heal_pure/heal_fire；断言文案 `会心率/精准率`、`治疗-纯奶`。
- `tests/yysls/test_tuning_rules_ui.py`：Tab 数改 5、角落容器双按钮断言、
  Roundtrip 参数化改 5 条扁平规则、首词条改 `page._first`、新增删除回调转发测试。
- `tests/yysls/test_equip_judge_dialog.py`：heal 三测试改写为两条独立规则
  （无子流派配置项、各自启用往返）。
- **错位属攻回归修复**：全量测试暴露 `_normalize` 简化后错位属攻命中新 pool
  里的标准名而不再判垃圾的回归，在 generic._normalize 恢复旧语义：错位属攻加
  `(错位)` 标记使其必然落在词条库外 → 判垃圾。

### 2.7 结果

- pytest 528 例全绿（yysls 342 例 + 全仓 528 例）。
- 全部改动已提交并推送至 `origin/master`。

---

## 三、文档清理

- `TODO.md`：第 1、2 项（运行时校验打通、UI 词条候选动态化）标记为已完成并
  删除，保留剩余两项待办（交互互通、weapons 表引用流派配置）。
- `PROGRESS.md`：删除「本轮完成内容」详细章节（详情已迁入本日志与
  `2026-07-21-project-evolution-summary.md`），删除「未提交文件清单」章节，
  「下一步」收敛为 TODO.md 引用。

---

## 四、关键设计决策（用户确认）

1. **无相自动兼收本属**：规则里的 `最大/最小无相攻击` 在判定时自动兼收本流派
   属攻（own_attr 展开），属攻归一化仅在非武器部位进行。
2. **删除变体层**：variants 层删除，字段上提 YAML 顶层；含 "variants" 键直接
   报错。
3. **heal 拆分**：heal.yaml 拆为 heal_pure/heal_fire 两条独立规则，原「纯奶/
   火拳按勾选激活取最优」由用户在两个规则间选择替代；用户配置中旧的 heal
   勾选项失效即失效，不做迁移兼容。
4. **错位属攻显式标记**：武器上流派属攻、非武器字面无相攻击在 `_normalize`
   中加 `(错位)` 标记，使其必然落在词条库外 → 判垃圾，保持旧语义等价。
5. **不写迁移兼容代码**：开发期配置重构旧 schema 直接废弃。

---

## 五、用户关键指令索引

| 指令 | 影响范围 |
|------|----------|
| 「调律规则标准词条化重构」 | 全规则引擎 + UI + YAML 迁移 |
| 「删除变体层」 | rules.py/generic.py/全部 YAML/UI |
| 「heal 拆为两条独立规则」 | heal_pure.yaml/heal_fire.yaml |
| 「导航『流派设置』改『规则设置』」 | school_rule_panel.py/school_settings_page.py |
| 「UI checkbox 网格改列表 + 弹窗选择」 | variant_pool_page.py → pool_page.py + AffixPickerDialog |
| 「规则可新建/删除」 | rules_dialog.py + TuningRuleManager.create_rule/delete_rule |
| 「横刀 → 唐横刀 全局改名」 | constants.py/attr_rules.py/equip_judge_dialog.py/多个 YAML/tests |
| 「武器类型动态化」 | constants.py/AttrRuleManager |
| 「新增游戏配置对话框（3 Tab）」 | attr_dialog.py/attr_tab.py/base_attr_panel.py/affix_caps_panel.py/school_panel.py |
| 「菜单与热键收口（F5/F6/F8-F10）」 | main_window.py |
