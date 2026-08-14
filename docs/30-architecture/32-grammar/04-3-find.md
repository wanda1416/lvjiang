# find — 文字坐标定位

在屏幕上搜索特定文字，找到后返回该文字所在区域的坐标对象，供后续 `click` 直接点击。

> 概览与对比表见 [04-data-flow.md](04-data-flow.md)。

## 完整语法

```
# ── 全画布搜索 ──
find as $var by contains "文字"
find as $var by equals "文字"
find as $var by contains_any $list
find as $var by equals_any $list

# ── 指定区域搜索 ──
find [scene].[area] as $var by contains "文字"
find [scene].[panel] as $var by contains "文字"     # panel 与 region 对 find 等价

# ── 动态场景和区域 ──
find $scene.$region as $var by contains "文字"

# ── 带 where 子句（置信度过滤） ──
find as $var by contains "文字" where confidence >= 0.8
find [scene].[area] as $var by contains_any $list where confidence >= 0.7
```

## 参数说明

| 参数 | 支持形式 | 说明 |
|------|---------|------|
| scene | `[key]` / `$var` | 场景名（可选），限定搜索范围 |
| area | `[key]` / `$var` | 区域名（可选），限定搜索范围 |
| `$var` | `$name` | 目标变量名，FoundRegion 或空字符串写入此变量 |

> **与 scan/recognize 的区别**：find 不存 `_coord_meta`，返回的是独立的坐标对象 `FoundRegion`，用 `click $var` 直接点击。

## 返回值格式

| 情况 | `$var` 类型 | 内容 | 条件判断 |
|------|------------|------|---------|
| 找到文字 | `FoundRegion` | 匹配区域的画布归一化坐标 | truthy |
| 未找到 | `str` | 空字符串 `""` | falsy |

> find **必须带 by 子句**（指定搜索目标），没有「无 by」形式。

**FoundRegion 对象**包含以下属性：

| 属性 | 类型 | 说明 |
|------|------|------|
| `x` | float | 匹配区域中心的画布归一化 X 坐标 |
| `y` | float | 匹配区域中心的画布归一化 Y 坐标 |
| `w` | float | 匹配区域的归一化宽度 |
| `h` | float | 匹配区域的归一化高度 |
| `text` | str | 匹配到的 OCR 文本 |
| `confidence` | float | 匹配置信度 |

## by 子句

find 的 `by` 子句**必填**，指定匹配模式和搜索目标。

**四种匹配模式**：

| 模式 | 说明 | target 类型 |
|------|------|------------|
| `equals "文字"` | OCR 文本完全等于目标 | 字符串常量 |
| `contains "文字"` | OCR 文本包含目标子串 | 字符串常量 |
| `equals_any $list` | OCR 文本等于列表中任一项 | 列表变量 |
| `contains_any $list` | OCR 文本包含列表中任一项 | 列表变量 |

`contains_any` / `equals_any` 支持**顺序匹配**：按列表顺序逐个尝试，返回第一个命中的文字位置。

**示例**：

```
# 全画布搜索，找到后直接点击
find as $found by contains "调律"
if $found
    click $found                   # 直接点击找到的文字位置
end

# 在指定区域内搜索
find [general_action].[btn_area] as $btn by contains "确认"
if $btn
    click $btn
end

# 动态场景和区域
find $scene.$region as $close by contains "关闭"

# 顺序匹配：在多个目标中找第一个命中的
eval $buttons = ["确认", "确定", "OK"]
find as $btn by contains_any $buttons
if $btn
    click $btn
end
```

## 搜索区域

| 形式 | 语法 | 说明 |
|------|------|------|
| 全画布 | `find as $var by ...` | 在整个屏幕截图中搜索 |
| 指定区域 | `find [scene].[area] as $var by ...` | 仅在布局定义的区域内搜索 |
| 指定面板 | `find [scene].[panel] as $var by ...` | 仅在布局定义的面板内搜索（与 region 等价） |
| 动态区域 | `find $scene.$region as $var by ...` | 场景和区域由变量指定 |

指定区域搜索时，`[scene].[area]` 必须在当前布局中绑定坐标（region 或 panel 均可），否则报错。Region 和 Panel 对 find 等价，都提供矩形裁剪区域。

## where 子句

`where` 是**纯过滤**，不改变返回值类型，只丢弃置信度低于阈值的匹配结果。

```
where confidence >= <threshold>
```

`<threshold>` 支持数字常量（`0.8`）和变量引用（`$threshold`）。

```
# 过滤低置信度匹配
find as $found by contains "确认" where confidence >= 0.9

# 与区域搜索组合
find [scene].[area] as $btn by contains "确定" where confidence >= 0.85
```

## 与 click 的配合

find 返回的 `FoundRegion` 可直接传给 `click $var`，点击匹配文字的屏幕坐标位置。

```
# 典型模式：find 找到文字 → click 直接点击
find as $found by contains "确认"
if $found
    click $found                   # 点击文字所在位置
end

# 找到后做其他操作再点击
find [general_action].[btn_area] as $btn by contains "背包"
if $btn
    wait stable 3
    click $btn
end
```

> **与 scan/recognize 的区别**：
> - `scan`/`recognize` 产出的是 dict 或 key 字符串，用 `click [scene].$var` 点击布局中定义的 region 中心
> - `find` 产出的是 `FoundRegion` 坐标对象，用 `click $var` 直接点击文字的屏幕位置

## 注意事项

- **find 必须带 by 子句**：没有「无 by」形式，因为必须指定搜索目标
- **指定区域必须在布局中绑定**：`[scene].[area]` 若未绑定坐标，find 立即报错
- **FoundRegion 是独立坐标对象**：不依赖 `_coord_meta`，不依赖布局中的 region 定义
- **顺序匹配**：`contains_any` / `equals_any` 按列表顺序返回首个命中，可用于优先级搜索
- **全画布搜索较慢**：建议尽量用 `[scene].[area]` 限定搜索范围，减少 OCR 处理量
