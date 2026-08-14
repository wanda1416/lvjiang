# DSL 感知指令概览

> 基础指令（collect/eval/call/log）见 [03-2-basic-commands.md](03-2-basic-commands.md)。

## 三条指令的定位

| 指令 | 识别方式 | 返回值 | 典型用途 |
|------|---------|--------|---------|
| **scan** | OCR 文字识别 | `{key: 文本}` 或命中 key | 读按钮文字、读装备词条 |
| **recognize** | ORB 图像匹配 | `{key: 材料名}` 或命中 key | 识别材料类型、道具种类 |
| **find** | OCR 文字定位 | `FoundRegion`（坐标对象） | 找到文字后直接点击 |

一句话区分：
- `scan` / `recognize` → 「这个区域里**写了什么** / **是什么**」
- `find` → 「屏幕上**哪里有这几个字**」

## 共性语法

三条指令共享同一套场景引用和修饰子句体系：

```
# Region 模式
<指令> [scene].[region_list] as $var [by ...] [where ...]

# Panel 模式（scan / recognize）
<指令> [scene].[panel] as $var [by ...]
<指令> [scene].[panel][row][col] as $var

# recognize 特有
recognize ... as rich $var [with <func>] [on group "<name>"]
```

**场景引用**（scene / region / panel 均支持两种形式）：

| 形式 | 示例 | 说明 |
|------|------|------|
| `[name]` | `[equip_detail]` | 配置引用 |
| `$var` | `$scene_name` | 变量引用（运行时解析） |

## 返回值总对比表

| 指令 | 目标 | 默认 | `as rich` | `by ...` | `by` + `rich` |
|------|------|------|-----------|----------|---------------|
| **scan** | Region | `{key: 文本}` | — | `str`（命中 key） | — |
| | Panel 整面板 | `{行: {列: 文本}}` | — | `{row, col}` | — |
| | Panel 单格 | `str` | — | — | — |
| **recognize** | Region | `{key: 材料名}` | `{key: 富dict}` | `str`（命中 key） | `str`（by 优先） |
| | Panel 整面板 | `{行: {列: 材料名}}` | `{行: {列: 富dict}}` | `{row, col}` | `{row, col}`（by 优先） |
| | Panel 单格 | `str` | `dict` | `str` | `str`（by 优先） |
| **find** | 全画布/区域 | — | — | `FoundRegion` 或 `""` | — |

> `scan` 不支持 `as rich`；`find` 必须带 `by`，没有「无 by」形式。

## 修饰子句速查

| 子句 | 作用 | 方向 | 适用指令 |
|------|------|------|---------|
| `by <mode> <target>` | 短路匹配，返回命中 key 或坐标 | **降级**：dict → str/位置 | scan / recognize / find |
| `as rich` | 返回含元数据的富 dict | **升级**：str → dict | 仅 recognize |
| `with <func>` | 指定 rich dict 的转换函数 | 配合 rich 使用 | 仅 recognize |
| `where confidence >= <n>` | 过滤低置信度结果（阈值 `[0.0, 1.0]`，超出范围输出警告） | **过滤**：不改变类型 | scan / recognize / find |
| `on group "<name>"` | 限定材料匹配分组 | **过滤**：缩小匹配范围 | 仅 recognize |

**by 的四种匹配模式**（三条指令通用）：

| 模式 | 说明 | target |
|------|------|--------|
| `equals "文本"` | 完全匹配 | 字符串 |
| `contains "文本"` | 包含子串 | 字符串 |
| `equals_any $list` | 等于列表中任一项 | 列表变量 |
| `contains_any $list` | 包含列表中任一项 | 列表变量 |

## 与 click 的配合

| 指令产出 | click 用法 | 说明 |
|---------|-----------|------|
| `scan` 无 by → `$var` = dict | 不可直接 click | dict 是 `{key: 文本}`，用于读取文字内容，不能传给 click |
| `scan` 有 by → `$var` = str | `click [scene].$var` | str 即 region key，引擎从布局解析 region 中心 |
| `recognize` 无 by → `$var` = dict | 不可直接 click | 同上，dict 是 `{key: 材料名}` |
| `recognize` 有 by → `$var` = str | `click [scene].$var` | 同上，str 即 slot key |
| `find` → `$var` = FoundRegion | `click $var` | 直接点击文字的屏幕坐标 |
| Panel by → `$var` = `{row,col}` | `click [scene].[panel][$var.row][$var.col]` | 按行列位置点击对应格 |

## 坐标运算

CoordRef 类型支持向量运算，可用于计算相对位置。CoordRef 值通过变量持有，运算结果可赋给新变量：

```
# 计算两个区域的相对位移
$a = [scene].[region_a]
$b = [scene].[region_b]
$offset = $b - $a                    # Offset

# 平移坐标
$target = $a + $offset               # CoordRef

# 点击计算后的位置
click $target
```

**合法运算**：
- `CoordRef + Offset` → `CoordRef`（保持子类）
- `CoordRef - Offset` → `CoordRef`（保持子类）
- `CoordRef - CoordRef` → `Offset`（隐式降级为中心点）
- `Offset ± Offset` → `Offset`
- `Offset * n` / `Offset / n` → `Offset`
- `tuple → Offset` — 隐式转换（`(dx, dy)` 可直接当位移用）

**禁止运算**：`CoordRef * n` / `CoordRef / n`（位置乘以数字无意义，破坏向量运算法则）

详见 [02-concepts.md — CoordRef 类型体系](02-concepts.md#三coordref-类型体系运行时坐标层)。

## 详细文档

- [04-1-scan.md](04-1-scan.md) — scan 完整语法、返回值、修饰子句
- [04-2-recognize.md](04-2-recognize.md) — recognize 完整语法、返回值、rich/with
- [04-3-find.md](04-3-find.md) — find 完整语法、FoundRegion、搜索区域
