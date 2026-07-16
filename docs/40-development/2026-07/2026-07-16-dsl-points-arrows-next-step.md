# DSL 消费 points / arrows 改造方案（next step）

> 本文档描述在「scene 扩展 points 与 arrows」UI 侧落地后，DSL 消费侧需要做的改造。
> 当前 session 未触碰 DSL，所有 DSL 改造留给本 session 完成后由另一 session 执行。
>
> 依赖：`lvjiang/core/region_config.py` 中的 `Point` / `Arrow` 数据类与 `Layout.get_scene_points / get_scene_arrows` API 已就位。

## 一、改造总览

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `lvjiang/core/input.py` | 新增方法 | `drag_screen`：拖拽操作 |
| `lvjiang/workflows/base.py` | 新增方法 | `click_any` / `click_point` / `drag_arrow` / `_point_to_screen` / `_ratio_to_screen` |
| `lvjiang/workflows/ast_nodes.py` | 新增节点 | `Drag` AST 节点 |
| `lvjiang/workflows/grammar.lark` | 新增语法规则 | `drag_stmt` |
| `lvjiang/workflows/parser.py` | 新增 transformer | `drag_stmt` → `Drag` |
| `lvjiang/workflows/engine.py` | 扩展 match | 新增 `Drag` 分发 |

## 二、`input.py`：新增 `drag_screen`

```python
def drag_screen(self, from_x: int, from_y: int, to_x: int, to_y: int, poi_name: str = ""):
    """从起点拖拽到终点（模拟人类操作）"""
    self._move_to(from_x, from_y)
    pre_delay = random.uniform(*self.before_click_wait)
    time.sleep(pre_delay)
    logger.debug(f"拖拽 {poi_name}: ({from_x},{from_y}) -> ({to_x},{to_y})")
    pyautogui.moveTo(from_x, from_y, duration=random.uniform(*self.mouse_move_duration))
    pyautogui.mouseDown()
    pyautogui.moveTo(to_x, to_y, duration=random.uniform(*self.mouse_move_duration))
    pyautogui.mouseUp()
    post_delay = random.uniform(*self.after_click_wait)
    time.sleep(post_delay)
```

要点：
- 复用现有 `mouse_move_duration` / `before_click_wait` / `after_click_wait` 参数，保持节奏拟人化
- 起点/终点均不做随机偏移（拖拽需要精确落点）

## 三、`base.py`：新增 5 个方法

```python
def click_any(self, scene_key: str, key: str):
    """点击 region 或 point（自动识别）"""
    regions = self._layout.get_scene_regions(scene_key)
    region = next((r for r in regions if r.key == key), None)
    if region is not None:
        self.click_region(scene_key, key)
        return
    points = self._layout.get_scene_points(scene_key)
    point = next((p for p in points if p.key == key), None)
    if point is not None:
        self.click_point(scene_key, key)
        return
    logger.error(f"场景 {scene_key} 没有定义 region 或 point: {key}")


def click_point(self, scene_key: str, point_key: str):
    """点击 point 中心（带半径内随机偏移）"""
    points = self._layout.get_scene_points(scene_key)
    point = next((p for p in points if p.key == point_key), None)
    if point is None:
        logger.error(f"场景 {scene_key} 没有定义 point: {point_key}")
        return
    screen_x, screen_y = self._point_to_screen(point)
    if screen_x is None:
        return
    logger.debug(f"点击 point: {scene_key}/{point_key} -> 屏幕({screen_x},{screen_y})")
    self._input.click_screen(screen_x, screen_y, f"{scene_key}/{point_key}")


def drag_arrow(self, scene_key: str, arrow_key: str):
    """执行 arrow 定义的拖拽（从 from point 到 to 坐标/point）"""
    arrows = self._layout.get_scene_arrows(scene_key)
    arrow = next((a for a in arrows if a.key == arrow_key), None)
    if arrow is None:
        logger.error(f"场景 {scene_key} 没有定义 arrow: {arrow_key}")
        return
    points = self._layout.get_scene_points(scene_key)
    from_point = next((p for p in points if p.key == arrow.from_key), None)
    if from_point is None:
        logger.error(f"arrow {arrow_key} 的起点 point {arrow.from_key} 未定义")
        return
    # 终点：吸附态动态查 point，绝对态直接用坐标
    if arrow.to_key is not None:
        to_point = next((p for p in points if p.key == arrow.to_key), None)
        if to_point is None:
            logger.error(f"arrow {arrow_key} 的终点 point {arrow.to_key} 未定义")
            return
        to_cx, to_cy = to_point.cx_ratio, to_point.cy_ratio
    else:
        to_cx, to_cy = arrow.to_cx_ratio, arrow.to_cy_ratio
    fx, fy = self._point_to_screen(from_point)
    tx, ty = self._ratio_to_screen(to_cx, to_cy)
    if fx is None or tx is None:
        return
    logger.debug(f"拖拽 arrow: {scene_key}/{arrow_key} ({fx},{fy})->({tx},{ty})")
    self._input.drag_screen(fx, fy, tx, ty, f"{scene_key}/{arrow_key}")


def _point_to_screen(self, point: Point) -> tuple[int | None, int | None]:
    """point 中心 → 屏幕坐标（带半径内随机偏移）"""
    img = self._capture.capture()
    if img is None:
        logger.error("截图失败")
        return None, None
    h, w = img.shape[:2]
    canvas = self._layout.get_canvas()
    canvas_x = canvas.x_ratio * w
    canvas_y = canvas.y_ratio * h
    canvas_w = canvas.w_ratio * w
    canvas_h = canvas.h_ratio * h
    cx = canvas_x + point.cx_ratio * canvas_w
    cy = canvas_y + point.cy_ratio * canvas_h
    # 半径内随机偏移（模拟人类点击 point 时的落点差异）
    r = point.r_ratio * min(canvas_w, canvas_h)
    angle = random.uniform(0, 2 * math.pi)
    dist = random.uniform(0, r)
    cx += dist * math.cos(angle)
    cy += dist * math.sin(angle)
    return int(self._window_left + cx), int(self._window_top + cy)


def _ratio_to_screen(self, cx_ratio: float, cy_ratio: float) -> tuple[int | None, int | None]:
    """画布内归一化坐标 → 屏幕坐标"""
    img = self._capture.capture()
    if img is None:
        logger.error("截图失败")
        return None, None
    h, w = img.shape[:2]
    canvas = self._layout.get_canvas()
    sx = canvas.x_ratio + cx_ratio * canvas.w_ratio
    sy = canvas.y_ratio + cy_ratio * canvas.h_ratio
    return int(self._window_left + sx * w), int(self._window_top + sy * h)
```

要点：
- `click_any` 优先查 region，回退查 point；DSL 调用方无需区分类型
- `click_point` 的随机偏移用极坐标在 `r_ratio` 半径内均匀采样
- `drag_arrow` 对吸附态 arrow 动态查 point 坐标，绝对态直接用固定归一化坐标
- `_ratio_to_screen` 是 `_region_to_screen` 的归一化版本，可考虑重构共享逻辑

## 四、`ast_nodes.py`：新增 `Drag` 节点

```python
@dataclass
class Drag:
    scene: Any   # VarRef | Literal
    arrow: Any   # VarRef | Literal
    line_no: int = 0
```

并在 `__init__.py` 的 `__all__` 中导出。

## 五、`grammar.lark`：新增 `drag_stmt`

```lark
?stmt : click_stmt | wait_stmt | scan_stmt | click_match_stmt | collect_stmt
      | collect_as_stmt | log_stmt | eval_stmt | if_stmt | for_stmt | loop_stmt
      | break_stmt | label_stmt | goto_stmt
      | drag_stmt                       // 新增

drag_stmt : "drag" bracket_expr "." bracket_expr _NL
```

注意：`drag [scene].[arrow_key]` 与 `click [scene].[field]` 语法结构完全一致，仅关键字不同。

## 六、`parser.py`：新增 transformer

```python
def drag_stmt(self, items):
    scene, arrow = items
    return Drag(scene=scene, arrow=arrow, line_no=self._line(items))
```

`_ContextPostProcessor` 无需改动：`drag_stmt` 中的 `bracket_expr` 与 `click_stmt` 一样，保持 VarRef 由运行时解析。

## 七、`engine.py`：扩展 match

```python
from .ast_nodes import (..., Drag)

match node:
    case Click():
        self._exec_click(node)
    case Drag():                                    # 新增
        self._exec_drag(node)
    ...

def _exec_drag(self, node: Drag):
    scene = self._resolve_param(node.scene)
    arrow = self._resolve_param(node.arrow)
    self._wf.drag_arrow(scene, arrow)
```

## 八、DSL 语法示例

```
# 点击 point（与点击 region 同语法，运行时自动识别）
click [equip_tune_detail].[slider_handle]

# 拖拽 arrow
drag [equip_tune_detail].[tune_drag]
```

## 九、测试计划

1. **单元测试**：
   - `_point_to_screen` / `_ratio_to_screen` 坐标换算正确性
   - `click_any` 在 region 存在时走 region 路径，否则走 point 路径
   - `drag_arrow` 对吸附态 / 绝对态 arrow 都能正确解析终点
2. **集成测试**：
   - 在 `config/user/layouts/测试布局.json` 中手工添加 `points` 和 `arrows`
   - 编写 `.wf` 文件执行 `click [scene].[point_key]` 和 `drag [scene].[arrow_key]`
   - 验证实际鼠标行为符合预期
3. **回归测试**：
   - 现有 `click [scene].[region_key]` 不受影响（`click_any` 优先查 region）
   - 现有 `.wf` 文件无需改动

## 十、注意事项

1. **命名空间**：`click_any` 在 region 与 point 同名时会优先点 region（保持向后兼容）
2. **arrow 终点缺失**：`to_key` 指向未放置的 point 或 `to_cx/to_cy` 为 None 时，跳过执行并打 error 日志
3. **拟人化**：拖拽时长复用 `mouse_move_duration`，不引入新参数
4. **DSL 关键字冲突**：`drag` 当前不是保留字，直接加入语法无冲突
5. **后续扩展**：若需支持"拖拽到绝对坐标"的 DSL 语法（而非只通过 arrow 间接），可再新增 `drag_to [scene].[point_key] x y` 形式
