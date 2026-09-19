# 04 · 坐标系与取整一致性

> Layer: L3 · 关键：所有视觉感知与点击操作**共用同一套坐标语义**。改一处必改多处，改错了就是"编辑器 ✓ 运行时 ✗"的经典 bug。

## 三级坐标体系

律匠的坐标是**三层嵌套**：

```
┌─ 帧（frame）────────────────────────┐
│   设备截屏的原始像素，跨设备不同     │
│  ┌─ 画布（canvas）────────────────┐ │
│  │  布局选定的有效区域，归一化定义  │ │
│  │  ┌─ Region（区域）──────────┐  │ │
│  │  │  具体 UI 元素框，相对画布 │  │ │
│  │  └──────────────────────────┘  │ │
│  └────────────────────────────────┘ │
└─────────────────────────────────────┘
```

- **帧**：像素整数，`(0, 0)` 到 `(frame_w-1, frame_h-1)`。设备相关。
- **画布**：归一化 `[0, 1]` 相对帧，由布局 `canvas.x_ratio / y_ratio / w_ratio / h_ratio` 定义。用于排除设备状态栏、非游戏区域。
- **Region**：归一化 `[0, 1]` 相对画布，`x_ratio + w_ratio ≤ 1`。

**所有 DSL 层引用（`[scene].[region]`、`FoundRegion`、`RectCoordRef`、`CircleCoordRef`）都是相对画布的归一化坐标**。运行时才乘画布像素换算到帧像素。

---

## 归一化 ↔ 像素的换算（唯一口径）

在 `recognition.py` L657、`template_locator.py` L298-306 各处重复出现：

```python
canvas_px_x = canvas.x_ratio * frame_w
canvas_px_y = canvas.y_ratio * frame_h
canvas_px_w = canvas.w_ratio * frame_w
canvas_px_h = canvas.h_ratio * frame_h

# Region 转像素（闭区间）
x1 = round(canvas_px_x + region.x_ratio       * canvas_px_w)
y1 = round(canvas_px_y + region.y_ratio       * canvas_px_h)
x2 = round(canvas_px_x + (region.x_ratio + region.w_ratio) * canvas_px_w) - 1
y2 = round(canvas_px_y + (region.y_ratio + region.h_ratio) * canvas_px_h) - 1
```

**注意**：`x2` 用 `- 1` 转成**闭区间**。这是"像素边界"约定（0..w-1 共 w 个像素），不是 bug。

---

## 取整一致性契约（历史教训）

### 契约

**编辑器**用 `int(round(right))` 裁剪模板；**运行时**用 `int(x_ratio * canvas_w)` 加/减边界算搜索区。二者必须在**同一个像素**上达成一致，否则会出现：

- 编辑器里"✓ 命中，score=0.95"，运行时"模板比搜索区大 1 px"被跳过
- 反过来：编辑器算出的模板尺寸运行时装不下

### 曾经踩过的坑

`template_locator.py` L237 早期版本：

```python
x2 = int(right) - 1      # 旧代码
```

`int()` 是**截断**，`round()` 是**四舍五入**。同一个小数 `1919.6`：
- `int(1919.6) = 1919`
- `round(1919.6) = 1920`

编辑器裁模板按 `round` 得 1920 宽，运行时按 `int - 1` 得 1918 的闭区间右界 → **搜索区比模板窄 2 px**。当 `scale=1.0` 时，模板宽度 `tw == rw + 1`（或更多），触发 `tw > rw` 判据被跳过。

**症状**：明明桌面 1920 录的模板放桌面 1920 用，scale 应该正好 1.0，结果被完全跳过，只能靠 0.99/1.01 次优尺度匹配，分数下降 5-10 点。

**修复**（commit `a41ff356` 与后续）：改用 `int(right) + 1`，与编辑器 `round` 语义对齐，diff 稳定在 +1 或 +2 px。

**权衡**：搜索区最多宽 2 px。放宽会引入误命中风险吗？memory 中 `模板匹配边界计算：+1 修正优于 -1 的保守策略` 判定：negligible。

### 契约现状（必须遵守）

| 组件 | 语义 |
|---|---|
| 编辑器裁剪模板 | `int(round(right))` |
| 运行时 Region → 像素 | `int(round(...))` |
| `locate()` 帧内 clip | `min(max(int(...), 0), w-1)` |
| `search_box()` 输出右界 | `- 1`（转闭区间） |
| `SEARCH_SLACK_PX = 2` | 每边 2 px，覆盖所有取整误差 + 跨分辨率模板尺寸与 Region 尺寸各差 ≤ 1 px |

**改任何一处**都要同步检查另外四处。这是全项目最脆的隐式契约之一。

### 归一化坐标浮点误差

`_RECT_EPS = 1e-6`（`layout_models.py` L66）。归一化坐标经过 UI 拖拽 + JSON 往返会有末位浮点误差，`x + w` 落在 `1.0000000000000002` 上不该判成越界。所有边界比较用 `± EPS` 容差。

---

## 三种坐标引用类型（RectCoordRef / CircleCoordRef / FoundRegion）

`layout_models.py` 与 `recognition.py` 里 DSL 层能拿到的坐标对象共三种：

### RectCoordRef（矩形）

由 `[scene].[region]` 解析而来。携带 `x_ratio, y_ratio, w_ratio, h_ratio`（画布归一化）。

```python
$rect = [scene].[region]          # 直接引用（部分 DSL 函数允许字面量）
click $rect
$ratio = color_ratio($rect, "#ff0000", 40)   # 图色函数接受
```

### CircleCoordRef（圆点）

由 `[scene].[point]` 解析而来。携带 `cx, cy, r`。

**退化规则**（`builtins/vision.py`）：
- 单点 → 零面积矩形：`w=0, h=0, x=cx, y=cy`
- 有半径的圆 → 外接正方形：`x=cx-r, y=cy-r, w=h=2r`

图色函数不接受 `CircleCoordRef` 字面参数，必须先赋值给变量。

### FoundRegion（命中区）

由 `find ... by image` / `find ... "关键词"` / `find_icons` 返回。携带 `x_ratio, y_ratio, w_ratio, h_ratio, text, confidence`。

**可以直接 `click`** — 与 RectCoordRef 在动作层等价。

### 三者之间的转换

```
[scene].[region]           → RectCoordRef         ─┐
[scene].[point]            → CircleCoordRef       ─┼→ 传给图色函数时 → 内部转 (x1,y1,x2,y2) 闭区间
find ... as $hit           → FoundRegion          ─┘
find_icons(...)            → [FoundRegion, ...]   ─┘
```

---

## 布局画布 vs 原始截图

**布局画布**是配置出来的（`canvas` 字段），**原始截图**是设备产出的。二者可能不同：

- 手机 ADB 截图含系统状态栏 → 布局画布裁掉顶部 5%
- 桌面窗口含边框 → 布局画布裁掉边框
- 全屏无装饰 → 画布 = 整帧

**所有归一化坐标都是相对画布**。像素换算时先算 `canvas_px_*` 再乘 `region.x_ratio`，不直接乘帧宽高。

`find as $x by image B`（省略搜索范围 A）时，搜索的是**画布**，不是全帧。要跨越画布外区域（比如状态栏）就超出布局能力了。

---

## `record_w / record_h`：模板录制时的画布宽度

`TemplateBinding` 携带 `record_w / record_h`：

```python
@dataclass(frozen=True)
class TemplateBinding:
    name: str
    min_score: float = 0.8
    record_w: int = 0
    record_h: int = 0
```

- **录制时**：编辑器把当前画布像素宽记到 `record_w`
- **匹配时**：`resolution_scale(canvas_w, record_w) = canvas_w / record_w`
- **校验**：`record_w` 与 `record_h` **必须同为正数或同为 0**（`__post_init__` L91），半设是配置错误。

`record_w = 0` 意味着**未记录录制尺寸**，`resolution_scale` 退化为 `1.0`。这时跨分辨率必挂，只在**同一分辨率**下工作。手写 JSON 布局时要补这个字段，或者用编辑器重录。

---

## 归一化阈值的换算（find_icons 类）

`find_icons` 的 `min_area / min_bbox`、`color_vec` 的 `min_r / max_r` 在 DSL 内置层是**归一化**参数（0–1 范围），底层 `color_ops.py` 是**像素**参数。

换算规则（06.4 §公共约定）：

| 参数类型 | 归一化基准 | 换算 |
|---|---|---|
| 面积（`min_area`） | 画布高 | `px² / canvas_h²` |
| 长度（`min_bbox`、`min_r`、`max_r`） | 画布高 | `px / canvas_h` |
| 相对偏移（`find_multi_color` 的 `dx/dy`） | 画布宽 | `px / canvas_w` |

例：1080p 下 `min_area=120 px²` → 归一化 `120 / 1080² ≈ 0.000103`。

**为什么面积按画布高而不是画布面积**：项目历史约定，与 Kotlin 版对齐。理解上有点绕，但换算公式统一：**所有"绝对"阈值都按 `canvas_h` 归一化，只有相对偏移按 `canvas_w`**。

---

## 修改一处时应该同步的清单

任何触碰坐标系的改动（例如换 OpenCV 版本、改布局模型、加新画布字段），核对：

- [ ] `template_locator.search_box()` 里的 `round(...)` 一致性
- [ ] `template_locator.locate()` 里的帧内 clip 逻辑
- [ ] `recognition.py` 里 `find_image_in_region` / `match_region_templates` 的画布换算
- [ ] `builtins/vision.py` 里 RectCoordRef / CircleCoordRef 退化路径
- [ ] `layout_models.py` 的 `_RECT_EPS` 与 `x_ratio + w_ratio` 边界校验
- [ ] 编辑器裁剪模板（`scene_tab.py` / `scene_region_panel.py`）是否同步
- [ ] `tests/core/test_template_locator.py` 与 `test_color_ops.py`（若有）
- [ ] 双目录截图回归（详见 [06-tooling-and-regression.md](06-tooling-and-regression.md)）
