# 自动调律早期方案与示例归档

> 归档日期：2026-10-09；这是整理日期，不代表原方案的提出或验证日期。
> 以下保留旧方案、排查或维护过程，不能作为当前用户操作或运行契约。

## 1. 背景与目标

自动调律是律匠的核心目标流程，旨在实现「打开背包 → 自动分析 → 自动调律」的完整闭环。

### 1.1 已具备的能力（实现前）

| 能力 | 说明 |
|------|------|
| `equip_analysis.wf` | 扫描身上已穿戴的 8 件装备 |
| `single_tuning.wf` | 对指定背包格装备执行一次调律 |
| `MingHongEvaluator` | 装备评估（品阶/首词条/神力/扣分/评级）+ 调律熔断判断 |
| `navigation.wf` | 导航子流程（含 nav_main_to_equip / nav_equip_to_tune / nav_back_to_main） |
| `AttrRuleManager` | 词条上限查询、品阶推断、词条分类映射（全局单例） |
| `affix_cap` / `chengyin_cap` | DSL 内置函数，查询词条上限 |

### 1.2 缺失的两个关键能力（需求来源）

1. **背包遍历** — 游戏背包远超可见区域，需精确滚动遍历所有装备
2. **调律决策编排** — 基于评估结果决定哪些装备值得调律，并自动执行

---

## 5. 指纹模型

### 5.1 指纹生成

基于装备详情页 OCR 结果，由 `to_equipment()` 解析后生成完整指纹字符串，然后 MD5 取前 8 位十六进制。

```python
def _make_fingerprint(equip: dict) -> str:
    """生成装备指纹，空装备返回空串"""
    if not equip:
        return ""
    parts = [
        str(equip.get("type", "")),
        str(equip.get("level", "")),
        str(equip.get("quality", "") or ""),
        str(equip.get("chengyin", "") or ""),
    ]
    for i in range(1, 6):
        affix = equip.get(f"affix_{i}")
        if isinstance(affix, dict) and affix.get("name"):
            parts.append(f"{affix['name']}:{affix.get('value', '')}")
    raw = "+".join(parts)
    return hashlib.md5(raw.encode()).hexdigest()[:8]
```

### 5.2 指纹存储

```python
# context（运行期临时状态，不持久化）
context.bag_fingerprints = {
    "r1c1": "a3f5b2c1",   # MD5 前 8 位 hex
    "r1c2": "e7d9f4a0",
    ...
}
```

- **key** = `r{row}c{col}`（屏幕物理位置）
- **value** = 指纹字符串（MD5 前 8 位 hex）
- 每次滚动后，新行覆盖旧 slot_key 的指纹

---

 装备锁定检测

> **新增功能**（v0.1.2）：回收确认弹窗中未识别到「确认」字样时，判定装备被锁定，关闭弹窗返回，避免卡死。

```python
# 装备锁定检测：确认弹窗内应含「确认」，否则装备被锁定
confirm_text = wf.ocr_scene(wf.EQUIP_DETAIL, ["recycle_confirm"]).get(
    "recycle_confirm", "") or ""
if "确认" not in confirm_text:
    logger.warning(f"回收确认弹窗未识别到「确认」，装备被锁定，保留")
    wf.click_region(wf.EQUIP_DETAIL, "more_func")
    return False
```

---

## 旧遍历方案回顾

早期固定 18 格 region 与单一位置对齐方案后来改为 Panel 和两类遍历策略。旧指纹示例与回收示例不再作为现行实现，现行模型见装备领域模型。
