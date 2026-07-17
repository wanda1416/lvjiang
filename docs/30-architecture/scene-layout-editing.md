# 场景与布局 — 编辑器操作手册

本文档描述页面管理 UI 的交互方式和扩展新场景的步骤。

语义模型定义见 [scene-layout-definition.md](scene-layout-definition.md)，各场景字段实现见 [31-models/scene-implementations.md](../31-models/scene-implementations.md)。

---

## 页面管理 UI

打开方式：菜单「设置 → 页面管理」或 `F3`。

### 界面结构

每个场景 Tab 由左右分栏构成：

**左侧画布**：
- 顶部工具栏：➕ 创建坐标 / → 创建方向
- 截图画布（场景截图作为底图）
  - 黄色圆环 = 已放置的 Point（可拖动中心、拖动手柄调半径、右上角 + 按钮拉 Arrow）
  - 黄色矩形 = Region（可拖动 / 缩放 / 吸附对齐）
  - 彩色带箭头线 = Arrow（从 Point 到 Point 或绝对坐标）
  - 右键 Point → 「复制 / 删除」菜单（复制沿用源半径，便于快速创建同半径新坐标）
  - 右键 Region → 「复制 / 删除」菜单

**右侧三 Tab**：
- **区域列表**：来自 YAML `regions`，✓ 已绑定 / ○ 未绑定
- **坐标列表**：来自 YAML `points`，✓ 已放置 / ○ 未放置
- **方向列表**：来自布局 JSON `arrows`，显示 `from_key → to_key` 或绝对坐标

---

## 扩展新场景

### 1. 新建 Scene YAML

在 `config/system/scenes/` 下新建 YAML 文件：

```yaml
# config/system/scenes/transfer.yaml
key: transfer
name: 转律界面
regions:
  - key: cost
    name: 消耗材料
    type: attr
  - key: confirm_btn
    name: 确认
    type: func
    is_text: true
    is_clickable: true
points:
  - key: slider_handle
    name: 滑块手柄
```

### 2. 注册到布局配置

在 `config/default.yaml` 的 `layout_scenes` 中添加场景 key：

```yaml
# config/default.yaml
layout_scenes:
  - bag_equip_detail
  - bag_item_detail
  - equip_weapon_detail
  - equip_armor_detail
  - equip_tune_detail
  - equip_tune_result
  - game_main_page
  - game_menu_page
  - general_control
  - transfer  # 新增
```

### 3. 在编辑器中配置坐标

打开页面管理 → 切换到新场景 Tab → 导入截图 → 在画布上放置 Region 和 Point 并调整坐标。
