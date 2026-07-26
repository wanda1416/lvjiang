# PROGRESS —— 跨对话进度快照

> 用途：开启新对话时先读本文件与 TODO.md。

## 一、当前状态

- 「游戏配置重构与流派配置」计划全部完成，pytest **509 例全绿**（2026-07-21）。
- 「调律规则标准词条化重构」完成，pytest **528 例全绿**（2026-07-22）。
- 全部改动已提交并推送至 `origin/master`。
- 严禁自动提交；提交时中文信息须写入 UTF-8 文件后 `git commit -F`。

## 二、最近完成（详见 docs/40-development/2026-07/）

- **游戏配置重构**（2026-07-21）：新增游戏配置对话框（装备/词条/流派 3 Tab）；
  attributes.yaml 顶层新增 `weapon_types` 与 `schools` 键；横刀 → 唐横刀全局改名；
  武器类型动态化（从 attributes.yaml 加载）；菜单与热键收口（F5/F6/F8-F10）。
- **调律规则标准词条化重构**（2026-07-22）：删除 SYMBOL_VOCAB/SYMBOL_MAP 符号层
  与 variants 层（字段上提 YAML 顶层）；规则词条唯一来源 =
  `AttrRuleManager.get_normal_affix_names()` 标准全集（越界名保存拒绝）；
  heal.yaml 拆为 heal_pure/heal_fire 两条独立规则；规则可新建/删除
  （TuningRuleManager.create_rule/delete_rule + 对话框 Tab 增删）；导航「流派设置」
  改「规则设置」；UI checkbox 网格改「已选列表 + AffixPickerDialog 添加/移除」
  （variant_pool_page.py → pool_page.py）；属攻归一化：非武器本属 → 无相，错位属攻
  （武器上流派属攻、非武器字面无相）加 `(错位)` 标记判垃圾（generic._normalize）。

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
