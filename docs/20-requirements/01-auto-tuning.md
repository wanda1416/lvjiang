# 自动调律 — 背包遍历与调律决策

> **状态**：已实现（v0.1.2）
> **依赖**：装备分析流程（equip_analysis）、单件调律（single_tuning）、MingHongEvaluator

---

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

## 2. 核心约束

### 2.1 背包格无法直接识别

- 背包格（bag_1_1 ~ bag_3_6）**仅显示装备图标**，无可 OCR 的文字
- 图标高度相似（同类装备外观接近），图像识别也无法可靠区分
- **唯一识别路径**：点击背包格 → 进入装备详情页 → OCR 详情页 → 生成指纹

### 2.2 装备可能被回收

- 调律等操作可能消耗装备，下一件自动补位到该槽位
- 需对该位置重新处理（再次点击进详情识别）

### 2.3 状态存储：context 而非 session

- 装备槽信息是**单次运行内的临时状态**，不应持久化到 session
- 使用 `context.bag_fingerprints` 存储遍历过程中的指纹记录
- 每次工作流执行前自动初始化空 dict

### 2.4 滚动无吸附 —— Panel 机制的根本动因

- 背包滚动纯靠触屏滑动，**游戏没有任何吸附机制**，无法保证滑动后内容精确对齐到整行
- 靠「固定 region + 反复小范围微调对齐」理论可行，但代价高、不可靠
- 因此固定 region-slot 方案在滚动场景下**根本无法确保滑到位** —— 这正是 Panel 存在的理由：
  由引擎在 panel 区域内**自动寻找 slot 的真实 x,y**，滚动停在哪里都能定位

---

## 3. Panel 声明式网格架构

> **旧需求回顾**：原计划使用 18 个固定 region（bag_1_1 ~ bag_3_6）+ region-slot 机制。
> **最终实现**：Panel 作为 Area 的第三种形态，与 regions、points 并存。

### 3.1 核心思路

**问题**：背包滚动无吸附（见 §2.4），固定 region-slot 无法确保滚动到位。

**方案**：声明一个 **Panel** — 覆盖整个网格展示区的容器，声明行列数和间距参数，由引擎在该区域内**图像自校准**找到每个格子的真实坐标。滚动停在任意位置都能定位。

```
固定 region-slot：滚动后内容错位 → 点到两格之间
Panel + 图像自校准：滚动停在哪都能找到 slot 真实中心
```

Panel 的核心能力：
1. **声明式网格** — 只需定义 panel 区域 + cols/rows/span 参数
2. **图像自校准** — 从截图中自动检测格子精确位置（黑边 + 方差分析）
3. **DSL 路由** — `click [scene].[panel][row][col]` 自动定向到该格子中心

### 3.2 Panel 数据结构

#### Scene YAML — 新增 `panels` 段

```yaml
# bag_equip_detail.yaml
panels:
  - key: bag_grid
    name: 背包格区域
    cols: 6
    rows: 3
    h_span: 0.0048    # 列间距（归一化，初始估算，运行时由图像校准覆盖）
    v_span: 0.0064    # 行间距（归一化，初始估算，运行时由图像校准覆盖）
```

- `panels` 与 `regions`、`points` 同级，是 Area 的第三种形态
- Panel 内部不定义子 region — 格子坐标由引擎运行时计算
- `h_span` / `v_span` 为初始估算值，实际运行时通过图像分析自校准

### 3.3 图像自校准 — 方差分析定位

每次进入背包页或滚动后，对 panel 区域截图，通过像素方差分析精确定位每个格子：

```
原理：
  slot 内有装备图标 → 像素变化丰富 → 高方差
  span 是纯色间隔   → 像素几乎不变 → 低方差

算法：
  1. 截取 panel 区域图像
  2. 转灰度图
  3. 计算每行像素方差 → variance_y[h]
  4. 计算每列像素方差 → variance_x[w]
  5. 低方差带 = span，高方差带 = slot
  6. 从方差剖面提取 slot 精确边界 → 算出每个格子中心坐标
```

**关键特征：slot 黑边**

每个 slot 边缘有**非常明显的黑色边框**，这是校准的强信号：
- 黑边与 span 纯色间隔、slot 内部图标都有明确对比，方差/边缘检测都能稳定切带
- 即使 slot 为空（无图标），**黑边仍在** → 空 slot 也能被定位

### 3.4 DSL 访问语法

Panel 内的格子通过 `[panel_key][row][col]` 寻址：

```dsl
# 点击第 2 行第 3 列的格子
click [bag_equip_detail].[bag_grid][2][3]

# 用变量寻址
eval $row = 1
eval $col = 4
click [bag_equip_detail].[bag_grid][$row][$col]
```

---

## 4. 背包遍历策略

> **旧需求回顾**：原计划使用单一的位置对齐 + 三向指纹校验策略。
> **最终实现**：抽象为策略模式，两种策略可按配置切换。

### 4.1 策略架构

```
src/lvjiang/apps/yysls/workflows/implementations/bag_traversal/
├── __init__.py       # 策略注册（TRAVERSALS）与默认策略（DEFAULT_TRAVERSAL）
├── base.py           # BagTraversal 抽象基类
├── dedup.py          # DedupTraversal（新，默认）
└── positional.py     # PositionalTraversal（旧，供回切）
```

### 4.2 DedupTraversal（默认策略）

**核心思路**：滚动后逐行读首列，用「上一轮窗口」的指纹集合去重：重复行跳过、新行处理。指纹只作去重依据、不做位置对齐，OCR 漂移的代价从错位/崩溃降为有界的重复处理（重复判定对已处理装备无副作用）。

**优点**：
- 对 OCR 漂移容忍度高
- 不会因坐标偏差导致崩溃
- 重复处理已调律装备无副作用（词条已满直接跳过）

### 4.3 PositionalTraversal（旧策略）

**核心思路**：位置对齐 + 三向指纹校验 + 小步补滚/回滚纠偏。

**适用场景**：当 Dedup 策略因特殊场景（如装备排序变化）表现不佳时，可回切此策略。

**回切方式**：`config/session/yysls/session.json` 的 tuning 节配 `"scroll_strategy": "positional"`。

### 4.4 到底检测

三个独立的到底信号，任一成立即结束：
1. **空 slot**：新一行 6 格全无内容 → 绝对到底
2. **指纹未变**：滚动后首列指纹与上一轮相同 → 内容未移动
3. **无新内容**：新一行所有格子指纹都在已知集合中 → 无新装备

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

## 6. 调律决策编排 — 状态机三行为点

> **旧需求回顾**：原计划由流派规则驱动流程决策。
> **最终实现**：流派规则定位为**装备预期识别逻辑**（输入装备 → 输出预期评级上限），无流程决策权。流程决策由 `tune_config.yaml` 的 `behavior` 段 + `materials` 段驱动。

### 6.1 扫描处理（behavior.scan，进调律前）

```
对背包中每件装备：

1. 传入规则（运行期勾选的流派规则）判定潜力 → 预期评级上限
2. 预期 ≥ entry_min_rating（进入门槛，默认 excellent）→ 进入调律
3. 未达门槛 → 按处置表决策：
   - 处置表未启用（enabled: false）→ 一律保留
   - 有序条件规则表首条命中 → 动作：回收 / 忽略（保留）/ 调满后回收
   - 无命中 → 默认保留
```

### 6.2 材料处理（materials，每轮调律开始前）

- **大律准石数量检查**：低于基准判材料不足，按配置的不足处理执行：
  - `skip` 跳过该装备：本件结束调律，继续遍历后续装备
  - `abort` 结束全部调律（默认）：阻断，全部退出
  - `ask` 询问是否继续：走 DSL `confirm` 弹窗询问用户，确认继续则本次运行不再检查
- **调律按钮就绪检查**：点击一键添加后扫描调律按钮文字，未变成「调律」说明添加失败，不盲点调律
- **狗粮检查与添加**：按狗粮规则表决定每轮是否添加狗粮

### 6.3 结束处理（behavior.tune，每轮调律结束后）

```
每轮调律结束后：

1. 传入规则口径刷新预期评级（供狗粮决策与说明文档）
2. 有序条件规则表首条命中 → 动作四选一：
   - 继续调律（continue）：进入下一轮
   - 重置调律（reset）：清空首词条以外的全部词条后继续调律
   - 回收（recycle）：退出调律页回背包后执行回收
   - 结束保留（ignore）：结束该件装备的调律，留在背包
3. 无命中默认：未满 = 继续调律；词条满 = 结束保留
4. 词条满为边界条件：continue 规则跳过匹配（不可达）
```

### 6.4 判定语义（judge_scope，四选一，逐规则声明）

每条行为规则自行声明「评级 ≤ 条件用哪个规则集识别」或判定方式：

| 取值 | 语义 |
|------|------|
| `incoming` | 传入规则 = 运行期勾选的流派规则（缺省） |
| `all` | 全部流派规则 |
| `custom` | 自选规则（配 `judge_rules` key 列表） |
| `affix` | 自选词条（`ratings` 改存词条名集合，不跑潜力判定） |

**自选词条（`affix`）语义**：不跑潜力判定，直接按装备词条名匹配。典型场景：跳过带金色数值珍贵词条（如最大外攻）的紫色武器，避免误回收。

---

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

### 7.3 装备锁定检测

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

## 8. 实现架构

### 8.1 模块拆分

```
src/lvjiang/apps/yysls/workflows/implementations/
├── auto_tuning.py          # 编排层（AutoTuningWorkflow）
├── bag_traversal/          # 背包遍历策略
│   ├── base.py             # 抽象基类
│   ├── dedup.py            # 去重策略（默认）
│   └── positional.py       # 位置对齐策略
└── tuning/                 # 调律功能模块
    ├── __init__.py
    ├── judge.py            # TuningJudge: 判定与评级（纯逻辑）
    ├── executor.py         # TuningExecutor: 调律执行（材料检查、狗粮决策）
    ├── navigator.py        # TuningNavigator: 导航（DSL subcall 桥接）
    └── recycler.py         # TuningRecycler: 重置与回收
```

### 8.2 职责分离

| 类 | 职责 | 依赖 |
|----|------|------|
| `AutoTuningWorkflow` | 编排层：部位循环、装备处理主链、行为处置 | 组合引用 tuning/* |
| `TuningJudge` | 判定与评级：潜力判定、期望评级、行为表评级提供者 | 纯逻辑，不依赖 UI |
| `TuningExecutor` | 调律执行：单轮调律、材料检查、狗粮决策、就绪确认 | 通过 wf 引用访问 UI 原语 |
| `TuningNavigator` | 导航：页面跳转、词条收集 | DSL subcall 桥接 |
| `TuningRecycler` | 重置与回收：重置调律（冷却期检查）、装备回收 | 通过 wf 引用访问 UI 原语 |

### 8.3 DSL subcall 桥接

导航逻辑通过 DSL subcall 文件实现，避免 Python 与 DSL 两处重复维护：

```python
# 导航 subcall 文件（统一在 navigation.wf 中定义）
_NAV_FILE = "subcall/navigation.wf"
_NAV_MAIN_TO_EQUIP = (_NAV_FILE, "nav_main_to_equip")
_NAV_EQUIP_TO_TUNE = (_NAV_FILE, "nav_equip_to_tune")
_NAV_BACK_TO_MAIN = (_NAV_FILE, "nav_back_to_main")
```

引擎通过 `load_subcalls()` 加载，`call_subcall()` 桥调用。

---

## 9. 调律说明文档

> **新增功能**：每次运行自动生成可交付阅读的叙事输出。

调律说明文档（`TuningDocWriter`）记录本次运行的完整过程：
- 运行头：用户名、规则配置、部位选择
- 装备节：每件值得调律的装备的判定过程、调律轮次、狗粮策略、结束决策
- 运行小结：成品清单（一般评级及以上的装备）

输出目录：`logs/tuning/{username}/`

---

## 10. 场景配置

### 10.1 bag_equip_detail.yaml

```yaml
# 废弃：18 个 bag_1_1 ~ bag_3_6 region
# 新增：
panels:
  - key: bag_grid
    name: 背包格区域
    cols: 6
    rows: 3
    h_span: 0.0048    # 初始估算，运行时自校准
    v_span: 0.0064    # 初始估算，运行时自校准
```

### 10.2 Layout JSON

```json
{
  "bag_equip_detail": {
    "panels": [
      {
        "key": "bag_grid",
        "x_ratio": 0.6638,
        "y_ratio": 0.5524,
        "w_ratio": 0.2789,
        "h_ratio": 0.3010
      }
    ],
    "arrows": [
      {
        "key": "scroll_down",
        "from_cx_ratio": 0.78,
        "from_cy_ratio": 0.65,
        "to_cx_ratio": 0.78,
        "to_cy_ratio": 0.55
      }
    ]
  }
}
```

---

## 11. 相关文档

| 文档 | 内容 |
|------|------|
| [../10-game/04-tuning-mechanics.md](../10-game/04-tuning-mechanics.md) | 调律机制详细说明（装备分级、流派规则） |
| [../10-game/01-equipment-system.md](../10-game/01-equipment-system.md) | 装备系统介绍 |
| [../30-architecture/35-workflows/01-current-equip-analysis.md](../30-architecture/35-workflows/01-current-equip-analysis.md) | 装备分析流程 |
