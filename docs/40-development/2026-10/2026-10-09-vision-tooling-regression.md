# 视觉感知诊断、离线回归与工具待办

> 归档日期：2026-10-09；这是整理日期，不代表原方案的提出或验证日期。
> 以下保留旧方案、排查或维护过程，不能作为当前用户操作或运行契约。

## 三、诊断脚本

### scripts/diagnostics/scan_tpl_bound.py

**用途**：扫布局里所有模板绑定，比较编辑器语义 vs 运行时的边界差异（`int` vs `round`），输出 diff 表。

**背景**：memory `模板搜索区域边界取整修复与 yysls_logo 模板集成` 修复 scale=1.0 skip bug 时创建。修完之后主要用于**回归**：任何触碰 `template_locator.py` 边界计算时先跑一遍。

**用法**（参考脚本头部 docstring；具体 CLI 参数以脚本为准）：

```
python scripts/diagnostics/scan_tpl_bound.py [layout_name]
```

**判据**：`diff` 应该恒为 +1 或 +2；出现 0 或负数说明边界计算又走偏了。

### scripts/fast_test.py

日常迭代入口。改 `color_ops.py` / `template_locator.py` 后：

```
python scripts/fast_test.py src/lvjiang/core/recognizers/template_locator.py
```

自动映射到相关测试文件（`tests/core/test_template_locator.py` 等）。
## 四、离线 fixture 回归

### 用截图直接跑 color_ops

`color_ops.py` **不 import 引擎**，可以：

```python
import cv2
from lvjiang.core.recognizers.color_ops import color_ratio_tol, find_icons

img = cv2.imread("path/to/hall_1080p.png")   # BGR
ratio = color_ratio_tol(img, x1, y1, x2, y2, (0, 200, 0), 40)
print(ratio)

icons = find_icons(img, x1, y1, x2, y2, channel=1, c_min=150, margin1=35)
for cx, cy, area, w, h in icons:
    print(f"({cx:.0f}, {cy:.0f}) {area}px² {w}x{h}")
```

**推荐做法**：把有代表性的截图存到 `fixtures/`（未进 git 时放 `data/temp/`），加图色判据**先在离线帧上验证**，再进 wf。

### 双目录截图比例回归

memory `双目录截图比例验证的容差感知脚本编写方法` 描述的做法：

同一 Region 在**桌面布局**和**投屏布局**下各截一张，写脚本比较归一化坐标是否**在容差内一致**。若不一致，运行时 `resolution_scale` 无法救回。

**容差经验**：单点归一化误差 ≤ 0.005（画布 1920 时 ≤ 10 px）算可接受；再大要重画 Region。
## 五、日志与调试开关

`logger.debug` 在下列位置输出关键决策信息（`LOG_LEVEL=DEBUG` 打开）：

| 位置 | 输出内容 |
|---|---|
| `template_locator.py` L279 | `模板 {tpl.name} 最佳分 {best.score:.3f} < {min_score}（scale {best.scale:.2f}）` |
| `recognition.py` L740 | `scan by image 命中: scene=... region=... template=... score=... scale=...` |
| `recognition.py` L687 | `find 命中模板: ... score=... scale=... center=(x,y)` |
| `recognition.py` L621 | `find 未命中: target=... mode=...` |

**排查未命中**：`grep "最佳分" logs/lvjiang_*.log logs/logs/*/*.log` 找到分数与阈值差多少。

- 差 0.05 内 → 阈值稍紧，可微降或换帧
- 差 0.2+ → 大概率不是同一个东西（跨布局未重录、T2 场景）
- score = 0 或缺失 → 检查 `record_w` / `resolution_scale`
## 六、常用命令速查

```
# 只跑相关测试
python scripts/fast_test.py src/lvjiang/core/recognizers/template_locator.py

# 扫所有模板边界（回归）
python scripts/diagnostics/scan_tpl_bound.py desktop
python scripts/diagnostics/scan_tpl_bound.py android

# 只跑某个模板匹配的测试用例
python scripts/fast_test.py tests/core/test_template_locator.py -- --tb=short -k "round"

# 打开 debug 日志定位匹配失败
$env:LOG_LEVEL="DEBUG"; python -m lvjiang ...
```
## 七、什么时候**不该**用工具

- **不要用截图工具**：直接 `data/temp/` 里放帧 + Python REPL 跑 `color_ops` 更快
- **不要用编辑器调 min_score**：改 layout JSON 里 `binding.min_score` 字段后重开编辑器；编辑器打开时会读回
- **不要在真机上在线调阈值**：一次 3 秒 × 几十次 = 半小时。先用离线帧定基线，真机只做最后 20% 微调
## 八、待补充工具（Backlog）

下列是**已经踩过坑、但工具还没做**的方向。有精力可以顺手补：

- **find_icons 交互式阈值预览**：脚本工作台里滑条调 margin/area，实时看命中变化
- **多分辨率一致性检查**：CI 里加一步，把所有模板在各支持分辨率下都跑一次，标出 `record_w` 缺失或 scale 掉分严重的项
- **掩膜可视化**：给定 Region + 图色函数参数，输出掩膜 PNG，直观看到哪些像素被算进去了
