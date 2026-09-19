# 01 · 四条感知通道

> Layer: L3 · 目标：给定一个问题，能一眼决定该走哪条通道，不走错。

游戏 UI 不在无障碍树里。律匠的感知层是**四条独立通道**，各自解决不同性质的问题：

| # | 通道 | 输入 | 输出 | 单帧成本 | 典型 DSL 入口 |
|---|------|------|------|---------|--------------|
| 1 | OCR 文字识别 | 帧像素 | 文本 + 坐标 | 100–400 ms | `scan` / `find` / `recognize as rich` |
| 2 | ORB 特征匹配 | 帧像素 + 参考图库 | 命中的参考 key | 30–80 ms | `recognize` |
| 3 | 模板匹配 | 帧像素 + 单张模板 | 命中区域或空串 | 5–20 ms | `scan ... by image` / `find ... by image` |
| 4 | 图色统计 | 帧像素 + 颜色阈值 | 数值 / 布尔 / 区域列表 | 1–5 ms | `pixel` / `color_ratio` / `find_icons` / … |

## 通道 1 · OCR（RapidOCR）

**能回答**："这块区域写了什么"、"哪里出现'确定'两个字"。
**不能回答**：纯图标（没有字形）、非汉英混排之外的符号。

- 实现：`src/lvjiang/core/recognizers/ocr_recognizer.py`（RapidOCR ONNX）
- 清洗：`config/system/ocr_rules.yaml`（分组）
- DSL：`scan`（读文字）、`find ... "关键词"`（找文字位置）
- 慢且吃 CPU。**能用图色秒判的场景不要走 OCR**。

**典型误用**：为了判断"当前是不是大厅"扫一次全屏 OCR。正确做法：`color_ratio` 数右下角主按钮的绿色像素占比，毫秒级。

## 通道 2 · ORB 参考图匹配

**能回答**："这个道具是什么"——同类物品外形相似、有旋转/缩放差异（材料图标）。
**不能回答**：精确位置（返回的是命中 key，不是坐标）。

- 实现：`src/lvjiang/core/recognizers/reference_matcher.py`（ORB + BFMatcher）
- 图库：`config/system/references/<group>/*.png`
- DSL：`recognize ... as $result on group "<组名>"`
- 支持 `as rich`（同时跑 OCR 输出到区域）；与 `by image` 冲突。

**典型用法**：仓库里第 n 行第 m 格是什么材料。

## 通道 3 · 模板匹配

**能回答**：
- `scan ... by image`："这几个候选 Region 里哪个当前显示的是它自己绑定的模板" → 返回 Region key。
- `find ... by image B`："模板 B 现在在画面哪里" → 返回 `FoundRegion` 坐标。

**不能回答**：形状相同颜色不同的两个元素（灰度匹配，色彩无关）；有旋转/形变的元素；缩放超出单尺度容差的元素。

- 实现：`src/lvjiang/core/recognizers/template_locator.py`
- 算法：`cv2.matchTemplate` + `cv2.TM_CCOEFF_NORMED`，单尺度线性缩放
- 图库：`config/system/templates/{common | <layout>/<scene>}/<name>.png`
- 详细展开见 [03-template-matching.md](03-template-matching.md)

**典型用法**：登录页返回按钮（同形状同位置、跨局稳定）、通用弹窗关闭按钮。

## 通道 4 · 图色统计

**能回答**：
- 状态判定的**快速布尔**（"绿色主按钮在不在"、"顶部 HUD 白字占比 > 5%"）
- **计数**（"底部页签栏有几段亮块"、"地图上有几个同色标记"）
- **方位**（"小地图路线朝哪个方向"）
- **漂移坐标**（"当前帧上所有主导绿点位置"）

**不能回答**：具体是什么元素（只有颜色，没有形状）。

- 实现：`src/lvjiang/core/recognizers/color_ops.py`
- DSL 层：`src/lvjiang/workflows/builtins/vision.py`
- 7 个内置函数：`pixel` / `bright` / `color_ratio` / `bright_segs` / `color_vec` / `find_icons` / `find_multi_color`
- 详细展开见 [02-color-ops-internals.md](02-color-ops-internals.md)

**典型用法**：判定"在对局中"→ `color_ratio(HUD 顶部, 白, 30) >= 0.05`；找地图标记 → `find_icons(绿主导)` 返回列表。

---

## 决策树

拿到一个感知需求，按下面顺序问自己：

```
Q1 需要区分「文字」吗（按钮 label、道具名、数字统计）？
    是 → OCR：scan / find
        └─ 需要精确坐标？是 → find；否 → scan
    否 ↓

Q2 需要区分「形状」吗（图标 A vs 图标 B）？
    是 → 形状相似且大小/朝向会变？
        是 → ORB：recognize（返回 key，不返坐标）
        否 → 模板：find by image（返回坐标）或 scan by image（判候选）
    否 ↓

Q3 只需要判「颜色 / 亮度」吗？
    是 → 图色函数
        └─ 位置固定 → color_ratio / bright_segs / pixel
        └─ 位置漂移 → find_icons
        └─ 需要方向 → color_vec
        └─ 多点相对色 → find_multi_color
    否 → 说明问题不在感知层，回需求层重拆
```

**关键约束**：从 Q1 到 Q3，成本递增（100 ms → 30 ms → 5 ms → 1 ms）。
**能用下层解决的，不要用上层**。业务里最常见的浪费是"明明颜色就能判断，非要 OCR 一次"。

---

## 混用模式

四条通道经常组合使用，律匠 DSL 支持自然混用：

### 模式 A · 图色门 + OCR 详情

```
if color_ratio($hud_white_area, "#ffffff", 30) >= 0.05:
    # 在对局中，才值得花 200 ms 走 OCR
    scan [general_combat].[hp_text] as $hp
end
```

**动机**：把 OCR 只用在图色确认的分支上，全局平均成本降一个数量级。

### 模式 B · scan 判候选 + find 拿坐标

```
scan [dialog].[confirm, cancel, retry] as $button by image
if $button equals "retry":
    # 只在重试按钮上时才拿它的实际像素位置（可能因文本长度漂移）
    find [dialog].[body] as $pos by image [dialog].[retry]
    click $pos
end
```

### 模式 C · find_icons 找全部 + 逐个 click

```
$icons = find_icons($map_canvas, 1, 150, 35, 60, 190, 0.0101, 0.0296)
for $i in $icons:
    click $i
    wait 0.3
end
```

**关键**：`find_icons` 是图色通道里唯一返回**坐标**的函数，其他都返回数值/布尔。

---

## 与其他感知方式的边界

**不属于本章的四类**：

- **屏幕坐标点击**（`click [scene].[region]`）— 属于动作层，不走识别；依赖布局已配置。
- **游戏状态查询**（角色属性、背包物品）— 走工作流层的 scan / recognize，本质是上面通道的组合。
- **音频识别** — 项目目前不做。
- **反外挂信号** — 见 memory 中 `ZBLPY 直接内存访问` 决策，本项目**不引入内存读取**，视觉是唯一合法感知。
