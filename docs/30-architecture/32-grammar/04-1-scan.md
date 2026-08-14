# scan — OCR 文字扫描

对指定场景区域的截图执行 OCR 文字识别，结果存入变量。

> 概览与对比表见 [04-data-flow.md](04-data-flow.md)。

## 完整语法

```
# ── Region 模式（一个或多个区域） ──
scan [scene] as $var                              # 扫描场景所有 region
scan [scene].[r1, r2, ...] as $var                # 仅扫描指定 region
scan [scene].$area_key as $var                    # 动态 region（变量指定 key）

# ── Panel 模式（网格逐格识别） ──
scan [scene].[panel] as $var                      # 整面板逐格 OCR
scan [scene].[panel][row][col] as $var            # 单格 OCR

# ── 带 by 子句（短路匹配） ──
scan [scene].[r1, r2] as $var by equals "文本"
scan [scene].[r1, r2] as $var by contains "文本"
scan [scene].[r1, r2] as $var by equals_any $list
scan [scene].[r1, r2] as $var by contains_any $list

# ── 带 where 子句（置信度过滤） ──
scan [scene].[r1, r2] as $var where confidence >= 0.8
scan [scene].[r1, r2] as $var by contains "文本" where confidence >= 0.7
```

## 参数说明

| 参数 | 支持形式 | 说明 |
|------|---------|------|
| scene | `[key]` / `"key"` / `$var` | 场景名，必须在当前布局中绑定坐标 |
| region | `[key]` / `"key"` / `$var` | 区域名，必须在当前布局中绑定坐标 |
| panel | `[key]` | 面板名，引擎自动按对齐结果逐格识别 |
| `$var` | `$name` | 目标变量名，结果写入此变量 |

> scene 和 region 的三种形式语义等价。`$var` 形式运行时从变量表查找。

## 返回值格式

### Region 模式

| 修饰符 | `$var` 类型 | 内容 | 未命中时 |
|--------|------------|------|---------|
| 无 | `dict` | `{region_key: ocr_text}` | 空 dict `{}` |
| `by ...` | `str` | 首个命中的 region_key | 空字符串 `""` |

**无 by（最常见）**：

```
scan [equip_weapon_detail] as $result
# $result = {"affix_1": "攻击+10", "affix_2": "防御+5", ...}
# 取值：$result.affix_1
# 点击：click [equip_weapon_detail].$result    ← 不行！$result 是 dict
#        正确做法：先 by 匹配拿到 key，再 click [scene].$key
```

**有 by（短路匹配）**：

```
scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3] as $key by contains "调律"
# $key = "sub_func_2"（首个命中的 region key）或 ""（未命中）
# 点击：click [equip_weapon_detail].$key
```

### Panel 模式

| 修饰符 | `$var` 类型 | 内容 | 未命中时 |
|--------|------------|------|---------|
| 无 | `dict` | `{行号: {列号: ocr_text}}` | 空 dict `{}` |
| `by ...` | `dict` | `{"row": 行号, "col": 列号}` | 空 dict `{}` |

**整面板无 by**：

```
scan [general_action].[actions] as $bags   # actions 是 6×2 panel
# $bags = {"1": {"1": "抱拳", "2": "作揖", ...}, "2": {...}}
# 取值：$bags.[1].[2] 或 $bags.$r.$c
# 点击：click [general_action].[actions][1][2]
```

**整面板有 by**：

```
scan [general_action].[actions] as $pos by contains "背包"
# $pos = {"row": 1, "col": 2}（首个命中的行列）或 {}（未命中）
# 取值：$pos.row、$pos.col
# 点击：click [general_action].[actions][$pos.row][$pos.col]
```

> 整面板 + by 返回的是**位置**（`{row, col}`），与 Region + by 返回字段名（`str`）的语义不同。

### Panel 单格

```
scan [scene].[panel][1][2] as $cell
# $cell = "抱拳"（该格的 OCR 文本）
```

## by 子句

`by` 将返回值从 dict **降级**为 str（Region 模式）或位置 dict（Panel 模式）。

**语义**：一次截图 → 逐区域/格识别 → 首个命中即返回，不再遍历剩余区域。

**四种匹配模式**：

| 模式 | 说明 | target 类型 |
|------|------|------------|
| `equals "文本"` | OCR 文本完全等于目标 | 字符串常量 |
| `contains "文本"` | OCR 文本包含目标子串 | 字符串常量 |
| `equals_any $list` | OCR 文本等于列表中任一项 | 列表变量 |
| `contains_any $list` | OCR 文本包含列表中任一项 | 列表变量 |

**示例**：

```
# 在多个按钮中找到"调律"按钮
scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4] as $tune_key by contains "调律"
if $tune_key
    click [equip_weapon_detail].$tune_key
end

# 匹配多个目标之一
eval $keywords = ["攻击", "防御"]
scan [scene].[field_1, field_2, field_3] as $found by contains_any $keywords
```

## where 子句

`where` 是**纯过滤**，不改变返回值类型，只丢弃置信度低于阈值的结果。

```
where confidence >= <threshold>
```

`<threshold>` 支持数字常量（`0.8`）和变量引用（`$threshold`）。

```
# 过滤低置信度
scan [equip_detail].[affix_1, affix_2] as $result where confidence >= 0.8

# 与 by 组合
scan [equip_detail].[f1, f2, f3] as $key by contains "调律" where confidence >= 0.7
```

## 与 click 的配合

`scan by` 返回的 str 即 region key，可直接用于 `click [scene].$key`。引擎通过布局定义解析 region 坐标，无需额外操作。

```
# 典型模式：scan by 找到 key → click 点击
scan [equip_page].[btn_1, btn_2, btn_3] as $key by contains "确认"
if $key
    click [equip_page].$key
end
```

> `by` 子句等价于 `scan` + `find_key` 的组合，但只需一次截图，推荐使用。

## 注意事项

- **未绑定区域直接报错**：点名的 region（含 `[scene].$var` 动态解析出的 key）若在当前布局中没绑定坐标，`scan` 立即抛错终止，不会静默跳过
- **`[scene].$var` 变量取空值**时同样报错
- **region 与 panel 同名时 region 优先**
- **空格 value** 为空字符串 `""`
- **行列数**取自对齐结果（实际检测到的网格），而非配置的 rows/cols
