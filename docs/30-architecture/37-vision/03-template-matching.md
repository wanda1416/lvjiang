# 03 · 模板匹配算法

> Layer: L3 · 代码入口：`src/lvjiang/core/recognizers/template_locator.py`（341 行）
> 引擎调用：`src/lvjiang/workflows/base/recognition.py`
> 上层合同：[32-grammar/04.4-image-template.md](../32-grammar/04.4-image-template.md)

## 一句话总结

律匠的模板匹配 = **OpenCV 单尺度归一化互相关（TM_CCOEFF_NORMED）+ 灰度输入 + 线性缩放模板适配分辨率 + 取整一致 + 严格 Region 内搜索**。

不是金字塔、不是多峰、不是彩色、不是形状不变量。这个边界决定了它能干什么、不能干什么。

## 三个入口

| 调用者 | 用途 | 位置 |
|---|---|---|
| `find_image_in_region(...)` | `find ... by image` 指令实现 | `recognition.py` L625 |
| `match_region_templates(...)` | `scan ... by image` 指令实现 | `recognition.py` L692 |
| `locate_in_region(...)` | 底层：单 Region 定位 | `template_locator.py` L318 |
| `locate(...)` | 最底层：任意矩形内定位 | `template_locator.py` L237 |

`scan` 与 `find` **共用 `locate()`**，唯一区别是候选处理：
- `scan` 逐个 Region 循环，**首个命中即返**，返回 Region key
- `find` 单次调用，返回 `FoundRegion` 坐标

---

## 核心：locate()

```python
def locate(frame_bgr, tpl, x1, y1, x2, y2, scales=(1.0,), min_score=DEFAULT_MIN_SCORE):
    region = cv2.cvtColor(frame_bgr[y1:y2+1, x1:x2+1, :3], cv2.COLOR_BGR2GRAY)
    for s in scales:
        tw = round(tpl.w * s);  th = round(tpl.h * s)
        if tw < 4 or th < 4 or tw > rw or th > rh:
            continue                                    # 尺寸不合法跳过
        t = cv2.resize(tpl.gray, (tw, th), INTER_AREA if s<1 else INTER_LINEAR)
        res   = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED)
        _, max_v, _, max_loc = cv2.minMaxLoc(res)
        score = max(float(max_v), 0.0)                  # 负分归零
        ...
    return best if best.score >= min_score else None
```

### TM_CCOEFF_NORMED 的性质

- **去均值归一化互相关系数**：减掉窗口/模板的均值再归一化到 [0, 1]。
- 对**整体亮度偏移**不敏感（截图前后曝光差异不影响分数）。
- 对**对比度变化**（灰阶线性拉伸）也不敏感。
- 对**旋转 / 缩放 / 形变**极敏感——本算法不做这些变换。

### 灰度输入

`cv2.cvtColor(..., COLOR_BGR2GRAY)`。

- **颜色变了但形状没变 → 仍命中**：按钮从蓝变绿，同形状照样过阈。
- **颜色不同但形状相同 → 会误命中**：红/蓝两个同形状按钮互相串扰。这类场景要区分颜色 → 改走 `find_icons`（图色通道，按主导通道 margin 判）。

### 缩放策略

`scales=(scale,)` **只有一个尺度**：`scale = resolution_scale(canvas_w, tpl.record_w) = canvas_w / record_w`。

- 录制时画布 1920 px，当前画布 1920 px → scale = 1.0
- 录制时 1920，当前 1080（投屏/手机）→ scale ≈ 0.5625
- `record_w = 0` → 返回 1.0（**不做跨分辨率适配**，桌面录的模板放手机上会不命中）

**为什么不做多尺度金字塔**：律匠的 Region 是**归一化坐标**，模板绑定携带 `record_w/record_h`，"录制画布宽 → 当前画布宽"是**精确的线性映射**，一个尺度就能覆盖所有分辨率。加金字塔只会拖慢搜索（每层跑一次 matchTemplate）+ 引入误命中风险（多层峰值竞争）。

**缩放插值**：`INTER_AREA`（缩小）与 `INTER_LINEAR`（放大）——缩小用面积平均更保形，放大用双线性更平滑。

### 命中判定

`cv2.minMaxLoc` 取相关图的**全局最大值**，不做 NMS，不返 top-K。

**只返回一个命中** — 要"图上一堆相同图标全部找出来"请用 `find_icons`（图色连通分量）。

### 阈值

`min_score` 由调用者决定。若为 `None`：

- `scan by image`：`threshold = binding.min_score`（每个 Region 自己的绑定阈值）
- `find by image`：`threshold = DEFAULT_MIN_SCORE = 0.8`（模块级常量）

三处口径统一都是 **0.8**：
- `layout_models.py` `TemplateBinding.min_score` 字段默认
- `template_locator.py` `DEFAULT_MIN_SCORE` 常量
- `scene_region_panel.py` 编辑器新建绑定时的初始值

---

## 搜索区：search_box()

```python
def search_box(frame_shape, tpl, canvas, region):
    scale = resolution_scale(round(canvas_w), tpl.record_w)
    x1 = round(canvas_x + region.x_ratio * canvas_w)
    y1 = round(canvas_y + region.y_ratio * canvas_h)
    x2 = round(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w) - 1
    y2 = round(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h) - 1
    need_w = round(tpl.w * scale) + 2 * SEARCH_SLACK_PX
    need_h = round(tpl.h * scale) + 2 * SEARCH_SLACK_PX
    grow_x = max(0, need_w - (x2 - x1 + 1))
    grow_y = max(0, need_h - (y2 - y1 + 1))
    x1 -= grow_x // 2; x2 += grow_x - grow_x // 2      # 对称外扩
    y1 -= grow_y // 2; y2 += grow_y - grow_y // 2
    return x1, y1, x2, y2, scale
```

**Region 边界**用 `round()` 而不是 `int()` 取整（详见 [04-coordinate-system.md](04-coordinate-system.md)）。

**`SEARCH_SLACK_PX = 2`**：Region 装不下"缩放后的模板 + 每边 2 px"时才对称外扩；Region 本就更大（部分截取的模板、手画的大区域）则原样使用。

**2 px 的根据**（源码注释）：只覆盖取整误差——每边 ≤ 1 px 的归一化取整差 + 跨分辨率时模板尺寸与 Region 尺寸各再差 ≤ 1 px。

---

## 坐标回投：FoundRegion

`locate()` 拿到像素级 `cx, cy` 后：

```python
FoundRegion(
    x_ratio=(hit.cx - hit.w/2 - canvas_px_x) / canvas_px_w,
    y_ratio=(hit.cy - hit.h/2 - canvas_px_y) / canvas_px_h,
    w_ratio=hit.w / canvas_px_w,
    h_ratio=hit.h / canvas_px_h,
    text=template_name,
)
```

坐标系是**相对画布**（不是相对帧）：`canvas_px_x/y/w/h` 是当前布局画布的像素坐标。这样 `FoundRegion` 可以直接喂给 `click` 与后续图色函数，与布局体系保持同一坐标语义。

---

## 帧截取的时机

`find_image_in_region` 和 `match_region_templates` 各自**内部只截一帧**（`capture_frame(...)`）：

- 一次 DSL 调用 → 一次屏幕截图 → 若干次匹配 → 返回
- `scan [s].[a, b, c] as $x by image` 也是**一帧扫三个 Region**，不重复截屏
- 截图失败：
  - `scan` → 返回 `""`（当作未命中，日志记录）
  - `find` → 返回 `""`（同上）
  - 图色函数 → **抛错**（图色更依赖精确坐标，帧不对就静默错误不可接受）

---

## 与编辑器"预览匹配"的一致性

`scene_tab.py` L415 编辑器里"预览命中"逻辑：

```python
passed = hit is not None and score >= binding.min_score
```

与运行时**同一 `locate()` + 同一 `binding.min_score`**——编辑器里看到"命中 ✓"，运行时理论上一定命中（同分辨率、同 Region 位置）。

**如果不一致**（编辑器 ✓ 运行时 ✗），排查顺序：
1. 编辑器截图与实际帧的差异（时间戳、光照、动画中间帧）
2. `record_w` 是否与当前画布宽不匹配（跨布局未重录）
3. Region 位置在两个布局下的归一化坐标差异
4. 双目录截图比例回归（见 [06-tooling-and-regression.md](06-tooling-and-regression.md)）

---

## 常见坑与修复历史

### 坑 1 · scale=1.0 被跳过（已修）

**症状**：编辑器预览命中，运行时分数极低；把阈值降到 0.6 才能过。
**根因**：`x2 = int(right) - 1`（旧）与编辑器 `int(round(right))` 存在 2 px 差异。当模板宽度 == Region 宽度时，`tw > rw` 触发 `continue`，scale=1.0 被完全跳过，只能靠次优缩放。
**修复**：`int(right) + 1`（现），见 commit `1d68a25a`。
**教训**：详见 [04-coordinate-system.md](04-coordinate-system.md) §取整一致性契约。

### 坑 2 · 同形状不同色按钮互相误命中

**症状**：`scan [dialog].[ok_btn, cancel_btn] by image` 两个都是灰色文字按钮，形状一致，永远返回声明顺序第一个。
**根因**：灰度匹配天然不看颜色。
**修复**：改用 OCR `scan [dialog].[ok_btn, cancel_btn]`（走文字），或用 `find_icons` 判颜色主导，或在布局里给两个 Region 分别绑更**独特的模板**（比如带图标那种）。

### 坑 3 · 跨布局未重录模板

**症状**：桌面布局录的模板（`record_w=1920`）配到投屏（`canvas_w=1280`），运行时缩放 `scale=0.667`；但缩放后的模板像素已经和投屏真实像素不一致（因为游戏渲染在小分辨率下笔画可能整像素对齐变了）。
**缓解**：不同布局应该独立录制各自模板；`TemplateBinding.name` 支持 `common/<name>` 与 `<layout>/<name>` 分层，编辑器默认按当前布局目录。

### 坑 4 · record_w 缺失

**症状**：`TemplateBinding.record_w = 0` → `resolution_scale` 返回 1.0 → 模板按原像素尺寸匹配。跨分辨率必挂。
**修复**：编辑器"录制模板"路径默认填当前画布宽；手写 `layout.json` 时**必须补** `record_w` / `record_h`（成对存在，见 `layout_models.py` `__post_init__` L91 校验）。

---

## 复杂度与性能

- `cv2.matchTemplate` 单次调用 O(W × H × w × h)，其中 W/H 是搜索区，w/h 是模板；实测 1920×1080 画布内 100×50 模板 ≈ 12 ms（AVX2 优化）
- `scan by image` N 个 Region：N × 单 Region 匹配。因为每个 Region 严格限制搜索范围（+2px slack），实际比整屏匹配快得多
- 每调用一次截一帧。**不要**在循环里对同一帧做多次 scan/find，应该把候选合并到一次 `scan [s].[r1, r2, r3] as $x by image`

---

## 与其他通道的选择

| 需求 | 用 | 不用 |
|---|---|---|
| 图标位置固定 + 形状稳定 | `by image` | ~~OCR~~（浪费） |
| 图标位置漂移 | `find_icons`（图色） | ~~`by image`~~（单候选返回一个） |
| 形状相同颜色不同要区分 | 图色 `color_ratio` 判主导色 | ~~`by image`~~（灰度无关） |
| 图标带文字且文字稳定 | OCR `find` | ~~`by image`~~（更慢） |
| 形状会变（不同材料同类型） | ORB `recognize` | ~~`by image`~~（形变挂） |
