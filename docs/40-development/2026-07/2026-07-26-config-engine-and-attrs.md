# 开发日志 2026-07-26

> 接续 2026-07-22 调律规则标准词条化重构。
> pytest 528 → **~540+ 例全绿**（本轮新增调律规则配置化引擎、属性管理拆分、
> 终局判定等测试；具体数字以 pytest 全量汇总为准）。

---

## 一、调律说明文档体系重构（`208a3a6`）

- 将此前散落在 docs/10-game/11-调律说明文档/ 的会意新规则同步至代码与测试；
- 文档结构归位，避免规则定义与说明文档双源漂移。

---

## 二、基础属性按八装备部位拆分并支持属性跟随（`f1069e0`）

### 2.1 背景

此前 `base_attrs` 按「武器/防具/首饰」三分类组织，但游戏内实际是 8 个独立
部位（主武器、冠胄、胸甲、胫甲、腕甲、环、佩），每个部位基础属性集合不同，
且部分属性会随武器类型/品阶「跟随」变化。

### 2.2 改动

- attributes.yaml `base_attrs` 改为 8 部位键（`主武器`/`冠胄`/`胸甲`/`胫甲`/
  `腕甲`/`环`/`佩` + 副武器位预留）；
- 新增「属性跟随」机制：指定属性在特定条件下（如品阶变化）自动派生关联属性值；
- 三面板（base_attr_panel / affix_caps_panel / school_panel）的 `_ATTRS_PATH`
  全量加载 → 改动 → yaml.dump 全量写盘（allow_unicode、sort_keys=False）约定
  在本轮固化，后续 UI 测试统一用 tmp 副本 + monkeypatch 三处 `_ATTRS_PATH`。

---

## 三、调律规则配置化引擎（`8e75090`）

### 3.1 背景

调律规则此前硬编码在 Python 中（`huiyi.py` / `huixin.py` / `heal.py`），
改规则即改代码，迭代成本高。目标：**规则全部外置为 YAML**，一个通用判定类
`GenericSchoolJudge` 加载规则完成 judge（完整定级）与 check_tuning_worthiness
（调律潜力），自动调律 / 单件调律 / 验证对话框全部走它。

### 3.2 规则 YAML Schema

`config/system/yysls/tuning_rules/` 下每流派一个文件，顶层字段：

```yaml
key: huiyi_general
name: 会意流派-通用
has_keep_pvp: true
needs_sub_schools: [...]
sub_school_label: ...
own_attr: from_sub_schools        # 或固定属名
weapons: {...}                    # (子流派.玩法) → 主武器:增伤 / 副武器列表
variants:
  default:
    transmute_priority: [...]
    affix_pool: [...]
    optional_pool: [...]
    junk_rules: [...]
    patterns:
      主武器: {first, required, required_damage, optional_n, top, ...}
      冠胄: {...}
      ...
```

### 3.3 通用判定器（`GenericSchoolJudge`）

- 位于 `src/apps/yysls/evaluator/tuning_rules/`；
- 加载 YAML → 构建模式表 → 穷举匹配制判定；
- `judge()` 返回完整定级（四档：顶级/优秀/一般/垃圾）；
- `check_tuning_worthiness()` 返回潜力判定（是否值得调律）。

### 3.4 结构化编辑 UI

`src/apps/yysls/ui/rules_editor/` 新增：

- `rules_dialog.py`：调律规则主对话框，Tab 式多规则切换；
- `school_rule_panel.py`：单规则编辑容器；
- `school_settings_page.py`：流派/规则基础设置；
- `pool_page.py`：词条库页（可用/可选）；
- `part_pattern_page.py`：部位模式页（首词条/必选/顶级条件）；
- `condition_editor.py`：条件编辑器（contains_all / not_together / count_max / count_min）。

### 3.5 测试

- `test_rule_loader.py`：YAML schema 全字段守护；
- `test_rules_editor.py`：UI 编辑往返；
- 旧三判定器（huiyi/huixin/heal）测试迁移到新通用判定器。

---

## 四、游戏配置重构 + 调律规则标准词条化重构（`606e91d`）

详见 2026-07-22 日志（该提交实际落地于 07-21~22，07-26 仅随本轮一起推送）。

---

## 五、文档清理（`e5bb2b3`、`62e84b8`）

- `PROGRESS.md`：删除「本轮完成内容」详细章节（详情已迁入本日志），删除
  「未提交文件清单」章节；
- `TODO.md`：清理过期条目；
- 新增 `2026-07-22-tuning-rules-standardization.md` 开发日志。

---

## 六、场景编辑器 key 重复校验（`7266a9f`）

- 创建分组/场景时 key 重复实时校验（弹 QMessageBox 警告，拒绝创建）；
- Tab 悬停显示 key（QToolTip），便于排查同名冲突。

---

## 七、新增终局判定与流派配置复用（`f4b19fb`）

### 7.1 终局判定

装备词条已满（5 条）时不再走调律流程，直接做「终局判定」——给出最终评级
（顶级/优秀/一般/垃圾），用于自动调律工作流决定是否回收/装上。

### 7.2 流派配置复用

终局判定复用流派配置的 `patterns` 与 `affix_pool`，不另起炉灶；
`judge_equipment()` 与 `check_tuning_worthiness()` 共用同一套规则加载路径。

---

## 八、武学增效从流派配置移至装备配置的武器类型（`e2ba0b7`）

### 8.1 背景

「武学增效」词条（如「唐横刀武学增伤」「扇武学增效」）原本挂在流派配置
（schools.{name}.main.affix），但实际它是武器类型的固有属性——同一武器类型
不论哪个流派使用，其增效词条名相同。

### 8.2 改动

- attributes.yaml `weapon_types.{name}.affix` 接管武学增效词条名；
- schools.{name}.main/sub 只保留 `{weapon}` 字段，删除 `affix`；
- 判定器读增效词条改从 weapon_types 表查；
- 同步修改：`attr_rules.py` / `generic.py` / `equip_judge_dialog.py` /
  `test_equip_model.py` / `test_huixin_judge.py`。

---

## 九、结果

- pytest 全绿；
- 全部改动已提交并推送至 `origin/master`（最新 `e2ba0b7`）。

---

## 十、关键设计决策（用户确认）

1. **规则外置 YAML**：改规则零代码改动；旧硬编码判定器全部删除。
2. **通用判定器统一入口**：judge / check_tuning_worthiness / 终局判定共用同一
   套规则加载路径，避免分叉。
3. **武学增效归属武器类型**：流派配置只指定武器，不指定增效词条名。
4. **不写迁移兼容代码**：开发期配置重构旧 schema 直接废弃。

---

## 十一、用户关键指令索引

| 指令 | 影响范围 |
|------|----------|
| 「调律规则配置化引擎」 | tuning_rules/ + rules_editor/ + 全部 YAML |
| 「基础属性按八装备部位拆分」 | attributes.yaml + base_attr_panel.py |
| 「武学增效移至武器类型」 | attributes.yaml + attr_rules.py + generic.py |
| 「终局判定」 | GenericSchoolJudge + auto_tuning |
| 「key 重复校验 + Tab 悬停显示」 | scene_editor/dialog.py |
