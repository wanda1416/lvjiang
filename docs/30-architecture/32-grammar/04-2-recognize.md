# recognize — 图像材料识别

对指定场景区域的截图通过 ORB 特征匹配识别材料类型，结果存入变量。

> 概览与对比表见 [04-data-flow.md](04-data-flow.md)。

## 完整语法

```
# ── Region 模式（一个或多个区域） ──
recognize [scene] as $var                           # 识别场景所有 slot
recognize [scene].[s1, s2] as $var                  # 仅识别指定 slot
recognize [scene].$area_key as $var                 # 动态 slot（变量指定 key）

# ── Panel 模式（网格逐格识别） ──
recognize [scene].[panel] as $var                   # 整面板逐格识别
recognize [scene].[panel][row][col] as $var         # 单格识别

# ── 带 by 子句（短路匹配） ──
recognize [scene].[s1, s2] as $var by equals "材料名"
recognize [scene].[s1, s2] as $var by contains "材料名"
recognize [scene].[s1, s2] as $var by equals_any $list
recognize [scene].[s1, s2] as $var by contains_any $list

# ── 带 as rich（富返回值） ──
recognize [scene].[s1, s2] as rich $var             # 返回富 dict
recognize [scene].[s1, s2] as rich $var with yysls_rich_parse   # 指定转换函数

# ── 带 where 子句（置信度过滤） ──
recognize [scene].[s1, s2] as $var where confidence >= 0.85

# ── 带 on group 子句（限定分组范围） ──
recognize [scene].[s1, s2] as $var on group "分组名"
recognize [scene].[s1, s2] as $var by equals $name on group "分组名"

# ── 组合使用 ──
recognize [scene].[s1, s2] as rich $var with yysls_rich_parse where confidence >= 0.8 on group "调律材料"
```

## 参数说明

| 参数 | 支持形式 | 说明 |
|------|---------|------|
| scene | `[key]` / `"key"` / `$var` | 场景名，必须在当前布局中绑定坐标 |
| region | `[key]` / `"key"` / `$var` | slot 区域名，必须在当前布局中绑定坐标 |
| panel | `[key]` | 面板名，引擎自动按对齐结果逐格识别 |
| `$var` | `$name` | 目标变量名，结果写入此变量 |

## 返回值格式

### Region 模式

| 修饰符 | `$var` 类型 | 内容 | 未命中/空槽时 |
|--------|------------|------|--------------|
| 默认（plain） | `dict` | `{slot_key: material_type_str}` | 空 dict `{}` |
| `as rich` | `dict` | `{slot_key: enriched_dict}` | 空 dict `{}` |
| `by ...` | `str` | 首个命中的 slot_key | 空字符串 `""` |
| `as rich by ...` | `str` | 同 `by`（by 优先） | 空字符串 `""` |

**默认（plain）**：

```
recognize [material_grid] as $mats
# $mats = {"slot_1": "玄铁", "slot_2": "精金", ...}
# 取值：$mats.slot_1
# 点击：click [material_grid].$key
```

**有 by（短路匹配）**：

```
recognize [equip_tune_detail].[material_1, material_2, material_3] as $slot by equals $material_name on group "调律材料"
# $slot = "material_2"（首个命中的 slot_key）或 ""（未命中）
```

### Panel 模式

| 修饰符 | `$var` 类型 | 内容 | 未命中时 |
|--------|------------|------|---------|
| 默认 | `dict` | `{行: {列: material_type}}` | 空 dict `{}` |
| `as rich` | `dict` | `{行: {列: enriched_dict}}` | 空 dict `{}` |
| `by ...` | `dict` | `{"row": 行号, "col": 列号}` | 空 dict `{}` |

**整面板无 by**：

```
recognize [bag_item_detail].[bag_grid] as $grid
# $grid = {"1": {"1": "金狗粮", "2": "玄铁", ...}, "2": {...}}
# 取值：$grid.[1].[2] 或 $grid.$r.$c
# 点击：click [bag_item_detail].[bag_grid][1][2]
```

**整面板有 by**：

```
recognize [bag_item_detail].[bag_grid] as $pos by equals "金狗粮" on group "食物"
# $pos = {"row": 1, "col": 2}（首个命中的行列位置）或 {}（未命中）
# 点击：click [bag_item_detail].[bag_grid][$pos.row][$pos.col]
```

### Panel 单格

| 修饰符 | `$var` 类型 | 内容 |
|--------|------------|------|
| 默认 | `str` | 材料类型名 |
| `as rich` | `dict` | enriched_dict |

```
recognize [bag_item_detail].[bag_grid][1][2] as $item
# $item = "金狗粮"

recognize [bag_item_detail].[bag_grid][1][2] as rich $item
# $item = {"label": "金狗粮", "group": "食物", "confidence": 0.95, ...}
```

## as rich — 富返回值

`as rich` 将默认的 str 返回值**升级**为包含完整元数据的富 dict。

**语法**：

```
recognize [scene].[s1, s2] as rich $var              # 仅 base 字段
recognize [scene].[s1, s2] as rich $var with <func>  # 指定转换函数
```

`rich` 是 `as` 和 `$var` 之间的可选关键字，大小写不敏感。

### base 字段（无 with 子句）

```
$mats = {
    "slot_1": {
        "label": "宋元通宝",          # 材料类型
        "group": "货币资产",            # 分组名（无则空字符串）
        "confidence": 0.95,            # 匹配置信度（Python float）
        "level_text": "110阶",          # OCR 原始文本
        "count_text": "0/691"           # OCR 原始文本
    },
    "slot_2": { ... }
}
```

### with 子句转换

`with <func_name>` 指定任意满足 `dict -> dict` 的内置函数（通过 `@builtin_func` 注册）。

```
recognize [material_grid].[f1, f2] as rich $mats with yysls_rich_parse
# $mats["slot_1"] = {
#     "label": "宋元通宝",
#     "group": "货币资产",
#     "confidence": 0.95,
#     "real_level": 110,       # yysls_rich_parse 解析
#     "count": 691,            # yysls_rich_parse 解析
#     "devoted": 0             # yysls_rich_parse 解析
# }
```

> `yysls_rich_parse` 会解析数值字段并**删除**原始 `level_text` / `count_text`。

### rich 与 by 的优先级

`rich` 和 `by` 是两个正交的修饰符，方向相反：

- **`rich` 是升级**：str → 富 dict
- **`by` 是降级**：dict → str/位置
- **`by` 优先于 `rich`**：同时指定时，`by` 的降级语义生效，`rich` 被忽略

| 组合 | Region 返回值 | Panel 整面板返回值 | Panel 单格返回值 |
|------|-------------|------------------|----------------|
| plain | `{slot_key: str}` | `{行: {列: str}}` | `str` |
| `as rich` | `{slot_key: dict}` | `{行: {列: dict}}` | `dict` |
| `by ...` | `str` | `{row, col}` | `str` |
| `as rich by ...` | `str`（by 优先） | `{row, col}`（by 优先） | `str`（by 优先） |

## by 子句

`by` 将返回值从 dict **降级**为 str（Region 模式）或位置 dict（Panel 模式）。

**语义**：一次截图 → 逐 slot 识别 → 首个命中即返回 slot key，不再遍历剩余 slot。

**四种匹配模式**：

| 模式 | 说明 | target 类型 |
|------|------|------------|
| `equals "材料名"` | 材料类型完全等于目标 | 字符串常量 |
| `contains "材料名"` | 材料类型包含目标子串 | 字符串常量 |
| `equals_any $list` | 材料类型等于列表中任一项 | 列表变量 |
| `contains_any $list` | 材料类型包含列表中任一项 | 列表变量 |

**示例**：

```
# 在材料槽中找到指定材料
recognize [equip_tune_detail].[material_1, material_2, material_3] as $slot by equals "玄铁"
if $slot
    click [equip_tune_detail].$slot
end

# 匹配多种材料之一
eval $targets = ["玄铁", "精金", "银矿"]
recognize [material_grid] as $found by equals_any $targets
```

## where 子句

`where` 是**纯过滤**，不改变返回值类型，只丢弃置信度低于阈值的结果。

```
where confidence >= <threshold>
```

`<threshold>` 支持数字常量（`0.85`）和变量引用（`$threshold`）。

```
# 过滤低置信度
recognize [material_grid] as $mats where confidence >= 0.85

# 与 by 组合
recognize [scene].[s1, s2] as $key by equals "玄铁" where confidence >= 0.8
```

## on group 子句

`on group` 限定材料识别的匹配范围，仅在指定分组的参考材料中匹配。

```
on group "<分组名>"
on group $var          # 变量引用
```

**示例**：

```
# 只在"调律材料"分组中匹配
recognize [equip_tune_detail].[material_1, material_2] as $slot by equals "玄铁" on group "调律材料"

# 动态分组
eval $group_name = "食物"
recognize [bag_item_detail].[bag_grid] as $pos by equals "金狗粮" on group $group_name
```

## 与 click 的配合

recognize 执行后，引擎自动将 slot region 坐标元数据存入 `_coord_meta`，供后续 `click [scene].$key` 解析坐标。

```
# 典型模式：recognize by 找到 slot → click 点击
recognize [equip_tune_detail].[material_1, material_2, material_3] as $slot by equals "玄铁"
if $slot
    click [equip_tune_detail].$slot
end
```

## 注意事项

- **未绑定区域直接报错**：与 scan 相同，点名的 slot 若在当前布局中没绑定坐标，立即抛错终止
- **`[scene].$var` 变量取空值**时同样报错
- **region 与 panel 同名时 region 优先**
- **空格 value** 为空字符串 `""`
- **行列数**取自对齐结果（实际检测到的网格），而非配置的 rows/cols
- **`with` 必须配合 `as rich`**：单独使用 `with` 而不写 `as rich` 会抛 `WorkflowUserError`
- **共享识别器状态**：引擎使用类级别的共享 `MaterialRecognizer`，每次工作流启动时通过 `reset_state()` 刷新参考库
