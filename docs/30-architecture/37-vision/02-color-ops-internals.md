# 02 · 图色原语内部算法

> Layer: L3 · 代码入口：`src/lvjiang/core/recognizers/color_ops.py`（388 行）
> DSL 层封装：`src/lvjiang/workflows/builtins/vision.py`（386 行）
> 上层合同：[32-grammar/06.4-vision-functions.md](../32-grammar/06.4-vision-functions.md)

## 通用约定

`color_ops.py` 头部约定：

- **输入帧是 BGR** numpy 数组（`CaptureBackend` 全仓统一），**颜色参数按 RGB 给**（`#rrggbb` 或 `(r, g, b)`），与截图工具取色一致。
- **坐标全用像素整数**，闭区间 `[x1, x2] × [y1, y2]`；归一化 ↔ 像素的换算由 DSL 内置层做，本模块不感知布局。
- 越界坐标**夹到边缘**（`_clip_rect`），不抛错——对齐按键精灵 Kotlin 版行为。
- 该模块**只依赖 numpy / cv2**，不 import 引擎；可以用截图文件在无 Qt、无设备的环境下跑离线回归。

### 通道号约定

```python
CH_RED, CH_GREEN, CH_BLUE = 0, 1, 2
_BGR_INDEX = {CH_RED: 2, CH_GREEN: 1, CH_BLUE: 0}  # 在 BGR 数组里各通道实际下标
```

`channel` 参数在 `color_vec` / `find_icons` 里都是这个语义：**0=红主导 1=绿主导 2=蓝主导**，指"要判主导的那个通道"，不是 BGR 内存下标。

### 采样步长 step

`_rgb_planes(..., step=1)` 里 `step>1` 时按网格采样，结果作**统计近似**。`color_ratio` 类函数在大区域可以 step=2 提速 4×，代价是精度损失。空区域返回 0，不抛错。

---

## 一、pixel / bright — 取点

```python
def pixel_rgb(img, x, y) -> RGB
def brightness(img, x, y) -> int   # 0–765，就是 r+g+b
```

**行为**：把 `(x, y)` 夹到帧内，返回该像素的 RGB 或三通道和。

**用途**：极窄的场景 — 判某一点是否某个特定颜色。99% 情况下用 `color_ratio` 更好（面积投票，抗单点噪声）。

---

## 二、color_ratio — 色占比

```python
def color_ratio(img, x1, y1, x2, y2, lo: RGB, hi: RGB, step=1) -> float
def color_ratio_tol(img, ..., color: RGB, tol: int, step=1) -> float
```

**签名对称 vs 非对称两种**：

- `color_ratio_tol("#rrggbb", tol)`：三通道各自 `[c-tol, c+tol]` 的**立方体**。适合"接近某固定色"（绿色按钮、白字）。
- `color_ratio(lo, hi)`：三通道**独立**上下界。适合非对称色族（如 "r 很小、g 很大、b 中等" 的青绿）。

DSL 内置 `color_ratio($rect, "#rrggbb", tol)` 与 `color_ratio($rect, "#lo", "#hi")` 分别对应两者。

**返回**：命中像素数 / 总像素数（0.0–1.0）。

**典型阈值**（本项目内实测稳定值）：
- 顶部 HUD 白字占比 ≥ 0.05 ⇒ 在对局内
- 右下绿色主按钮 ± 40 色容差占比 ≥ 0.10 ⇒ 在大厅
- 页签栏白字占比 < 0.02 ⇒ 页面未加载完成

### 相关函数 · pixel_ratios（未进 DSL）

`pixel_ratios(img, ..., rules: dict[str, dict[str, float]])` 支持 8 种指标（`r` `g` `b` `r_g` `r_b` `g_b` `max_rgb` `min_rgb`）× 多组规则，**一次裁剪一次采样同时算多组**，用于 Python 层批量分析。DSL 目前不直接暴露。

指标语义：
- `r_g = r - g`（通道差，能表达"红比绿大多少"）
- `max_rgb` = 三通道最大值（等价于亮度上限）
- `min_rgb` = 三通道最小值（低值⇒暗或有彩色分量）

**用例注释里给的实例**："金黄色 `r_g_min=-5, g_b_min=8`；红色主导 `r_g_min=10, r_b_min=10`"——通道差比绝对 RGB 更抗亮度漂移。

---

## 三、bright_segs — 亮段计数

```python
def bright_segments(img, y, x1, x2, on_min, off_max) -> int
```

**沿一条水平线** `y` 从 `x1` 扫到 `x2`：

- 每点亮度 `v = row[i].sum()`（r+g+b）
- `v > on_min` 进入亮段
- `v < off_max` 退出并计 1
- **两阈值之间是迟滞带**（防止边缘噪声抖动误计）

**关键陷阱**：一直到行尾仍未退出的亮段**不计**（`in_bright` 保持 True 但不加计数）。这条规则与按键精灵 Kotlin 版一致。写阈值时**要确保页签左右有暗背景留白**，否则最右侧一段会被吞掉。

**用途**：数底部页签栏上有几段字（大厅 ≥ 4 段、结算页 < 3 段）—— 是 `lobby` 与 `settle` 分派的核心判据。

**调参思路**：`on_min` 卡在文字最暗笔画与背景最亮点之间；`off_max` 一般取 `on_min - 100` 左右。两者不能颠倒（`off_max > on_min` 会永远进入不了亮段）。

---

## 四、color_vec — 色心方位角

```python
def color_vec(img, x1, y1, x2, y2, cx, cy, c_lo, c_hi, margin,
              channel=CH_GREEN, step=1, min_r=0.0, max_r=inf)
              -> (deg, count)
```

**返回**：`(deg, count)`，`deg` 以屏幕上方为 0°、**顺时针 0–360**；`count = 0` 时 `deg = -1`。

**算法**：

1. 在矩形 `[x1,y1]×[x2,y2]` 内构造 `_dominant_mask`：`c ∈ [c_lo, c_hi]` 且 `c - other ≥ margin`。
2. 对每个掩膜像素 `(px, py)`：算相对中心 `(cx, cy)` 的单位向量 `(dx/d, dy/d)`。
3. 只保留环带 `min_r ≤ d ≤ max_r` 内的像素。
4. **所有单位向量求和**，再 `atan2(sum_x, -sum_y)` 得合成方位角。

**为什么用单位向量而不是平均角**：环形/均匀噪声会**自相抵消**（对称方向的向量互相减去），只剩不对称的那一撮像素给出的方向。这就是"朝北的路线"能把"整圈杂点"过滤掉的原理。

**`min_r / max_r` 环带**：把朝向楔（近中心，半径小）和外圈路线（远中心，半径大）分开。不传时默认 `[0, inf]` = 全域。

**channel 语义**：小地图路线一般是绿主导（`channel=CH_GREEN`），朝向楔可能是红主导。同一个 `color_vec` 换个 `channel` 就变判据。

### `_dominant_mask` 详解

```python
(c >= c_lo) & (c <= c_hi)
& (c - other1 >= margin1) & (c - other2 >= margin2)
& (other1 <= o_max) & (other2 <= o_max)
```

三条件：

1. 目标通道值本身在范围内
2. 目标通道减另两个通道的差**都** ≥ margin
3. 另两个通道各自 ≤ o_max（防止"紫"被误当"红主导"）

**other1/other2 配对**（与 Kotlin 一致）：
- green 主导 → 比较 (r, b)
- red 主导 → 比较 (g, b)
- blue 主导 → 比较 (r, g)

**为什么用"主导"而不是绝对 RGB 范围**：小地图路线是**半透明叠加层**，底图（地形颜色）会混进路线色里；绝对 RGB 值随底图漂，但"绿远大于红蓝"这个**关系不变**。所以 `color_vec` / `find_icons` 都走通道 margin。

---

## 五、find_icons — 同色连通分量

```python
def find_icons(img, x1, y1, x2, y2, channel, c_min, margin1,
               margin2=None, o_max=255, min_area=60, min_bbox=0, c_max=255)
               -> list[(cx, cy, area, w, h)]
```

**返回**：`[(cx, cy, area, w, h), ...]`，按面积**降序**。`cx/cy` 是 bbox 中心的**像素**坐标（float）。

**算法**：

1. 用 `_dominant_mask` 构造二值掩膜（同 `color_vec`）
2. `cv2.connectedComponentsWithStats(mask, connectivity=8)` — **8 连通**
3. 过滤：`area >= min_area` **且** `w >= min_bbox` **且** `h >= min_bbox`
4. 中心：`cx = x1 + left + (w - 1) / 2.0`（像素整数边界中点）

**关键参数**：

- `margin1 / margin2` — 两个非目标通道各自的 margin。默认 `margin2 = margin1`（对称）。不对称场景：区分"绿主导 vs 青绿（蓝也不低）"。
- `min_area` — 面积下限（像素平方）。滤掉细小噪声、"!"标记。1080p 下经验值 ≈ 120 px²，归一化后 0.0101。
- `min_bbox` — bbox 宽高同时 ≥ 该值。把大图标（~40 px）和同色队友小标记（~24 px）**分开**——两者面积可能重叠，但外接矩形宽度不同。
- `c_min / c_max` — 目标通道值本身的上下界。默认 `c_max=255`（不设上界）。

**为什么是 8 连通**：图标像素常带抗锯齿边缘，对角线相连（4 连通会误拆成两半）。8 连通更宽容。

---

## 六、find_multi_color — 多点找色

```python
def find_multi_color(img, x1, y1, x2, y2, anchor: RGB,
                     points: Sequence[(dx, dy, RGB)], tol=12)
                     -> (x, y) | None
```

按键精灵经典 API：锚点色 + 若干相对偏移点色，**全部命中**即返回锚点像素坐标；无命中返回 `None`。

**实现优化**（避免 Python 层双循环扫全图）：

1. 对锚点色 `anchor` 做**整区域一次性掩膜**（`near_mask`，三通道绝对差都 ≤ tol 的像素集）
2. 只在候选像素上逐个验证偏移点色
3. 命中即返回，**行优先左上角优先**

**tol**：三通道**绝对差**都 ≤ tol 才算"够近"。默认 12。

**偏移语义**：`dx/dy` 是**当前帧像素**偏移。若跨分辨率录制，DSL 内置层会先按画布宽比例缩放（`find_multi_color` 的偏移按**画布宽**归一化，见 06.4 §公共约定）。

---

## DSL 层的封装差异

`workflows/builtins/vision.py` 是 DSL 与 `color_ops.py` 的适配层，负责：

- 把 `[scene].[region]` 引用解析成像素矩形
- 点/圆退化为零面积矩形 / 外接正方形
- 距离/尺寸参数归一化：`color_vec` 环带半径、`find_icons` 面积/bbox 门槛按**画布高**；`find_multi_color` 偏移按**画布宽**
- 每次调用**截一帧**并裁到布局画布（截图失败抛错，不静默）

这层**不做算法**，只做参数换算 + 截帧。**算法调优请回 `color_ops.py`**。

---

## 单元测试的离线回归能力

因为 `color_ops.py` 只依赖 numpy / cv2，`tests/core/test_color_ops.py`（若存在）可以直接：

```python
import cv2
img = cv2.imread("fixtures/hall_1080p.png")   # BGR
ratio = color_ratio_tol(img, x1, y1, x2, y2, (0, 200, 0), 40)
assert ratio >= 0.10
```

**建议**：新加图色判据时先跑离线 fixture，验证阈值合理后再进 wf。避免在线调参 → 每次改动跑一次设备。
