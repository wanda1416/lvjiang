# PROGRESS —— 游戏配置重构与流派配置（2026-07-21）

> 用途：跨对话进度快照。开启新对话时先读本文件与 TODO.md。

## 一、当前状态

「游戏配置重构与流派配置」计划已**全部完成**，全量 pytest **509 例全绿**。
**所有改动未提交**（见文末 git 状态），等用户 /submit 指令——严禁自动提交。

**2026-07 追加：调律规则标准词条化重构完成**（全量 pytest 528 例全绿，未提交）：
删除 SYMBOL_VOCAB/SYMBOL_MAP 符号层与 variants 层（字段上提 YAML 顶层），
规则词条唯一来源 = `AttrRuleManager.get_normal_affix_names()` 标准全集（越界名拒存）；
heal.yaml 拆为 heal_pure/heal_fire 两条独立规则；规则可新建/删除
（TuningRuleManager.create_rule/delete_rule + 对话框 Tab 增删）；导航「流派设置」
改「规则设置」；UI checkbox 网格改「已选列表 + AffixPickerDialog 添加/移除」
（variant_pool_page.py → pool_page.py）；属攻归一化：非武器本属 → 无相，错位属攻
（武器上流派属攻、非武器字面无相）加 `(错位)` 标记判垃圾（generic._normalize）。

## 二、本轮完成内容

### 1. 菜单与快捷键
- 「装备属性管理」→「游戏配置」（F5）、「装备调律规则」→「调律规则」（F6，QAction 窗口级）；
- 全局热键收口为 F8-F10（pynput GlobalHotKeys），回调经 `_backend_ready()` 门控；
  F9 启动共用 `_on_f9_start` 入口（运行中忽略）。

### 2. 游戏配置对话框（AttrManagerDialog，3 Tab）
- **Tab1 装备配置**（base_attr_panel.py）：左侧「装备类型」8 部位列表；
  主武器详情页含「武器类型」编辑区——标题行 = 标签 + 灰色提示 + 添加/删除按钮（同一行右侧），
  列表 QListWidget 单独占下方整行（maxHeight 300）；被流派主/副武器引用的武器不可删。
- **Tab2 词条配置**（affix_caps_panel.py）：未动。
- **Tab3 流派配置**（school_panel.py，本轮重写为左右分栏）：
  - 左侧：流派列表（可增删、直接编辑重命名）+ 添加/删除按钮；
  - 右侧表单依次为：属性（下拉 鸣金/裂石/破竹/牵丝）、主武器、主武器增效、副武器、副武器增效；
  - 任一下拉变化即时写盘并刷新 AttrRuleManager 单例；showEvent 重载防跨面板脏数据。

### 3. attributes.yaml（config/system/yysls/）顶层四键
- `base_attrs`（8 部位）、`affix_caps`（15 类别）；
- `weapon_types`：10 武器注册表，**「唐横刀」为正确名称（非「横刀」）**；
- `schools`：**新 schema** `流派名 → {attr: 属性, main: {weapon, affix}, sub: {weapon, affix}}`，
  预填十大流派（源自 docs/10-game/02-school-system.md L7-18）：
  鸣金·虹、鸣金·影、裂石·威、裂石·钧、牵丝·玉、牵丝·霖、牵丝·翊、破竹·风、破竹·尘、破竹·鸢。
  扇的增效词条是「扇武学增效」（非增伤），其余武器为「X武学增伤」。

### 4. 横刀 → 唐横刀 全局改名（注册表驱动）
同步了：constants.py `_DEFAULT_WEAPON_TYPES` 回退表、attr_rules.py `_TYPE_TO_KEY`、
equip_judge_dialog.py `_WEAPON_WUXUE`、tuning_rules/huixin_big.yaml 与 huixin_small.yaml 的
weapons 表、tests（test_huixin_judge / test_equip_model / test_equip_judge_dialog）。

### 5. 武器类型动态化
- constants.py `WEAPON_TYPES`/`WEAPON_TYPES_SET` = 模块加载时读 attributes.yaml 快照
  （失败回退内置默认）→ **新增武器需重启才参与识别**；
- AttrRuleManager 新增 `get_weapon_types()`/`get_schools()`（reload 后实时生效）。

### 6. 测试
- tests/yysls/test_attr_manager_ui.py：3 Tab 冒烟 + 武器增删往返 + 流派面板 4 用例
  （预填 10 流派 / 选中联动表单 / 表单即存 / 增改删往返）；
- tests/yysls/test_attr_rules.py：weapon_types/schools 新结构断言 + 绑定合法性校验；
- tests/ui/test_main_window_hotkeys.py：热键门控桩测试 4 例（不实例化 MainWindow）。

## 三、特别注意事项（下一对话必读）

1. **严禁自动提交**；提交时中文信息须写入 UTF-8 文件后 `git commit -F`。
2. 运行环境：用**全局 python**（.venv 缺 yaml）；pytest 加 `-p no:cacheprovider`
   可避开沙箱对 .pytest_cache 的权限报错；PowerShell 写中文用 `python -X utf8`。
3. **SearchReplace 陷阱**：任何含 `───` U+2500 制表线的行做锚点必失败，
   用纯代码锚点或（文件 <1000 行时）Write 整文件重写。
4. **Qt 样式级联坑**：容器 QFrame 的 setStyleSheet 必须用 `QFrame#objectName` 选择器，
   否则级联到 QListWidget/QTableWidget（均继承 QFrame）抹掉其边框背景，看似"布局错乱"。
   排查布局问题先离屏 `widget.grab().save()` 截图确认实际渲染。
5. 三面板（base_attr/affix_caps/school）各自模块级 `_ATTRS_PATH`（相对路径
   `config/system/yysls/attributes.yaml`），全量加载→改动→yaml.dump 全量写盘
   （allow_unicode、sort_keys=False）；UI 测试用 tmp 副本 + monkeypatch 三处 `_ATTRS_PATH`。
6. school_panel 信号重入：`_refresh_list` 中 `clear()` 会嵌套触发 `_on_school_changed`，
   后者用 `prev_loading` 保存/恢复 `_loading` 标志，改动时勿破坏此约定。
7. 用户约定：开发期配置重构**不写迁移兼容代码**（旧 schema 直接废弃）。

## 四、下一步（TODO.md「打通阶段」，未开工，需用户明确发起）

1. 运行时校验打通：TuningRuleManager 校验引入 AttrRuleManager 标准词条全集，失配拒存/告警；
2. 调律规则 UI 词条候选动态化（DIVINE_CANDIDATES/_DAMAGE_OPTIONS 等改从 AttrRuleManager 取）；
3. tuning_rules 各 YAML 的 weapons 表改为引用 schools（当前仍各自硬编码，注意其结构还是
   旧式 `{main: {武器: 词条}, sub: [武器]}`，与 attributes.yaml 新 schema 不同）；
4. 两对话框互挂入口按钮成环。

## 五、未提交文件清单（git status）

修改：attributes.yaml、tuning_rules/huixin_big.yaml、tuning_rules/huixin_small.yaml、
constants.py、attr_rules.py、attr_dialog.py、attr_tab.py、base_attr_panel.py、
equip_judge_dialog.py、src/apps/yysls/ui/main_window.py、rules_dialog.py、
src/ui/main_window.py、test_attr_rules.py、test_equip_judge_dialog.py、
test_equip_model.py、test_huixin_judge.py

新增：TODO.md、src/apps/yysls/ui/attr_manager/school_panel.py、tests/ui/、
tests/yysls/test_attr_manager_ui.py
