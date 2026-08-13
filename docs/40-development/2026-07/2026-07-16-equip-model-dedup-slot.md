# Dev Log: 2026-07-16 装备模型去 slot 依赖全链路重构

> 日期：2026-07-16（对应提交 `42f8294`；文件名原误标为 07-14，已修正）
> 涉及模块：`lvjiang/equip_parser/`、`lvjiang/evaluator/`、`lvjiang/workflows/builtins.py`、`config/system/rules/鸣金虹.yaml`、`config/system/workflows/single_tuning.wf`、`tests/`
> 关键词：去 slot 化、type-based、infer_category、EquipmentData、equipment_parser、DSL 条件分支

---

## 一、背景与问题

### 1.1 装备分析流程所有 slot 都是 head

跑装备分析流程后发现，输出 JSON 中所有装备的 `slot` 都是 `"head"`，`key` 也都是 `"head"`。

**根因**：`_CATEGORY_REPR_SLOT` 将所有防具映射到 `"head"`、首饰映射到 `"ring"`、武器映射到 `"main_weapon"`。`equipment_parser` 通过 `_infer_equip_category` 推断类别后，用这个映射选出一个代表槽位作为 key，导致同类型装备全部坍缩到同一个 key。

### 1.2 设计层面的问题

更根本的问题在于：**装备领域模型不应该包含 slot 字段**。

装备的 `type`（剑/枪/冠胄/胸甲/环/佩...）已经隐含了一切分类信息：
- 从 type 可推断 weapon / jewelry / armor 类别
- 从 type + 基础属性值可推断品阶（如胸甲 + 气血 13758 → 100 阶金色装备）
- slot 是外部工作流的概念（变量名），不是装备自身的属性

---

## 二、设计方案

### 2.1 核心原则

1. **EquipmentData 自包含**：不含 slot，parser 从装备自身 type 推断一切
2. **type 决定分类**：`infer_category(equip_type)` 从 type 推断 weapon/jewelry/armor
3. **工作流变量名即 key**：`main_weapon`、`ring` 等是工作流变量名，不是装备属性

### 2.2 数据流

```
OCR raw data → equipment_parser.parse(raw) → EquipmentData(type="剑", ...)
                                                ↓
工作流: eval main_weapon = equipment_parser([last_scan])
                                                ↓
collect [main_weapon] → {"main_weapon": <EquipmentData>}
```

---

## 三、本日完成

### 3.1 constants.py — type-based 分类常量

新增 `WEAPON_TYPES_SET`、`JEWELRY_TYPES_SET`、`ARMOR_TYPES_SET`（中文类型名集合），新增 `infer_category(equip_type)` 函数。

旧的 `WEAPON_SLOTS` / `JEWELRY_SLOTS` / `ARMOR_SLOTS` 保留但已无消费侧引用。

### 3.2 models.py — EquipmentData 去 slot

- 移除 `slot: str` 字段
- 新增 `category` property（调用 `infer_category(self.type)`）
- `to_dict()` / `from_dict()` 不再读写 slot

### 3.3 parser.py — parse 方法重构

- `parse_slot(slot_key, raw)` → `parse(raw)`，去掉 slot_key 参数
- 内部用 `infer_category(equip_type)` 决定 base_attr 解析路径
- 保留 `parse_slot` 为向后兼容别名
- 删除 `_infer_type_from_slot()` 方法

### 3.4 builtins.py — equipment_parser 简化

- 删除 `_CATEGORY_REPR_SLOT` 和 `_infer_equip_category`
- `equipment_parser` 直接返回 `parser.parse(raw_data)` 的 EquipmentData 对象

### 3.5 评估器全链路 slot → type/category

| 文件 | 变更 |
|------|------|
| `evaluator/base.py` | `to_dict()` 移除 `"slot"` 序列化 |
| `evaluator/generic_evaluator.py` | `equip.slot in WEAPON_SLOTS` → `equip.category == "weapon"`，`get_first_affix(equip.slot)` → `get_first_affix(equip.type)` |
| `evaluator/ming_hong.py` | 首词条字典从 slot-keyed 改为 type-keyed，所有 slot 判断改为 type/category |
| `evaluator/rule_config.py` | `_parse_divine_affix` 支持 `match.type`（兼容 `match.slot`） |
| `evaluator/equip_attrs.py` | `_SLOT_TO_KEY` → `_TYPE_TO_KEY`（中文类型名映射），`infer_quality(slot→equip_type)` |

### 3.6 规则配置 YAML

`鸣金虹.yaml`：
- `first_affix` 从 slot-keyed（main_weapon/ring/head...）改为 type-keyed（剑/枪/环/冠胄...）
- `divine_affixes` 从 `match: { slot: [ring, pendant] }` 改为独立 `match: { type: 环 }` 和 `match: { type: 佩 }`

### 3.7 single_tuning.wf — DSL 条件分支

将"判断当前页面并导航"的伪代码注释替换为正式 DSL：

```
scan [bag_equip_detail].[sub_equip]
if not [last_scan].sub_equip contains "装备"
    click [bag_equip_detail].[training]
    wait step_interval
end
```

### 3.8 测试文件同步更新

- `test_diaodiaolan.py`：`WEAPON_SLOTS` → `WEAPON_TYPES_SET`，`infer_quality(slot→equip.type)`
- `test_generic_evaluator.py`：`make_equip` 去掉 slot 参数，所有调用点适配
- 全部测试通过（25/25）

---

## 四、关键技术决策

### 4.1 为什么不用 slot 推断基础属性

slot 是工作流层面的概念（变量名），装备领域模型不应感知。装备的 type（如"胸甲"）加上基础属性值（如气血最大值 13758），本身就足以推断品阶和校验 OCR 正确性。引入 slot 作为中间层既无信息增益，又造成所有防具坍缩到同一 key 的 bug。

### 4.2 category property 而非字段

`EquipmentData.category` 设计为 computed property 而非存储字段，保证 type 和 category 始终一致，无需额外同步逻辑。

---

## 五、文件变更汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `lvjiang/equip_parser/constants.py` | 修改 | 新增 type-based 分类集合和 `infer_category()` |
| `lvjiang/equip_parser/models.py` | 修改 | EquipmentData 移除 slot，新增 category property |
| `lvjiang/equip_parser/parser.py` | 修改 | `parse_slot` → `parse`，去掉 slot_key 参数 |
| `lvjiang/workflows/builtins.py` | 修改 | equipment_parser 直接返回 EquipmentData |
| `lvjiang/evaluator/base.py` | 修改 | to_dict 移除 slot 序列化 |
| `lvjiang/evaluator/generic_evaluator.py` | 修改 | slot → type/category |
| `lvjiang/evaluator/ming_hong.py` | 修改 | 首词条字典 type-keyed，slot → type/category |
| `lvjiang/evaluator/rule_config.py` | 修改 | 神力规则支持 match.type |
| `lvjiang/evaluator/equip_attrs.py` | 修改 | `_SLOT_TO_KEY` → `_TYPE_TO_KEY`，infer_quality 参数改 equip_type |
| `config/system/rules/鸣金虹.yaml` | 修改 | first_affix / divine_affixes 改为 type-keyed |
| `config/system/workflows/single_tuning.wf` | 修改 | 培养页判断改为 DSL if 条件分支 |
| `tests/test_diaodiaolan.py` | 修改 | slot → type-based |
| `tests/test_generic_evaluator.py` | 修改 | make_equip 去 slot 参数 |
