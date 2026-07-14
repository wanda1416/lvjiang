# Dev Log: 区域配置布局层级重构（Layout → Scene → Region）

> 日期：2026-07-14
> 涉及模块：`lvjiang/core/region_config.py`、`lvjiang/ui/region_editor_dialog.py`、`lvjiang/ui/main_window.py`
> 关键词：布局层级、Layout、LayoutConfigManager、QTabWidget、数据迁移

---

## 背景

原有配置结构为「场景 → 配置组」：`regions.json` 按场景（equip_detail / equip_tune）组织，每个场景下有多套 preset。这种结构的问题在于：**多套布局的语义是「不同设备」，但设备级别应该高于场景级别**。一个布局（对应一台设备）应该包含所有场景的区域定义，而不是每个场景各自维护布局列表。

---

## 数据模型变更

### 旧结构（已废弃）

```
config/regions.json
{
  "equip_detail": { "active": "默认布局", "presets": { "默认布局": { "regions": [...] } } },
  "equip_tune":   { "active": "详情布局", "presets": { "详情布局": { "regions": [...] } } }
}
```

### 新结构

```
config/
  config.json              → {"active_layout": "默认布局"}
  layouts/
    默认布局.json           → 一个布局文件包含所有场景
```

单个布局文件格式：
```json
{
  "equip_detail": { "regions": [...] },
  "equip_tune":   { "regions": [...] }
}
```

**核心变化**：顶层 key 从场景变为布局名；每个布局内包含所有场景的 regions；`config.json` 记录当前 active 布局。

---

## 代码变更

### 1. `region_config.py` — 完全重写

| 变更 | 说明 |
|------|------|
| 删除 `REGIONS_FILE` | 旧的单文件路径 |
| 新增 `LAYOUTS_DIR`、`CONFIG_FILE` | `config/layouts/`、`config/config.json` |
| 删除 `RegionPreset` | 被 `Layout` 取代 |
| 新增 `Layout` dataclass | `name: str` + `scenes: dict[str, list[Region]]` |
| 删除 `RegionConfigManager` | 被 `LayoutConfigManager` 取代 |
| 新增 `LayoutConfigManager` | 布局 CRUD + active 管理 |

`LayoutConfigManager` 关键方法：
- `list_layouts()` → 扫描 `layouts/*.json`
- `new_layout(name)` → 创建空布局 + 保存 + 设为 active
- `load_layout(name)` / `save_layout(layout)` → 全量读写
- `delete_layout(name)` → 删除文件，若为 active 则清空
- `get_active_layout()` → 读取 active 布局

### 2. `region_editor_dialog.py` — UI 结构重写

**旧结构**：场景下拉框 + 单画布 + 右侧预设管理面板

**新结构**：
```
[布局栏] 当前布局 [下拉框] | 激活 | 新建 | 保存 | 另存为 | 删除 | 刷新截图 | 默认布局：XXX
[场景Tab] QTabWidget: 装备词条详情 | 装备调律详情
  [每个tab内] SceneTab = RegionCanvas + 字段列表
[底部] 识别全部字段 | OCR 结果展示区 | 状态栏
```

新增 `SceneTab(QWidget)` 类：封装单个场景的画布 + 字段列表交互。每个 tab 独立维护画布，保存时统一写入。

**布局栏语义**：
- 左侧「当前布局」+ 下拉框：切换下拉框即加载对应布局到画布（不激活）
- 右侧「默认布局：XXX」标签：显示当前激活的布局名称
- **激活**：将当前加载的布局设为激活；已激活时按钮禁用
- **新建**：创建空布局（不自动激活）
- **保存**：从所有 tab 收集 regions → 全量写入当前布局文件（不改变激活状态）
- **另存为**：输入新名称 → 若已存在则提示确认覆盖（不自动激活）
- **删除**：弹出选择对话框 → 激活布局不可删除 → 确认后删除

### 3. `main_window.py` — 适配

- `_open_region_editor`：不再传 `preset` 参数，对话框自行管理布局
- `_region_preset` 属性改名为 `_region_layout`（类型从 `RegionPreset` 改为 `Layout`）

### 4. 数据迁移

从旧 `regions.json` 提取每个场景的 active preset，合并写入 `layouts/默认布局.json`，创建 `config.json` 设 `active_layout: "默认布局"`，删除旧文件。

---

## 前置 Bug 修复（区域编辑器迭代过程中）

在布局层级重构之前，区域编辑器经历了多轮迭代，修复了以下问题：

### 1. 刷新截图不生效

**现象**：点击「刷新截图」后画布没有变化。

**根因**：`_get_last_capture` 只返回缓存的截屏图片，没有重新截取。

**修复**：在 `main_window.py` 新增 `_refresh_capture()` 方法，重新调用截屏器获取新截图。

### 2. OCR 识别结果区域太小

**现象**：OCR 结果文本区高度不够，底部留大量空白。

**根因**：`_result_text` 使用 `setMaximumHeight(120)` 限制了最大高度，导致内容显示有限。

**修复**：改为 `setMinimumHeight(180)` + `stretch=1`，让结果区自适应扩展。

### 3. 切换场景后字段列表未同步

**现象**：切换到第二个场景后，框选区域弹出的字段选择对话框仍显示第一个场景的字段。

**根因**：`RegionCanvas._prompt_field_selection` 硬编码使用 `EQUIP_FIELDS`，不随场景切换变化。

**修复**：画布新增 `_current_fields` 属性和 `set_current_fields()` 方法，由对话框在场景切换时调用更新。

### 4. 布局栏 UI 重构：标签+按钮 → 下拉框+激活标签

**现象**：布局栏按钮过多，加载/删除/另存为等操作语义不够直观。

**修复**：左侧改为「当前布局」+ `QComboBox` 下拉框（切换即加载到画布，不激活），右侧放置功能按钮：激活、保存、新建、另存为、删除。新建移到保存右侧，与另存为语义一致（都是打开新布局）。最右侧新增「默认布局：XXX」标签显示当前激活布局。激活按钮在已激活时禁用，新建/另存为不自动激活。

### 5. 画布初始图片未左上角对齐

**现象**：打开区域编辑器时，截图居中显示，左上角留有空白。

**根因**：`_recalc_display` 使用 `_apply_zoom_anchor(QPointF(ww/2, wh/2))` 将图片居中，导致左上角未对齐 widget 左上角。

**修复**：`_recalc_display` 改为直接设置 `_display_rect = QRectF(0, 0, pw*scale, ph*scale)`，图片左上角对齐 widget 左上角。

### 6. 删除/另存为/新建交互优化

**现象**：删除布局弹出选择对话框多余（应直接针对当前展示的布局）；另存为/新建后下拉框未定位到新布局。

**修复**：
- 删除：直接对当前下拉框选中的布局弹出确认，删除后自动加载默认激活布局。
- 另存为：保存完成后自动将下拉框切换到新布局。
- 新建：创建后恢复原激活布局，并将下拉框切换到新布局。

### 7. 配置目录拆分：自带配置 vs 用户配置

**现象**：用户频繁修改布局导致 `config/` 目录不断产生新提交，污染 git 历史。

**修复**：
- 新增 `USER_CONFIG_DIR = config/user/`，`LAYOUTS_DIR` 和 `CONFIG_FILE` 迁移至该目录。
- `.gitignore` 排除 `config/user/`，自带配置（`default.yaml` 等）保留在 `config/` 下继续跟踪。

### 8. 画布全局调整模式

**现象**：未选中右侧字段时，画布上的已有区域无法直接移动或缩放，只能创建新区域。

**根因**：`mousePressEvent` 在 `_selected_idx == -1` 时直接进入 DRAWING 模式，没有检测是否点击了已有区域。

**修复**：
- 新增 `_field_selected` 标志区分两种模式。
- **全局模式**（右侧未选中字段）：点击已有区域可选中/移动/缩放，点击空白才创建新区域。
- **单区域模式**（右侧选中字段）：只显示并编辑该区域，点击空白不创建。
- 取消右侧选中时自动回到全局模式。

### 9. 全局模式下拖拽后区域残留黄色高亮

**现象**：连续调整多个区域时，之前操作过的区域仍然显示黄色边框。

**根因**：`mouseReleaseEvent` 在 MOVING/RESIZING 结束后没有重置 `_selected_idx`，导致该区域在绘制时仍被视为选中状态。

**修复**：拖拽结束后，全局模式下清除 `_selected_idx = -1`；单区域模式下保留选中以便继续调整。后续加强：将重置逻辑从 MOVING/RESIZING 分支提升到 `mouseReleaseEvent` 顶层，确保全局模式下任何左键交互结束后都统一清除选中。

### 10. 全局模式无法拉伸 + 单区域模式无法回到全局模式

**现象**：全局模式下只能拖拽移动区域，无法拉伸；右侧选中字段后点击画布空白无法退出单区域模式。

**根因**：
1. `mouseReleaseEvent` 在全局模式下立即重置 `_selected_idx = -1`，导致下次点击时 `_hit_test` 跳过手柄检测，永远无法进入拉伸。
2. 单区域模式下点击空白区域直接 `return`，没有清除 `_field_selected`。

**修复**：
- 移除全局模式下的 `_selected_idx` 重置，移动/缩放后保留选中，用户再次点击同区域即可获取手柄进行拉伸。
- 单区域模式下点击画布空白区域：清除 `_field_selected` 和 `_selected_idx`，回到全局模式。

### 11. QPainter 画刷泄漏致后续区域黄色覆盖

**现象**：全局模式下选中一个区域后，所有比它晚创建的区域都被黄色满区域覆盖。

**根因**：`_draw_region` 绘制缩放手柄时调用 `painter.setBrush(黄色)`，但 `QPainter` 状态是持久的，方法返回后画刷未恢复。下一个区域的 `drawRect` 调用会用当前残留的黄色画刷填充整个矩形内部。

**修复**：手柄绘制前后加 `painter.save()` / `painter.restore()`，隔离画刷状态。

### 12. 删除布局后加载了列表下一个而非默认激活布局

**现象**：删除布局后，画布加载的是下拉列表中的下一个布局，而非默认激活布局。

**根因**：`_on_delete_layout` 先正确加载了激活布局，但随后 `_update_ui_state` → `_refresh_combo` 尝试恢复旧的下拉框文本（已删除的布局名），找不到后 Qt 自动选中相邻项，触发 `_on_combo_changed` 覆盖了正确加载。

**修复**：在 `_update_ui_state` 之前，先 `blockSignals` 同步下拉框到激活布局名称，防止 `_refresh_combo` 恢复已删除文本触发错误回调。

### 13. OCR 识别性能优化：整图一次推理

**现象**：识别 5 个区域耗时约 5 秒，每个区域单独调用一次完整 OCR 管线。

**根因**：循环中对每个区域裁剪后单独调用 `engine.recognize(crop)`，每次调用都跑完整的检测+识别管线，小图推理的开销/有效计算比极低。

**修复**：先计算覆盖所有区域的最小外接矩形，裁剪后一次 OCR 推理，再将 bbox 坐标还原到全图坐标系后分配到各区域。一次推理替代 N 次，且不传整张大图。

### 14. 区域边缘对齐困难 → 引入吸附对齐

**现象**：手动拖拽/拉伸多个区域时边缘难以对齐，常出现像素级错位。

**方案**：参照流程图设计软件实现吸附对齐。

- **参考线来源**：每个其他区域贡献 3 条竖线（左边/右边/水平中心）和 3 条横线（上边/下边/垂直中心）。
- **拖拽移动**：区域自身左/右/中心三条竖边匹配参考线，取最近且在阈值内的一条整体平移对齐；纵向同理。
- **拉伸**：只对正在拖动的那条边吸附（如拖右手柄只吸附右边），且保证不违反最小尺寸。
- **阈值**：`SNAP_PIXELS = 6` 像素，x/y 分别用 `_display_rect` 宽高换算为归一化值，保证缩放后阈值恒为屏幕 6 像素。
- **视觉反馈**：吸附命中时画品红色虚线参考线，横跨整个图片显示区域，松开鼠标后清除。

### 15. 区域编辑器模块拆分

**现象**：`region_editor_dialog.py` 单文件超过 1200 行，难以维护。

**方案**：拆分为 `lvjiang/ui/region_editor/` 子包：

```
region_editor/
├── __init__.py        # 导出 RegionEditorDialog, RegionCanvas, SceneTab
├── canvas.py          # RegionCanvas 画布组件（~745 行）
├── scene_tab.py       # SceneTab 场景 Tab（~100 行）
└── dialog.py          # RegionEditorDialog 主对话框（~408 行）
```

- 共享常量（`REGION_COLORS`, `HANDLE_SIZE`, `SNAP_PIXELS`）和枚举（`DragMode`, `HandlePos`）移入 `canvas.py`
- `main_window.py` 导入路径从 `.region_editor_dialog` 改为 `.region_editor`
- 删除旧文件 `region_editor_dialog.py`

---

## 教训

| 维度 | 旧设计 | 新设计 |
|------|--------|--------|
| 组织层级 | 场景 → 布局（每个场景各自管理布局） | 布局 → 场景（一个布局包含所有场景） |
| 语义清晰度 | "布局"一词在场景级别，容易混淆 | "布局"对应设备，直觉清晰 |
| 保存方式 | 按场景单独保存 | 全量保存所有场景 |
| UI 结构 | 场景下拉框 + 单画布 | 场景 Tab + 每 Tab 独立画布 |
| 配置存储 | 单文件 `regions.json` | `config/default.yaml`（自带）+ `config/user/`（用户级，git 忽略） |

**核心启示**：当发现组织层级反了的时候，越早改越好。继续在上层打补丁只会让后续逻辑越来越扭曲。用户频繁变动的配置必须与自带配置分离，避免污染 git 历史。
