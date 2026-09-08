# 场景与布局 — 编辑器操作手册

本文档描述场景管理 UI 的交互方式和扩展新场景的步骤。

语义模型定义见 [01-scene-layout-definition.md](01-scene-layout-definition.md)，各场景字段实现见 [31-models/02-scene-implementations.md](../31-models/02-scene-implementations.md)。

---

## 场景管理 UI

打开方式：菜单「设置 → 场景管理」或 `F3`。

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

**右侧五列布局组件**：
- **区域**：来自 YAML `regions`，✓ 已绑定 / ○ 未绑定
- **坐标**：来自 YAML `points`，✓ 已放置 / ○ 未放置
- **方向**：来自布局 JSON `arrows`，显示 `from_key → to_key` 或绝对坐标
- **网格**：原 Panel 网格编辑能力；存储字段仍为 `panels`，保持兼容
- **引用**：在父场景中声明、绑定和调整子场景实例

区域、坐标、网格和子场景引用列表可从「名称」列拖拽排序，结果会立即写回
场景 YAML 中对应定义列表的顺序。多视图筛选下只交换当前可见定义原来占据的
槽位，不移动隐藏定义。跨场景引用展开到画布后仅供绘制和选中查看，不能移动、
缩放、复制、删除或作为方向起点；需要改坐标时应回到源场景修改。

### 创建和引用子场景

1. 先创建一个语义明确的普通场景，并在其截图上定义区域、坐标或网格。
2. 点击画布上方「场景管理」，勾选「可作为引用子场景」。多视图场景不能转换；
   子场景也不能嵌套引用其他子场景。
3. 顶部「编辑画布」会变为「裁剪画布」。框定组件在原截图中的内容范围后，
   编辑器自动按宽或高把裁剪区域完整填入展示区。内部实体仍保存为裁剪画布内的
   `0..1` 相对坐标。
4. 切到父场景，在右侧「引用」中新建引用，选择目标子场景，再点击「绑定引用」
   在父画布上框出实例外框。外框可移动和缩放；内部结构随外框绘制，但不可单独选择。

每个引用都有父场景内唯一的实例 key。同一子场景可注册多次，例如
`card_1` 到 `card_6`。工作流通过三段地址访问内部实体：

```text
click [scene].[card_1].[refresh]
scan [scene].[card_1].[label] as $text
recognize [scene].$card.[icon] as $result
```

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
  - game_main_page
  - game_menu_page
  - general_control
  - transfer  # 新增
```

### 3. 在编辑器中配置坐标

打开场景管理 → 切换到新场景 Tab → 导入截图 → 在画布上放置 Region 和 Point 并调整坐标。
