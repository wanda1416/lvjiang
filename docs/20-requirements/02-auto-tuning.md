# 自动调律 — 背包遍历与调律决策

> 状态：方案设计中
> 依赖：装备分析流程（equip_analysis）、单件调律（single_tuning）、鸣金虹评估器

---

## 1. 背景与目标

自动调律是律匠的核心目标流程，旨在实现"打开背包 → 自动分析 → 自动调律"的完整闭环。

当前已具备的能力：
- `equip_analysis.wf` — 扫描身上已穿戴的 8 件装备
- `single_tuning.wf` — 对指定背包格装备执行一次调律
- `MingHongEvaluator` — 装备评估（品阶/首词条/神力/扣分/评级）+ 调律熔断判断
- `nav_main_to_equip.wf` / `nav_equip_to_tune.wf` — 导航子流程
- `AttrRuleManager` — 词条上限查询、品阶推断、词条分类映射（全局单例）
- `affix_cap` / `chengyin_cap` — DSL 内置函数，查询词条上限

缺失的两个关键能力：
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
- context 目前缺少这些字段是**待补充项**（本次改动新增），不是障碍

### 2.4 滚动无吸附 —— Panel 机制的根本动因

- 背包滚动纯靠触屏滑动，**游戏没有任何吸附机制**，无法保证滑动后内容精确对齐到整行
- 靠「固定 region + 反复小范围微调对齐」理论可行，但代价高、不可靠
- 因此固定 region-slot 方案在滚动场景下**根本无法确保滑到位** —— 这正是 Panel 存在的理由：
  由引擎在 panel 区域内**自动寻找 slot 的真实 x,y**，滚动停在哪里都能定位

### 2.5 Panel 与 region-slot 共存

- Panel 是 Area 的**新增第三形态**，与现有 region-slot、point **并存**，不是替换
- 装备栏（main_weapon 等固定单格）继续用 region-slot；背包网格用 panel
- 本项目**未对外发布，无需前向兼容**：迁移过程破坏 `single_tuning.wf` 等旧 wf 可接受，后续同步改造即可

---

## 3. Panel 声明式网格架构

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

> slot 无文字，`scan/recognize` 对 panel 格子无意义；panel 只服务 `click`（及内部校准）。

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

#### Layout JSON — panel 绑定为一个 region

```json
{
  "key": "bag_grid",
  "x_ratio": 0.6638,
  "y_ratio": 0.5524,
  "w_ratio": 0.2789,
  "h_ratio": 0.3010
}
```

只需一个矩形区域，覆盖整个 3×6 网格可见范围。

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

```
示意（垂直方向方差剖面）：

y=0   ████  ← 顶部 UI（高方差）
y=10  ░░░░  ← span（低方差）  ← 边界
y=15  ████  ← slot row 1（高方差）
y=80  ████
y=90  ░░░░  ← span（低方差）
y=95  ████  ← slot row 2（高方差）
y=160 ████
y=170 ░░░░  ← span（低方差）
y=175 ████  ← slot row 3（高方差）
y=240 ████
y=250 ░░░░  ← span（低方差）  ← 边界
```

```python
def detect_grid(image: np.ndarray, expected_rows=3, expected_cols=6):
    """从 panel 截图中检测网格槽位精确位置
    
    Returns:
        slot_centers: list[(cx_ratio, cy_ratio)]  # 相对于 panel 区域
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    row_var = np.var(gray, axis=1)  # 每行像素方差
    col_var = np.var(gray, axis=0)  # 每列像素方差
    
    row_bands = find_high_variance_bands(row_var, expected_rows)
    col_bands = find_high_variance_bands(col_var, expected_cols)
    
    centers = []
    for r in row_bands:
        for c in col_bands:
            centers.append((c.center, r.center))
    return centers
```

**关键特征：slot 黑边**

每个 slot 边缘有**非常明显的黑色边框**，这是校准的强信号：
- 黑边与 span 纯色间隔、slot 内部图标都有明确对比，方差/边缘检测都能稳定切带
- 即使 slot 为空（无图标），**黑边仍在** → 空 slot 也能被定位

**空槽与到底的识别逻辑**

```
本行若至少有一个非空 slot：
  → 有非空 slot 的图标变化 + 各 slot 黑边 → 整行 6 个格子边界都能推出
  → 空 slot 照常定位，OCR 详情为空即可判定该格为空

本行 6 个 slot 全部为空：
  → 说明已越过最后一件装备 → 判定到底（见 §4.3）
```

**自校准的好处**：
- 不依赖硬编码坐标，任何分辨率/设备自动适配
- 滚动后重新检测，格子位置自动修正
- 滚动停在任意位置都无所谓，重新检测后自动纠正

### 3.4 DSL 访问语法

Panel 内的格子通过 `[panel_key][row][col]` 寻址：

```dsl
# 点击第 2 行第 3 列的格子
click [bag_equip_detail].[bag_grid][2][3]

# 识别第 1 行第 1 列的格子（虽然无文字，但可作为点击目标）
# 实际上 slot 不需要 scan，点击后进入详情页才有文字

# 用变量寻址
eval $row = 1
eval $col = 4
click [bag_equip_detail].[bag_grid][$row][$col]
```

引擎内部处理流程：
```
1. 解析 [bag_grid][2][3] → 找到 bag_grid panel
2. 查询 panel 的校准缓存 → 获取 row=2, col=3 的中心坐标
3. 如果缓存不存在 → 截图 panel 区域 → 运行方差分析 → 缓存结果
4. 将坐标转换为屏幕绝对坐标 → 执行 click
```

---

## 4. 遍历策略 — Panel + 重叠滚动

### 4.1 核心算法

```
初始化 context.bag_fingerprints = {}   # slot_key -> 指纹
初始化 context.bag_candidates = []      # 候选装备列表

从第 1 行开始，当前可见行 = [1, 2, 3]

循环处理当前可见的 3 行：
    # 每次处理前先对齐 panel（截图 + 方差分析）
    align [bag_equip_detail].[bag_grid]
    
    for row = 1 to 3:
        for col = 1 to 6:
            slot_key = concat("r", $row, "c", $col)
            
            1. click [bag_equip_detail].[bag_grid][row][col]
               → 进入装备详情页
            2. OCR 详情页 → to_equipment() → 生成指纹
            3. 执行计划动作（评估/调律等）
            4. 如果装备被回收：该位置被下一件填充，
               回到步骤 1 重新处理该位置
            5. 最终确定该位置装备后：
               生成指纹，写入 context.bag_fingerprints[slot_key]
            6. 返回背包页

    处理完当前 3 行后，滚动一行：
        drag [bag_equip_detail].[scroll_down]
        wait step_interval
        
        # 滚动后重新对齐 panel → 自动检测新行位置
        # 旧行 2 → 新行 1（已处理，跳过）
        # 旧行 3 → 新行 2（已处理，跳过）
        # 新行 3 → 全新内容（需要处理）

    检查新行 3 的 6 个格子是否到底（见 4.3）
```

### 4.2 重叠滚动与滚动管理器

滚动管理器的详细算法设计见 [02-auto-tuning.md](../30-architecture/35-workflows/02-auto-tuning.md)。

核心职责：
1. 记录每行第一列（col=1）的装备指纹，形成有序序列
2. 滚动后通过比对 `grid[1][1]` 的新指纹与已知序列，判定偏移量
3. 校验通过后推进状态（移除已滚出的行指纹）

**状态结构**（存储在 context 中）：

```python
context._scroll_manager = {
    "row_fps": [],          # 有序列表：每行 col=1 的指纹 [fp_row1, fp_row2, fp_row3]
    "fingerprints": {},     # 集合：所有已处理装备的指纹（用于快速查找）
    "scroll_count": 0,      # 已滚动次数
}
```

### 4.3 到底检测

三个独立的到底信号，任一成立即结束。详细判定逻辑见 [02-auto-tuning.md](../30-architecture/35-workflows/02-auto-tuning.md)。

---

## 5. 指纹模型

指纹生成与存储的详细设计见 [02-auto-tuning.md](../30-architecture/35-workflows/02-auto-tuning.md)。

### 5.1 指纹生成

基于装备详情页 OCR 结果，由 `to_equipment()` 解析后生成完整指纹字符串，然后 MD5 取前 8 位十六进制。

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

### 5.3 滚动管理器状态

滚动管理器维护的状态也存储在 context 中（详见 §4.2）：

```python
context._scroll_manager = {
    "row_fps": [],          # 有序列表：每行 col=1 的指纹
    "fingerprints": {},     # 集合：所有已处理装备的指纹
    "scroll_count": 0,      # 已滚动次数
}
```

### 5.4 内置函数

| 函数 | 用途 |
|---|---|
| `make_fingerprint($equip)` | 基于装备数据生成去重指纹（MD5 前 8 位 hex） |
| `check_scroll($fp)` | 滚动校验：比对指纹与滚动管理器预期，返回偏移量 |
| `notify_scroll($col, $row, $fp)` | 记录已处理装备的指纹到滚动管理器 |
| `scroll_advance()` | 滚动校验通过后，推进状态：移除已滚出的行指纹 |

---

## 6. 回收处理

### 6.1 场景

装备调律/回收后，该槽位被下一件装备填充。

### 6.2 处理逻辑

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

### 6.3 DSL 实现要点

```dsl
@slot_start
click [bag_equip_detail].[bag_grid][$row][$col]
wait step_interval
scan [equip_weapon_detail].[...] as $equip_scan
eval $equip = to_equipment($equip_scan)
eval $fp = make_fingerprint($equip)

# 执行计划动作（如调律）
# ...调律操作可能导致装备被回收...

click [bag_equip_detail].[back]
wait step_interval

# 回收检测：重新点击检查该位置是否还是同一件
# 如果指纹不同 → 回收发生了 → goto slot_start 重新处理
# 如果指纹相同 → 处理下一个格子
```

---

## 7. 调律决策编排

### 7.1 决策流程

```
对背包中每件装备：

1. 品阶检查
   - 武器/首饰：非金色 → 跳过
   - 防具：非紫/金色 → 跳过

2. 首词条检查（初始词条，不可更改）
   - 武器：最大外功攻击 / 势
   - 首饰：最大外功攻击
   - 冠胄/胸甲：会意率
   - 胫甲/腕甲：劲
   → 不符合 → 跳过

3. 神力词条检查
   - 剑：必须有剑武学增伤
   - 枪：不能有枪武学增伤
   - 首饰：必须有全武学增效
   - 胫甲/腕甲：必须有对首领单位增伤
   → 不符合 → 跳过

4. 评分（对通过 1-3 的装备）
   - 传家宝（0 扣分）→ 已完美，无需调律
   - 合格（≤1 扣分）→ 可选调律（提升空间小）
   - 凑合（≤2 扣分）→ 建议调律
   - 垃圾（>2 扣分）→ 跳过

5. 对选中装备执行 single_tuning 流程
```

### 7.2 调律后处理

所有装备均留在背包，不做自动处理：
- 调律后评级提升 → 保留，日志标记"调律成功"
- 调律后仍一般 → 保留，日志标记"可后续转律"
- 调律后变差 → 保留，日志标记"建议手动处理"

> 注：用户后续可手动决定回收/承音/传律等操作。

### 7.3 评估集成

`MingHongEvaluator` 已实现完整评估逻辑：
- `evaluate()` — 完整装备评级
- `check_tuning_worthiness()` — 调律过程中实时熔断判断

需要将其封装为 DSL 内置函数，使工作流可以调用：
```dsl
eval $result = evaluate($equip)
if $result.rating == "合格装备"
    # 执行调律
end
```

---

## 8. DSL 工作流草案

### 8.1 遍历子工作流 `subcall/bag_traverse.wf`

```dsl
#% name: 背包遍历（单个装备部位）

# 初始化 context
eval context.bag_fingerprints = {}
eval $detail_scene = context.equip_scene

# ── 第一步：处理初始可见 3 行 ──
align [bag_equip_detail].[bag_grid]
for r in [1...3]
    for c in [1...6]
        eval context._current_row = $r
        eval context._current_col = $c
        call "bag_process_slot.wf"
    end
end

# 记录三行指纹（col=1）
eval $fp_r1 = context.bag_fingerprints."r1c1"
eval $fp_r2 = context.bag_fingerprints."r2c1"
eval $fp_r3 = context.bag_fingerprints."r3c1"

# ── 第二步：滚动循环 ──
@scroll_loop
drag [bag_equip_detail].[bag_grid][2][3] up hold 0.3
wait step_interval

# 读取 grid[1][1]
align [bag_equip_detail].[bag_grid]
click [bag_equip_detail].[bag_grid][1][1]
scan $detail_scene.[...] as $check_scan
eval $check_fp = make_fingerprint(to_equipment($check_scan))

# 到底检测 1：空 → 绝对到底
if $check_fp is_empty
    log "到底（空）"
    return
end

# 到底检测 2：== fp_r1 → 内容未移动 → 到底
if $check_fp equals $fp_r1
    log "到底（未移动）"
    return
end

# 滚动校验：仅处理过头
eval $verify_retry = 0
@verify_loop
if $check_fp equals $fp_r2
    # 滚动正确
else
    drag [bag_equip_detail].[bag_grid][2][3] down 0.2
    wait step_interval
    eval $verify_retry = add($verify_retry, 1)
    if $verify_retry < 4
        # 重新读取...
        goto verify_loop
    end
    log "滚动校验失败"
    return
end

# ── 确保 row 3 可见（补滚机制）──
eval $nudge_retry = 0
@nudge_loop
click [bag_equip_detail].[bag_grid][3][1]
scan $detail_scene.[...] as $r3_scan
eval $r3_fp = make_fingerprint(to_equipment($r3_scan))
if $r3_fp is_empty
    drag [bag_equip_detail].[bag_grid][2][3] up 0.3
    wait step_interval
    align [bag_equip_detail].[bag_grid]
    eval $nudge_retry = add($nudge_retry, 1)
    if $nudge_retry < 3
        goto nudge_loop
    end
    log "到底（补滚后 row 3 仍无内容）"
    return
end

# 滑动指纹窗口
eval $fp_r1 = $fp_r2
eval $fp_r2 = $fp_r3

# 处理新行（row 3）
align [bag_equip_detail].[bag_grid]
eval $has_new = "0"
for c in [1...6]
    if $c == 1
        eval $fp = $r3_fp   # 已在 nudge_loop 读过
    else
        click [bag_equip_detail].[bag_grid][3][$c]
        scan $detail_scene.[...] as $check_scan
        eval $fp = make_fingerprint(to_equipment($check_scan))
    end
    eval $slot_key = concat("r3c", $c)
    if context.bag_fingerprints.$slot_key equals $fp
        # 无变化
    else
        eval $has_new = "1"
        call "bag_process_slot.wf"
    end
end

eval $fp_r3 = $fp

if $has_new equals "0"
    log "到底（无新内容）"
else
    goto scroll_loop
end
```

### 8.2 单槽位处理子工作流 `subcall/bag_process_slot.wf`

> **设计决策：调律内联，不做两阶段。** 处理每个格子时立即评估并调律（与 §6 回收处理一致）。
> 不先收集候选、再回头调律 —— 因为 slot_key 只在当前滚动帧有效，回头重定位极不可靠。
> `context.bag_candidates` 仅作**处理结果汇总**（报告用），不用于回头定位。

> **武器/防具共用扫描字段。** 点击格子前无法知道是武器还是防具，但两个详情场景
> （equip_weapon_detail / equip_armor_detail）的 `equip_type/equip_level/affix_*` 字段
> **坐标完全相同**，仅基础属性不同（武器 `base_attr`，防具 `base_attr/2`）。
> 指纹与筛选只需共用字段，因此单次 scan 即可；如需完整解析基础属性再按 `equip_type` 分支。

```dsl
#% name: 处理单个背包槽位

eval $row = context._current_row
eval $col = context._current_col
eval $slot_key = concat("r", $row, "c", $col)

@slot_start
click [bag_equip_detail].[bag_grid][$row][$col]
wait step_interval

# OCR 装备详情（仅扫描武器/防具共用字段，坐标一致）
scan [equip_weapon_detail].[equip_type, equip_level,
    affix_gong, affix_shang, affix_jue, affix_zhi, affix_yu] as $equip_scan
eval $equip = to_equipment($equip_scan)
eval $fp = make_fingerprint($equip)

# 空格检查：空 slot 点击后详情页无内容，equip_type 为空字符串
if $equip.[equip_type] equals ""
    eval context.bag_fingerprints.$slot_key = ""
    click [bag_equip_detail].[back]
    wait step_interval
    return
end

# 已处理检查（指纹一致 = 同一件装备）
if context.bag_fingerprints.$slot_key equals $fp
    log concat($slot_key, " 已处理，跳过")
    click [bag_equip_detail].[back]
    wait step_interval
    return
end

# ── 内联评估 + 调律 ──
eval $eval_result = evaluate($equip)
if not $eval_result.[rating] equals "垃圾装备"
    # 记录到候选汇总（报告用）
    eval context.bag_candidates = append(context.bag_candidates, $slot_key)
    # 调用调律子流程（当前已在详情页，无需再点背包格）
    call "subcall/tune_current_equip.wf"
    # 调律可能回收该装备 → 该位置补位新装备 → 重新处理本格
    click [bag_equip_detail].[back]
    wait step_interval
    goto slot_start
end

# 记录指纹
eval context.bag_fingerprints.$slot_key = $fp
eval notify_scroll($col, $row, $fp)

# 返回背包
click [bag_equip_detail].[back]
wait step_interval
```

### 8.3 自动调律主工作流 `auto_tuning.wf`

```dsl
#% name: 自动调律
#% required_scenes: [game_main_page, game_menu_page, bag_equip_detail,
#%   equip_weapon_detail, equip_armor_detail, equip_tune_detail]

# 导航到背包页
call "subcall/nav_main_to_equip.wf"

# 背包遍历 —— 调律已在遍历中内联完成（见 §8.2）
call "subcall/bag_traverse.wf" read "candidates" as $candidates

# 返回主界面
click [bag_equip_detail].[back]
wait step_interval
click [game_menu_page].[back]

# 汇总报告
log concat("已处理候选装备数量: ", count_key($candidates))
```

> 调律在 `bag_process_slot.wf` 中内联执行，**无需阶段二**；`$candidates` 仅用于最终报告。

---

## 9. 场景配置

### 9.1 bag_equip_detail.yaml — 简化

废弃原有的 18 个 bag_X_Y region，改为一个 panel：

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

其他 region（main_weapon、sub_weapon 等装备栏）保持不变。

### 9.2 Layout JSON — panel 绑定 + 滚动箭头

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

> 滚动箭头不需要精确到像素级 — 只要大致对应一行距离即可。
> 偏半行无所谓，align 会自动修正。

### 9.3 Scene YAML — 新增 `panels` 段支持

引擎层需要在 Scene 数据模型中支持 `panels` 作为 Area 的第三种形态（与 regions、points 同级）。

---

## 10. 实施路径

> 以下每一项都是本次改动**需要新增/升级的能力**，而非方案障碍。现有 DSL/引擎只支持两级 `[scene].[area]`
> 引用与固定 region-slot，要支撑 Panel 机制需要以下扩展。

### Phase 0：DSL / 引擎原语升级

- [ ] **grammar 三级索引**：`click_target` / `bracket_expr` 支持 `[panel][row][col]`，且索引可为数字或 `$var`（现仅支持单个 NAME）
- [ ] **`align` 指令**：新增语句，触发 panel 区域截图 + 图像自对齐
- [ ] **context 字段赋值**：确认 `eval context.<field> = {}` / `context.<field>.$key = v` 可用（指纹存储依赖）
- [ ] **context 初始化**：每次工作流启动自动建 `bag_fingerprints={}`、`bag_candidates=[]`

### Phase 1：Panel 架构实现

- [ ] Scene 数据模型扩展：新增 `panels` 段解析（与 regions/points 同级，第三形态）
- [ ] Layout 数据模型扩展：`scenes.<key>.panels` 坐标绑定（一个矩形区域）
- [ ] 图像自校准引擎：`detect_grid()` + `find_high_variance_bands()` 方差分析 + 黑边切带
- [ ] click 路由：`[panel][row][col]` → 查校准缓存 → 格子中心 → 屏幕绝对坐标
- [ ] editor UI：支持绘制/绑定 panel 矩形区域（区别于 region/point）
- [ ] 单元测试：方差分析算法 + 坐标计算

### Phase 2：内置函数

- [ ] `make_fingerprint()` —— 指纹生成
- [ ] `evaluate()` —— 封装 `MingHongEvaluator.evaluate()`，返回 rating 等
- [ ] `append()` —— 向 list 追加元素（`bag_candidates` 汇总用）
- [ ] 单元测试

### Phase 3：配置迁移

- [ ] bag_equip_detail.yaml：废弃 18 个 region，新增 panel
- [ ] Layout JSON：新增 panel 绑定 + scroll_down 箭头
- [ ] 同步改造旧 wf（single_tuning.wf 等）从 region-slot 切到 panel（无需前向兼容）
- [ ] 实测：验证方差分析在不同分辨率下的准确性

### Phase 4：遍历工作流

- [ ] 新建 `subcall/bag_traverse.wf` —— 主遍历循环
- [ ] 新建 `subcall/bag_process_slot.wf` —— 单槽位处理（内联评估+调律）
- [ ] 新建 `subcall/tune_current_equip.wf` —— 详情页内联调律（由 single_tuning 拆分）
- [ ] 实测：验证滚动容差、到底检测、回收处理
- [ ] 迭代：根据实测结果调整 hold 时间和等待时间

### Phase 5：自动调律主工作流

- [ ] 编写 `auto_tuning.wf`
- [ ] 注册到 `workflows.yaml`
- [ ] 端到端测试

---

## 11. 待确认

| 项目 | 说明 |
|------|------|
| 方差分析对空格子的识别 | 空格子方差可能接近 span，需要实测确认区分度 |
| 滚动后画面稳定时间 | hold 后需等多久 OCR 才稳定 |
| 回收后补位速度 | 装备被回收后，新装备补位是否需要额外等待 |
| 到底检测保守度 | 当前设计为新一行 6 格全无变化即到底，是否需要连续 2 行无变化 |
| 游戏背包排序功能 | 是否支持按品质/等级排序？排序后能否让同类型/同品阶装备连续排列？ |
| align 缓存策略 | 每次滚动后必须重新 align，还是只有画面变化时才需要？ |
| Panel 通用性 | 除背包外，是否还有其他场景可用 panel（如材料栏、商店货架）？ |

---

## 12. 相关文档

| 文档 | 内容 |
|------|------|
| [01-game-rules.md](01-game-rules.md) | 调律规则、评估标准 |
| [../10-game/04-tuning-mechanics.md](../10-game/04-tuning-mechanics.md) | 调律机制详细说明 |
| [../10-game/01-equipment-system.md](../10-game/01-equipment-system.md) | 装备系统介绍 |
| [../30-architecture/35-workflows/01-current-equip-analysis.md](../30-architecture/35-workflows/01-current-equip-analysis.md) | 装备分析流程 |
