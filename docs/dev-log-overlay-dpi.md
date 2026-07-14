# Dev Log: BorderOverlay 多 DPI 跨屏定位问题

> 日期：2026-07-14
> 涉及模块：`lvjiang/ui/app.py` — `BorderOverlay`
> 关键词：PyQt6、多 DPI、Overlay、Win32、坐标系

---

## 问题描述

双屏环境（左屏 2560x1440 DPI=1.0，右屏竖屏 1728x3072 DPI=1.25）。
需求：在右屏投屏窗口外围绘制 5px 彩色边框（定位=红色，运行=绿色）。

**现象**：无论怎么调整坐标，边框始终无法正确对齐目标窗口——
- 边框跑到左屏（主屏）
- 右边缺线、上下边短于窗口实际宽度
- 底边出现在窗口内侧

---

## 尝试过的方案（均失败）

### 方案 1：Qt setGeometry + DPI 坐标转换

思路：Win32 `GetWindowRect` 返回物理像素，除以 DPI 缩放比转为 Qt 逻辑坐标。

```python
ratio = screen.devicePixelRatio()  # 1.25
left = int(w['left'] / ratio)
```

结果：边框出现在左屏。`_get_dpi_ratio()` 用 `geo.x() * ratio` 算物理范围匹配屏幕，
但 Qt 的 `screen.geometry()` 逻辑坐标和 Win32 坐标的关系并非简单的 `×DPI`。

### 方案 2：Win32 SetWindowPos 直接定位 Qt 窗口

思路：绕过 Qt 的 setGeometry，用 `SetWindowPos` 强制设置 Qt 窗口的 Win32 位置。

```python
ctypes.windll.user32.SetWindowPos(hwnd, -1, x, y, w, h, flags)
```

结果：边框跑回左屏。Qt 在内部拦截了 `WM_WINDOWPOSCHANGING`，用自己的坐标覆盖了 SetWindowPos 的设置。

### 方案 3：全屏覆盖层

思路：overlay 覆盖整个虚拟屏幕（始终在主屏 DPI 上下文），在正确坐标处画边框。

```python
self.setGeometry(min_x, min_y, vw, vh)  # 全屏
painter.drawRect(rx, ry, rw, rh)         # 在目标位置画边框
```

结果：位置对了，但右边缺线、上下边短于窗口、底边在窗口内侧。

### 方案 4：全屏 + 扩展 pen_width

思路：向右下扩展 pen_width，给 drawRect 的边留出绘制空间。

结果：仍然缺边。当 overlay 窗口扩展到负坐标 `(-5, -5)` 时，Qt 把窗口放到了屏幕外并做了缩放，所有坐标全部错位。

### 方案 5：全屏 + 只向右下扩展

思路：左上角不扩展（避免负坐标），只向右下扩展 pen_width。

结果：右边和底边仍然被裁剪。`drawRect` 的 pen 以路径为中心绘制，右/底边的外半圈超出 widget 边界被裁掉。

---

## Codex 的修复方案：纯 Win32 窗口

**核心思路：放弃 Qt，用纯 Win32 API 创建 overlay 窗口。**

```
Qt 窗口 → 受 Qt 坐标系统管辖 → 多 DPI 下坐标被隐式转换
纯 Win32 窗口 → 不受 Qt 管辖 → SetWindowPos 直接生效，坐标准确
```

### 实现要点

1. **注册 Win32 窗口类**：`RegisterClassW` + 自定义 `WndProc`
2. **创建 layered 窗口**：`CreateWindowExW` 带 `WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW`
3. **颜色键透明**：`SetLayeredWindowAttributes(hwnd, 0x000000, 255, LWA_COLORKEY)` — 窗口内黑色像素变透明，红/绿边框保持可见
4. **GDI 绘制边框**：在 `WM_PAINT` 中用 `FillRect` 填黑底 + `Rectangle` 画彩色边框
5. **Win32 定位**：`SetWindowPos` 直接使用 `GetWindowRect` 返回的坐标，无任何转换
6. **点击穿透**：`WS_EX_TRANSPARENT` 让鼠标事件穿透

### 关键代码结构

```python
class BorderOverlay:
    # 纯 Win32 实现，不继承 QWidget
    _class_name = "LvjiangBorderOverlayWindow"
    _instances: dict[int, "BorderOverlay"] = {}

    def _ensure_window(self):
        # RegisterClassW + CreateWindowExW
        # WS_EX_LAYERED + LWA_COLORKEY → 黑色透明，彩色可见

    def _paint(self, hwnd):
        # WM_PAINT 处理
        # 1. FillRect 填黑色（→ 透明）
        # 2. CreatePen + Rectangle 画边框（→ 可见）

    def show_border(self, left, top, width, height):
        # SetWindowPos 直接定位，坐标 = GetWindowRect 原始值
        # InvalidateRect + UpdateWindow 触发重绘

    def set_color(self, color):
        # 更新颜色 → InvalidateRect 触发 WM_PAINT
```

---

## 为什么 Codex 能修复，我想不到

### 根本原因：思维定式

我始终在 **Qt 框架内** 寻找解决方案：
- 调坐标（除以 DPI、乘以 DPI、不转换）
- 换 API（setGeometry → SetWindowPos → show+SetWindowPos）
- 改窗口策略（小窗口 → 全屏覆盖层 → 调整扩展方向）

每一次都是"在 Qt 的坐标系里怎么对齐 Win32 的坐标"。但问题不在坐标——**问题在于 Qt 根本不允许你精确控制它的窗口在 Win32 层面的位置**。

### Codex 的思路：换框架而非调参数

Codex 直接跳出了"用 Qt 做 overlay"的前提：

> "既然 Qt 的坐标系统和 Win32 对不上，那就不用 Qt 做这个窗口。"

这是一个**架构层面的决策**，不是参数调优。具体来说：

1. **识别了不可解性**：Qt 在多 DPI 下对窗口位置有隐式控制，外部无法绕过
2. **选择了正确的抽象层级**：overlay 只需要"在屏幕指定位置画一个彩色框"——这是 Win32 GDI 的基本能力，不需要 Qt 的高级特性
3. **利用了颜色键透明**：`LWA_COLORKEY` 是 Win32 原生支持的透明方案，比 Qt 的 `WA_TranslucentBackground` 更底层、更可控

### 教训

| 维度 | 我的做法 | Codex 的做法 |
|------|---------|-------------|
| 问题定位 | "坐标不对" → 调坐标 | "Qt 窗口不可控" → 换实现 |
| 解决层级 | 参数层（坐标值、DPI 比） | 架构层（替换整个实现方案） |
| 尝试次数 | 5+ 次坐标调整 | 1 次到位 |
| 依赖关系 | 依赖 Qt 的坐标映射 | 零 Qt 依赖 |

**核心启示**：当一个框架在某个场景下行为不可控时，正确的做法不是花更多精力去理解/绕过它的内部机制，而是评估这个场景是否真的需要这个框架。overlay 边框是一个纯视觉+精确定位的需求，Win32 GDI 完全胜任，引入 Qt 反而带来了不可控的坐标转换。

---

## 技术细节备忘

### Win32 颜色键透明原理

```
SetLayeredWindowAttributes(hwnd, 0x000000, 0, LWA_COLORKEY)
                                    ↑
                              颜色键：黑色

窗口内所有黑色像素 → 透明（可点击穿透）
窗口内非黑色像素 → 不透明（红/绿边框可见）
```

配合 `FillRect` 填黑底 + `Rectangle` 画彩色边框，实现"只有边框可见，其余全透明"的效果。

### 为什么 NULL_BRUSH

```python
gdi32.SelectObject(hdc, gdi32.GetStockObject(5))  # NULL_BRUSH
```

`Rectangle` 默认会用当前画刷填充内部。用 `NULL_BRUSH` 让内部保持黑色（→ 透明），只画边框线。

### WS_EX_TRANSPARENT 的交互语义

- 窗口对所有鼠标消息返回 `HTTRANSPARENT`
- 点击、拖拽等事件穿透到下方窗口
- 边框本身也不可点击（对本工具来说是期望行为）
