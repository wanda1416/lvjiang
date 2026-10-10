# 背包遍历、去重与回收补位

> 最后更新：2026-10-09（基线 v0.13.14）。

Panel 寻址与自校准见 [自动调律架构](02-auto-tuning.md)。指纹字段和生成算法由 [装备模型](../31-models/01-equipment-models.md#六装备指纹-_fp) 定义，遍历层不另定义一份指纹算法。

## 4. 背包遍历策略


### 4.1 策略架构

```
src/lvjiang/apps/yysls/workflows/implementations/bag_traversal/
├── __init__.py       # 策略注册（TRAVERSALS）与默认策略（DEFAULT_TRAVERSAL）
├── base.py           # BagTraversal 抽象基类
├── dedup.py          # DedupTraversal（默认）
├── positional.py     # PositionalTraversal（位置对齐）
└── scrolling.py      # 背包滚动手段策略（精准拖拽 / 后台滚轮）
```

遍历算法与滚动手段是两组正交策略。默认使用 `DragBagScroll`，按网格行高
执行更精准的拖拽；PC 端开启“兼容后台模式”后使用 `WheelBagScroll`，将每次
内容向上移动映射为与 DSL `scroll [bag_equip_detail].[bag_grid] down 1`
完全相同的公共滚轮能力。Android 始终使用拖拽。

### 4.2 DedupTraversal（默认策略）

**核心思路**：滚动后逐行读首列，用「上一轮窗口」的指纹集合去重：重复行跳过、新行处理。指纹只作去重依据、不做位置对齐，OCR 漂移的代价从错位/崩溃降为有界的重复处理（重复判定对已处理装备无副作用）。

**优点**：
- 对 OCR 漂移容忍度高
- 不会因坐标偏差导致崩溃
- 重复处理已调律装备无副作用（词条已满直接跳过）

### 4.3 PositionalTraversal（位置对齐策略）

**核心思路**：位置对齐 + 三向指纹校验 + 小步补滚/回滚纠偏。

**适用场景**：当 Dedup 策略因特殊场景（如装备排序变化）表现不佳时，可回切此策略。

运行选择来自用户调律配置的 scroll_strategy；操作步骤见[调律设置](../../60-userguide/04.06-running-tuning.md)。

### 4.4 到底检测

三个独立的到底信号，任一成立即结束：
1. **空 slot**：新一行 6 格全无内容 → 绝对到底
2. **指纹未变**：滚动后首列指纹与上一轮相同 → 内容未移动
3. **无新内容**：新一行所有格子指纹都在已知集合中 → 无新装备

---

## 运行期指纹状态

指纹用于本轮窗口的去重或位置校验，运行状态不写为用户初始配置。原始与模拟装备身份沿用公共模型。

## 7. 回收处理

### 7.1 场景

装备调律/回收后，该槽位被下一件装备填充。

### 7.2 处理逻辑

```
处理 row=1, col=3（slot_key = r1c3）：
    click [bag_grid][1][3] → 详情页
    执行调律 → 装备被回收
    返回背包 → 该位置现在是新装备

    此时不移动，重新 click [bag_grid][1][3] → 详情页
    识别新装备 → 生成新指纹
    执行计划动作
    ...直到该位置装备不被回收，或决定跳过

    最终：context.bag_fingerprints["r1c3"] = 最终指纹
    移到下一个格子 [bag_grid][1][4]
```
