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
[布局栏] 布局下拉框 | 新建 | 加载 | 保存 | 删除 | 刷新截图 | 当前布局：XXX
[场景Tab] QTabWidget: 装备词条详情 | 装备调律详情
  [每个tab内] SceneTab = RegionCanvas + 字段列表
[底部] 识别全部字段 | OCR 结果展示区 | 状态栏
```

新增 `SceneTab(QWidget)` 类：封装单个场景的画布 + 字段列表交互。每个 tab 独立维护画布，保存时统一写入。

**布局栏语义**：
- **新建**：输入名称 → 创建空布局 → 设为 active
- **加载**：读取选中布局到所有 tab 画布
- **保存**：从所有 tab 收集 regions → 全量写入布局文件
- **删除**：确认对话框 → 删除文件 → 清空 active

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

---

## 教训

| 维度 | 旧设计 | 新设计 |
|------|--------|--------|
| 组织层级 | 场景 → 布局（每个场景各自管理布局） | 布局 → 场景（一个布局包含所有场景） |
| 语义清晰度 | "布局"一词在场景级别，容易混淆 | "布局"对应设备，直觉清晰 |
| 保存方式 | 按场景单独保存 | 全量保存所有场景 |
| UI 结构 | 场景下拉框 + 单画布 | 场景 Tab + 每 Tab 独立画布 |
| 配置存储 | 单文件 `regions.json` | `config.json` + `layouts/*.json` |

**核心启示**：当发现组织层级反了的时候，越早改越好。继续在上层打补丁只会让后续逻辑越来越扭曲。
