# Dev Log: 画布中间层坐标解耦（Canvas Middle Layer）

> 日期：2026-07-15
> 涉及模块：`lvjiang/core/region_config.py`、`lvjiang/ui/region_editor/canvas.py`、`lvjiang/ui/region_editor/scene_tab.py`、`lvjiang/ui/region_editor/dialog.py`
> 关键词：画布中间层、CanvasConfig、坐标解耦、两层变换、布局级别

---

## 背景

游戏窗口缩放时，**内容区等比变化**，但**边框固定像素不变**。窗口越大，边框占比越小，导致 region 相对截图的比例发生偏移。

原有方案中，子区域坐标直接相对于截图（含边框），窗口尺寸变化后边框占比改变，所有区域比例都会偏移。

**解决方案**：在截图和子区域之间引入"画布"中间层——

```
原来: 截图（含边框） ──> 子区域（坐标相对于截图，受边框占比变化影响）
现在: 截图（含边框） ──> 画布（排除边框，布局级别） ──> 子区域（坐标相对于画布）
```

画布是**布局级别**的，不是场景级别的。同一布局下所有场景共享同一个画布。调整画布后，所有场景的区域坐标自动适配。原因：画布代表的是游戏内容区域（排除窗口边框），窗口边框对所有场景都是一样的。

---

## 数据模型变更

### 存储格式（`config/local/layouts/xxx.json`）

```json
{
  "canvas": {
    "x_ratio": 0.05,
    "y_ratio": 0.08,
    "w_ratio": 0.9,
    "h_ratio": 0.85
  },
  "equip_detail": {
    "regions": [...]
  },
  "equip_tune": {
    "regions": [...]
  }
}
```

- `canvas` 在布局顶层，所有场景共享
- `canvas` 的归一化坐标相对于**截图**（0~1）
- `regions` 的归一化坐标相对于**画布**（0~1）
- canvas 缺省时默认 `(0, 0, 1, 1)` 即全截图（向后兼容旧布局文件）

### `region_config.py` 变更

| 变更 | 说明 |
|------|------|
| 新增 `CanvasConfig` dataclass | `x_ratio, y_ratio, w_ratio, h_ratio`（默认 0, 0, 1, 1） |
| `Layout` 新增 `canvas` 字段 | `canvas: CanvasConfig = field(default_factory=CanvasConfig)` |
| `Layout.to_dict()` | 序列化时写入 `canvas` 字段 |
| `Layout.from_dict()` | 反序列化时读取 `canvas`，缺失则默认全图 |
| `CanvasConfig.to_dict()` / `from_dict()` | 自身的序列化 |

---

## 坐标转换

运行时像素映射（两层变换）：

```python
# 第一层：画布在截图像素中的位置（布局级别，所有场景共用）
canvas_x = canvas.x_ratio * screenshot_w
canvas_y = canvas.y_ratio * screenshot_h
canvas_w = canvas.w_ratio * screenshot_w
canvas_h = canvas.h_ratio * screenshot_h

# 第二层：子区域在截图像素中的实际位置
pixel_x = canvas_x + region.x_ratio * canvas_w
pixel_y = canvas_y + region.y_ratio * canvas_h
pixel_w = region.w_ratio * canvas_w
pixel_h = region.h_ratio * canvas_h
```

### `canvas.py` 坐标转换方法

- `_canvas_to_screenshot_norm(cx, cy)` → 画布内归一化坐标转截图归一化坐标
- `_screenshot_to_canvas_norm(sx, sy)` → 截图归一化坐标转画布内归一化坐标（带 clamp）
- `_canvas_rect_widget()` → 画布框的 widget 坐标矩形
- `_region_rect_widget(r)` → 改为叠加画布变换，先将区域画布坐标转为截图坐标再转 widget

---

## UI 变更

### `canvas.py` — 画布编辑模式

**新增枚举**：
- `EditMode.REGION` — 区域编辑模式（默认）
- `EditMode.CANVAS` — 画布编辑模式（移动/缩放画布框）

**绘制层级**：
```
底层: 截图（全图）
中层: 画布遮罩（画布外区域半透明黑色）+ 画布边框（黄色虚线）
顶层: 子区域（在画布内按归一化坐标绘制）
```

**画布框交互**：
- 黄色虚线边框（区域模式 2px/alpha200，画布模式 3px/不透明）
- 画布编辑模式下 8 个黄色缩放手柄
- 画布外区域半透明遮罩（alpha=120）
- 支持 8 手柄缩放 + 内部拖拽移动
- 画布修改后通过 `on_canvas_changed` 回调通知外部

**坐标映射调整**：
- 新建区域时，鼠标位置先做 screenshot→canvas 坐标转换
- 区域绘制和命中检测叠加画布变换

### `scene_tab.py` — Tab 集成

新增方法：
- `set_canvas_config(config)` / `get_canvas_config()` — 画布配置传递
- `set_canvas_mode()` / `set_region_mode()` — 编辑模式切换
- `edit_mode` 属性 — 当前编辑模式

### `dialog.py` — 对话框集成

**画布模式切换按钮**：
- 顶部按钮栏新增"编辑画布"checkable 按钮
- 切换时同步所有 Tab 的编辑模式

**布局加载/保存**：
- `_apply_layout_to_tabs()` — 分发 canvas 配置到所有 Tab，连接 `on_canvas_changed` 回调
- `_on_save_layout()` / `_on_save_as_layout()` — 收集 canvas 配置写入 Layout

**画布同步**：
- `_on_any_canvas_changed()` — 任一 Tab 画布修改时同步到所有其他 Tab

**OCR 坐标变换**：
- `_on_recognize()` — 叠加画布两层变换，将区域画布坐标转为截图像素坐标后裁剪

---

## 向后兼容

- 旧布局文件无 `canvas` 字段 → 默认全截图 `(0, 0, 1, 1)`，行为与改动前完全一致
- 用户首次打开旧布局时，画布框覆盖全图，可手动调整排除边框
- `Layout.from_dict()` 中 `canvas` 字段缺失时静默使用默认值，不报错

---

## 教训

| 维度 | 旧设计 | 新设计 |
|------|--------|--------|
| 坐标参考系 | 子区域直接相对于截图 | 子区域相对于画布，画布相对于截图 |
| 窗口缩放适应 | 边框占比变化导致区域偏移 | 只需调整画布排除边框，区域自动适配 |
| 作用域 | 无 | 画布为布局级别，所有场景共享 |
| 向后兼容 | N/A | 旧文件无 canvas 字段时默认全图 |

**核心启示**：当发现坐标系统受到外部因素（窗口边框）干扰时，不要试图修改坐标系统本身，而是引入中间层隔离干扰。中间层的作用域应该与干扰因素的作用域一致（边框是布局级别的，所以画布也是布局级别的）。

---

## 后续修复与 UI 优化

### 1. 画布边框可见性增强

**现象**：默认画布全屏时黄色虚线在图片边缘几乎不可见。

**修复**：绘制两层边框——先画黑色底衬（线宽+2），再画黄色虚线，确保在任何背景下都清晰可辨。

### 2. 对话框可缩放/最大化

**现象**：区域编辑器对话框无法最大化，区域太小难以操作。

**修复**：
- 添加 `WindowMaximizeButtonHint` / `WindowMinimizeButtonHint` 窗口标志
- 启用 `setSizeGripEnabled(True)`
- 初始尺寸调整为 1200x800

### 3. 最大化后画布飞向左上角

**现象**：最大化对话框后，画布图片跑到左上角远处。

**根因**：`resizeEvent` 使用 `_apply_zoom_anchor` 以 widget 中心为锚点，但最大化时中心点位置剧变，导致归一化坐标计算偏移。

**修复**：`resizeEvent` 改为直接重置缩放 `_zoom = 1.0` 并调用 `_recalc_display()` 重新适配窗口。

### 4. 吸附参考线显示偏移

**现象**：吸附对齐线显示位置与实际吸附位置不一致。

**根因**：吸附线存储的是画布相对归一化坐标，但绘制时直接用 `_display_rect` 映射（截图坐标），未叠加画布变换。

**修复**：绘制吸附线时先调用 `_canvas_to_screenshot_norm()` 转换坐标。

### 5. 未保存状态指示

**功能**：顶部右侧新增绿色 "● 有改动" 标签，编辑区域或画布时自动显示，保存/另存为/删除/加载新布局后自动清除。

**实现**：
- 连接 `on_region_changed` 和 `on_canvas_changed` 回调标记 dirty
- `_set_dirty(bool)` 方法控制标签可见性

### 6. OCR 区域可拖拽调整

**现象**：OCR 结果区占空间太大，画布空间不足。

**修复**：
- 画布 Tab 和 OCR 面板之间改用 `QSplitter` 垂直分割
- 默认比例：画布 2/3，OCR 1/3
- 用户可拖拽分割条自由调整比例

