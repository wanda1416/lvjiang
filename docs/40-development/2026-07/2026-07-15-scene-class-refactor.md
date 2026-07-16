# Dev Log: 装备场景类化重构与功能按钮标记

> 日期：2026-07-15
> 涉及模块：`lvjiang/core/region_config.py`、`lvjiang/ui/region_editor/canvas.py`、`lvjiang/workflows/equip_analysis.py`
> 关键词：场景类化、字段继承、功能按钮标记、OCR跳过、区域复制修复

---

## 变更概览

| 类别 | 变更内容 |
|------|----------|
| 架构重构 | FIELD_GROUPS 字典 → 场景定义类继承体系 |
| 新增场景 | `equip_tune_result`（装备调律结果，2 字段） |
| 字段扩展 | `equip_weapon_detail` 新增主功能/更多功能/次功能1-4 |
| 字段扩展 | `equip_armor_detail` 新增主功能/更多功能/次功能1-4 |
| 字段扩展 | `equip_tune_detail` 新增一键添加/材料格1-7/调律 |
| 新增机制 | `button_fields()` 标记纯功能按钮（OCR 可跳过） |
| 工作流优化 | `equip_analysis` 跳过功能字段，只识别装备属性 |
| Bug 修复 | 区域复制时所有字段已绑定导致 IndexError |

---

## 场景类化重构

### 重构前

```python
FIELD_GROUPS = {
    "equip_weapon_detail": ("装备武器详情", [...13个字段...]),
    "equip_armor_detail": ("装备防具详情", [...14个字段...]),
    # 大量重复字段定义
}
```

### 重构后

```python
class SceneDef:
    """场景定义基类"""
    key: str
    name: str
    
    @classmethod
    def fields(cls) -> list[tuple[str, str]]: ...
    
    @classmethod
    def button_fields(cls) -> set[str]:
        """纯功能按钮字段（OCR 可跳过）"""
        return set()

class EquipDetail(SceneDef):
    """装备详情基类：武器和防具共享的字段"""
    
    @classmethod
    def _common_fields(cls):  # 装备类型、装备等级
    @classmethod
    def _affix_fields(cls):   # 词条宫商角徵羽
    @classmethod
    def _func_fields(cls):    # 主功能、更多功能、次功能1-4

class EquipWeaponDetail(EquipDetail):
    """武器详情：基础属性为单一范围值"""
    # common + base_attr + affix + func = 14 字段

class EquipArmorDetail(EquipDetail):
    """防具详情：基础属性分为气血和防御两项"""
    # common + base_attr_1/2 + affix + func = 15 字段
```

### 继承关系

```
SceneDef (基类)
├── EquipBagDetail          — 10 个槽位字段
├── EquipDetail (抽象基类)
│   ├── EquipWeaponDetail   — 14 字段
│   └── EquipArmorDetail    — 15 字段
├── EquipTuneDetail          — 14 字段
└── EquipTuneResult          — 2 字段
```

---

## 功能按钮标记机制

### 设计思路

- `button_fields()` 标记真正的 UI 按钮，未来可用于识别按钮功能
- 工作流级别的跳过逻辑独立维护，不与全局标记耦合

### 各场景按钮标记

| 场景 | 标记为按钮的字段 |
|------|-----------------|
| `bag_equip_detail` | 全部 10 个槽位（slot_*） |
| `equip_weapon_detail` | `more_func`（更多功能） |
| `equip_armor_detail` | `more_func`（更多功能） |
| `equip_tune_detail` | 无 |
| `equip_tune_result` | `close_btn`（关闭） |

### 工作流专属跳过

```python
# equip_analysis.py
SKIP_FIELDS = {
    "main_func", "more_func",
    "sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4",
}
```

装备属性分析工作流跳过所有功能字段，因为这些字段与装备属性无关。

---

## Bug 修复：区域复制 IndexError

### 问题

当所有字段已绑定时，复制区域会导致 `IndexError: list index out of range`。

### 原因

`_prompt_field_selection()` 在所有字段已分配时会移除新创建的区域，但 `_copy_selected_region()` 仍尝试通过索引访问。

### 修复

```python
# 修复前
if self._regions[new_idx].key:  # 可能越界

# 修复后
if new_idx < len(self._regions) and self._regions[new_idx].key:
```

---

## 最终场景字段统计

| 场景 | 字段数 | 按钮字段 |
|------|--------|----------|
| `bag_equip_detail` | 10 | 全部 10 个 |
| `equip_weapon_detail` | 14 | `more_func` |
| `equip_armor_detail` | 15 | `more_func` |
| `equip_tune_detail` | 14 | 无 |
| `equip_tune_result` | 2 | `close_btn` |
