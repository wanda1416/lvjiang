# 37 · 视觉感知子系统（Vision）

> Layer: L3（架构解释）· Stability: evolving
> 目标读者：需要**选通道 / 调阈值 / 排查不命中 / 扩算法**的贡献者
> 上层文法契约见 [32-grammar/04.4-image-template.md](../32-grammar/04.4-image-template.md) 与 [06.4-vision-functions.md](../32-grammar/06.4-vision-functions.md)

## 为什么单开这一档

`32-grammar` 下的 `04.4` 和 `06.4` 是 **L7 · stable** 的合同文档：签名、语义、合法用法。
它们不该关心 OpenCV 用哪个 flag、单尺度还是金字塔、Region 归一化怎么算——
这些属于**实现**，改起来比合同频繁得多。

本文档目录承载"实现细节 + 调优经验 + 失败模式"三层内容，与文法层解耦：

- 合同变化 → 改 `32-grammar/`
- 实现变化 → 改本目录
- 用法变化 → 改 `60-userguide/`

## 文件索引

| 文件 | 主题 |
|------|------|
| [01-perception-channels.md](01-perception-channels.md) | 四条感知通道（OCR / ORB / 模板 / 图色）的能力边界与决策树 |
| [02-color-ops-internals.md](02-color-ops-internals.md) | 图色原语的算法（`color_ops.py` 展开） |
| [03-template-matching.md](03-template-matching.md) | 模板匹配算法（`TM_CCOEFF_NORMED` · 单尺度 · `SEARCH_SLACK`） |
| [04-coordinate-system.md](04-coordinate-system.md) | 画布 / Region / 归一化 / 取整一致性契约 |
| [05-tuning-and-failures.md](05-tuning-and-failures.md) | 症状索引式调优手册与失败模式 |
| [06-tooling-and-regression.md](06-tooling-and-regression.md) | 编辑器、脚本工作台、双目录截图回归 |

## 与相关文档的关系

```
        32-grammar/04.4-image-template.md          32-grammar/06.4-vision-functions.md
        （by image 指令合同）                       （图色 7 函数合同）
                     │                                        │
                     ▼                                        ▼
        37-vision/03-template-matching.md            37-vision/02-color-ops-internals.md
        37-vision/04-coordinate-system.md            37-vision/04-coordinate-system.md
                     │                                        │
                     └─────────────┬──────────────────────────┘
                                   ▼
                    37-vision/01-perception-channels.md（选型）
                    37-vision/05-tuning-and-failures.md（排障）
                    37-vision/06-tooling-and-regression.md（工具）
```

代码入口：

- 底层算法：`src/lvjiang/core/recognizers/{color_ops, template_locator, heading}.py`
- DSL 内置：`src/lvjiang/workflows/builtins/vision.py`
- 引擎调用：`src/lvjiang/workflows/base/recognition.py`（`match_region_templates` / `find_image_in_region`）
- 布局模型：`src/lvjiang/core/layout_models.py`（`TemplateBinding`、`RectCoordRef`、`CircleCoordRef`）
