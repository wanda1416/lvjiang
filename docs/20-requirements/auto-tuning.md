# 自动调律 — 背包遍历与调律决策

> 状态：规划中（前置工作未完成）
> 依赖：装备分析流程（equip_analysis）、单件调律（single_tuning）、鸣金虹评估器

---

## 1. 背景与目标

自动调律是律匠的核心目标流程，旨在实现"打开背包 → 自动分析 → 自动调律"的完整闭环。

当前已具备的能力：
- `equip_analysis.wf` — 扫描身上已穿戴的 8 件装备
- `single_tuning.wf` — 对指定背包格装备执行一次调律
- `MingHongEvaluator` — 装备评估（品阶/首词条/神力/扣分/评级）+ 调律熔断判断
- `nav_main_to_equip.wf` / `nav_equip_to_tune.wf` — 导航子流程

缺失的两个关键能力：
1. **背包滑动遍历** — 游戏背包可滚动，需自动翻页扫描所有装备
2. **调律决策编排** — 基于评估结果决定哪些装备值得调律，并自动执行

缺失的架构前提：
3. **全局背包状态** — 独立于工作流的持久化状态，记录每个格子当前装的是什么装备

---

## 2. 全局背包状态（架构前提）

### 2.1 为什么需要全局状态

工作流引擎的变量（`$var`）是执行期临时的，工作流结束后即丢失。但背包遍历场景要求：

- 滑动后 `bag_1_1` 的内容变了，我们需要记录它**此刻**是什么
- 滑回来时，能直接查表知道 `bag_1_1` 现在又是哪件装备
- 调律阶段需要知道"候选装备在哪个格子"，但此时背包可能已经滑到了别的页
- 多次运行之间，状态应能保留（比如上次扫到一半中断了）

因此需要一个**与工作流生命周期无关的全局状态**，随时可读、随时可写。

### 2.2 状态模型

```python
# 背包状态 — 记录每个可见格子的当前内容
bag_state = {
    # key: 格子标识（固定位置标识，非装备名）
    # value: 装备简要信息（从背包格 OCR 可获取的最少信息）
    "bag_1_1": {
        "type": "横刀",           # 装备类型
        "quality": "gold",        # 品阶（如果可识别）
        "level": 110,             # 等级（如果可识别）
        "signature": "横刀_金_110", # 去重签名
        "evaluated": true,        # 是否已完成评估
        "rating": "合格装备",      # 评估结果（评估后填入）
        "tuned": false,           # 是否已调律
        "scanned_at": "2026-07-19T12:00:00"  # 最后扫描时间
    },
    "bag_1_2": { ... },
    ...
    "bag_3_6": null,  # 空格子
}
```

关键设计点：
- **格子 key 是位置标识**（bag_1_1 ~ bag_3_6），不是装备名
- 每次 OCR 后更新该位置的装备信息
- `null` 表示空格子
- 滑动后，所有 18 个 key 的 value 都需要刷新（因为内容整体偏移了）

### 2.3 状态与滑动的关系

```
初始状态（第 1 页）：
  bag_1_1 = 横刀A    bag_1_2 = 剑B    ... bag_3_6 = 冠胄F

滑动一次后（第 2 页）：
  bag_1_1 = 胸甲G    bag_1_2 = 佩H    ... bag_3_6 = 腕甲L
  ↑ 18 个 key 的 value 全部更新

滑回第 1 页后：
  bag_1_1 = 横刀A    bag_1_2 = 剑B    ... bag_3_6 = 冠胄F
  ↑ 通过签名对比确认回到了已知页面
```

> 注意：格子 key 始终代表"屏幕上的物理位置"，不随滑动改变含义。
> bag_1_1 永远是左上角那个格子，只是它里面的内容随滑动变化。

### 2.4 持久化方案

**方案 A：session.json 扩展**（推荐）

在现有 `session.json` 中增加 `bag_state` 字段：
```json
{
  "active_user": "蔡元君",
  "bag_state": {
    "bag_1_1": { "type": "横刀", "quality": "gold", ... },
    ...
  },
  "bag_state_updated": "2026-07-19T12:00:00"
}
```

优点：与现有配置体系一致，随用户切换自动隔离。

**方案 B：独立状态文件**

`config/local/users/{username}/bag_state.json`

优点：不污染 session.json，读写更轻量。

### 2.5 DSL 访问方式

工作流中需要能随时读写这个全局状态：

```dsl
# 读取某个格子的状态
eval $item = bag_state("bag_1_1")

# 更新某个格子
eval bag_state.set("bag_1_1", $scan_result)

# 查询所有候选（已评估但未调律的）
eval $candidates = bag_state.query(rating != "垃圾装备", tuned == false)

# 标记已调律
eval bag_state.update("bag_1_1", tuned = true)
```

### 2.6 前置工作

| 编号 | 内容 | 说明 |
|------|------|------|
| S-1 | 设计 BagState 数据类 | Python 端的数据模型，含序列化/反序列化 |
| S-2 | 持久化集成 | 选择存储方案，实现读写 |
| S-3 | DSL 内置函数 | `bag_state()` / `bag_state.set()` / `bag_state.query()` |
| S-4 | 与 OCR 结果对接 | 背包格 OCR 后自动更新对应 key 的状态 |

---

## 3. 背包滑动遍历

### 3.1 问题描述

游戏装备背包可见区域为 3×6=18 格。当背包物品超过 18 件时，需要滑动翻页查看更多内容。

核心难点：
- 触屏滑动受惯性/动量影响，每次滑动距离不完全一致
- 背包无分页指示器、无滚动条、无页码
- 无法通过固定滑动次数来保证覆盖所有物品

### 3.2 方案：受控短距滑动 + OCR 变化检测

**核心思路**：不追求精确翻页，而是"滑动 → OCR 识别当前可见项 → 与已处理集合对比 → 判断是否到底"。

#### 滑动策略

```
参数：
  - drag 起点：背包区域右侧中间
  - drag 终点：向上偏移约 1/3 背包高度
  - duration：~150ms（快速拖动，减少惯性影响）
  - 滑动后等待：0.5s（画面稳定）

流程：
  seen_signatures = set()

  loop:
      current = OCR 当前可见 18 格
      current_sigs = {装备签名 for 每件可见装备}

      if current_sigs ⊆ seen_signatures:
          # 滑动后没有新物品 → 到底了
          break

      new_items = current_sigs - seen_signatures
      处理(new_items)
      seen_signatures |= current_sigs

      执行受控滑动
```

#### 装备签名

用于去重的装备特征标识，可基于以下信息组合：
- 装备类型名（如"横刀"、"冠胄"）
- 品阶颜色（金/紫/蓝/绿）
- 当前可见的词条文本片段

> 注意：同一页内可能存在同类型同品阶的装备，签名需要足够区分度。
> 如果 OCR 信息不足以区分，可能需要结合位置信息。

### 3.3 备选方案

如果受控滑动不够稳定：

**方案 B — 固定次数遍历**：
- 滑动 N 次（N 可配置，默认 5 次覆盖 90 格）
- 每次滑动后 OCR 并与上一页去重
- 连续 2 次无新物品则提前终止

**方案 C — 排序优先**：
- 如果游戏背包支持按品质/等级排序，优先处理金色装备
- 排序后只需遍历前几页即可覆盖所有高价值装备
- 需要确认游戏是否支持此功能

### 3.4 前置工作

| 编号 | 内容 | 说明 |
|------|------|------|
| B-1 | 确认背包格 OCR 能力 | 当前 `bag_equip_detail` 的 18 个 `bag_X_Y` 区域是否已有 OCR 字段定义，还是仅作为点击坐标？需要确认能否从背包格直接识别装备类型/品阶 |
| B-2 | 实测滑动行为 | 在投屏环境下实测 drag 操作的惯性表现，确定合适的 drag 参数 |
| B-3 | 确认背包排序功能 | 游戏背包是否支持排序？排序后能否让同类型/同品阶装备连续排列？ |

---

## 4. 调律决策编排

### 4.1 决策流程

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

### 4.2 调律后处理

所有装备均留在背包，不做自动处理：
- 调律后评级提升 → 保留，日志标记"调律成功"
- 调律后仍一般 → 保留，日志标记"可后续转律"
- 调律后变差 → 保留，日志标记"建议手动处理"

> 注：用户后续可手动决定回收/承音/传律等操作。

### 4.3 评估集成

`MingHongEvaluator` 已实现完整评估逻辑：
- `evaluate()` — 完整装备评级
- `check_tuning_worthiness()` — 调律过程中实时熔断判断

需要将其封装为 DSL 内置函数，使工作流可以调用：
```dsl
eval $result = evaluate($equip_data)
if $result.rating == "合格装备"
    # 执行调律
end
```

### 4.4 前置工作

| 编号 | 内容 | 说明 |
|------|------|------|
| D-1 | 评估器 DSL 集成 | 将 `MingHongEvaluator.evaluate()` 封装为 DSL 内置函数 `evaluate()` |
| D-2 | EquipmentData DSL 可传递 | 确保装备数据对象可在 DSL 变量中存储和传递 |
| D-3 | 背包格 → 装备详情 的扫描流程 | 需要实现"点击背包格 → 进入详情页 → OCR → 返回背包"的循环 |

---

## 5. 自动调律工作流草案

```dsl
#% name: 自动调律
#% required_scenes: [game_main_page, game_menu_page, bag_equip_detail,
#%   equip_weapon_detail, equip_armor_detail, equip_tune_detail, equip_tune_result]

call "subcall/nav_main_to_equip.wf"

# ── 阶段一：背包遍历 + 初步筛选 ──
eval $candidates = []
eval $page = 0

# 遍历背包所有页
loop
    eval $page = $page + 1

    # 扫描当前页 18 格的装备简要信息
    scan_bag [bag_equip_detail] as $page_items

    for item in $page_items
        # 快速品阶过滤（如果 OCR 能识别品阶）
        if item.quality == "绿色" or item.quality == "蓝色"
            continue
        end

        # 点击进入详情页
        click [bag_equip_detail].item.slot_key
        wait step_interval

        # 识别完整词条
        scan [equip_weapon_detail] or [equip_armor_detail] as $equip_data
        eval $equip = to_equipment($equip_data)

        # 评估
        eval $result = evaluate($equip)
        if $result.rating != "垃圾装备"
            eval $candidates.append($equip)
        end

        # 返回背包页
        click [bag_equip_detail].[back]
        wait step_interval
    end

    # 滑动到下一页
    eval $scrolled = scroll_bag("down")
    if not $scrolled.has_new
        break
    end
end

# ── 阶段二：对候选装备逐一调律 ──
log concat("候选装备数量: ", len($candidates))

for equip in $candidates
    # 调用单件调律流程
    call "single_tuning.wf" with ...
end

# 返回主界面
click [game_menu_page].[back]
```

> 注：以上为概念草案，实际实现需根据 DSL 引擎能力和前置工作的完成情况调整。

---

## 6. 实施路径

### Phase 0：全局状态层（架构前提）

- [ ] S-1：设计 BagState 数据类（Python）
- [ ] S-2：持久化集成（session.json 或独立文件）
- [ ] S-3：DSL 内置函数（bag_state 读写接口）
- [ ] S-4：与 OCR 结果对接

### Phase 1：前置确认

- [ ] B-1：确认背包格 OCR 能力现状
- [ ] B-2：实测投屏环境下的滑动行为
- [ ] B-3：确认游戏背包排序功能

### Phase 2：背包遍历能力

- [ ] 实现受控滑动函数（drag 参数调优）
- [ ] 实现 OCR 变化检测 + 到底判断
- [ ] 滑动后自动刷新 bag_state 中 18 个 key
- [ ] 封装为 DSL 可用的内置函数 `scroll_bag()`
- [ ] 单元测试

### Phase 3：评估集成

- [ ] D-1：封装 `evaluate()` 为 DSL 内置函数
- [ ] D-2：确保 EquipmentData 在 DSL 中可传递
- [ ] D-3：实现"背包格 → 详情 → OCR → 返回"循环
- [ ] 评估结果写入 bag_state

### Phase 4：自动调律工作流

- [ ] 编写 `auto_tuning.wf`
- [ ] 注册到 `workflows.yaml`
- [ ] 端到端测试

---

## 7. 相关文档

| 文档 | 内容 |
|------|------|
| [game-rules.md](game-rules.md) | 调律规则、评估标准 |
| [../10-game/tuning-mechanics.md](../10-game/tuning-mechanics.md) | 调律机制详细说明 |
| [../10-game/equipment-system.md](../10-game/equipment-system.md) | 装备系统介绍 |
| [../35-workflows/01-current-equip-analysis.md](../35-workflows/01-current-equip-analysis.md) | 装备分析流程 |
