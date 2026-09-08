# 场景与布局 — 语义模型定义

本文档定义 Scene / Area / Layout 三层语义模型，描述数据结构和存储规范。

编辑器操作手册见 [02-scene-layout-editing.md](02-scene-layout-editing.md)，各场景具体字段实现见 [31-models/02-scene-implementations.md](../31-models/02-scene-implementations.md)。

---

## 三层架构

```
Scene（场景）          ← 逻辑定义层：游戏中一个界面
  ├── Area（区域）     ← 界面中的一个逻辑单元
  │     ├── Point      ← 圆形交互锚点（点击/拖拽端点）
  │     └── Region     ← 矩形识别区域（OCR / 材料识别 / 点击）
  └── Panel（容器）    ← 可寻址的复合区域（type 区分内部结构）
        ├── grid       ← 行列网格，[r][c] 寻址（已实现）
        └── regions    ← Region 集合，[name] 寻址（规划中）
  └── SubsceneRef      ← 对 type=subscene 场景的可复用实例引用

Layout（布局）          ← 物理绑定层：一套投屏方案
  ├── Area-Coord 绑定  ← 每个 Area 在屏幕上的实际坐标
  ├── Panel-Coord 绑定  ← 每个 Panel 在屏幕上的实际坐标
  ├── SubsceneRef 绑定  ← 子场景实例在父画布上的外框
  └── Action           ← 基于 Area 的交互动作
        └── Arrow      ← 拖拽（两点间拖拽）
```

**核心原则**：
- **Scene** 定义"有什么"（逻辑结构），与分辨率无关
- **Layout** 内部分两层：
  - **Area-Coord 绑定**：每个 Area 在屏幕上的物理坐标（位置）
  - **Action → Arrow**：基于 Area 的交互动作（行为），目前仅拖拽
- Scene 与 Layout 完全解耦：同一 Scene 可在不同 Layout 中有不同坐标

---

## Scene — 场景

一个 Scene 对应游戏中的一个界面（如装备详情页、调律页、主界面）。

### YAML 定义结构

每个场景由一个 YAML 文件定义（`config/system/scenes/{scene_key}.yaml`）：

```yaml
key: scene_key
name: 场景名称

regions:                        # 矩形区域（OCR / 点击目标）
  - key: region_key
    name: 区域名称
    type: attr                  # attr / slot / func
    is_text: true
    is_clickable: false

points:                         # 圆形交互锚点
  - key: point_key
    name: 坐标名称
    type: func                  # attr / slot / func
    is_text: false
    is_clickable: true
    # 注意：YAML 不定义 r_ratio，半径属于 Layout 实例数据

panels:                         # 可寻址容器（默认 type=grid）
  - key: panel_key
    name: 面板名称
    type: grid                  # grid（默认，可省略） / regions（规划中）
    rows: 3
    cols: 6
```

### Subscene — 可复用场景

子场景仍是完整 Scene，只增加 `type: subscene` 标记。它不能启用多视图，也不能
嵌套引用其他子场景。父场景通过 `subscene_refs` 声明实例 key 和目标场景：

```yaml
# card.yaml
key: card
name: 通用卡片
type: subscene
regions:
  - key: label
    name: 标签

# parent.yaml
subscene_refs:
  - key: card_1
    name: 卡片1
    scene: card
```

子场景自己的布局 JSON 保存 `crop_canvas` 和局部实体坐标；父场景布局 JSON 的
`subscene_refs` 保存各实例外框。运行时按以下方式组合坐标：

```text
absolute_x = reference.x + child.x * reference.w
absolute_y = reference.y + child.y * reference.h
absolute_w = child.w * reference.w
absolute_h = child.h * reference.h
```

组合结果仍位于父场景的布局画布坐标系，随后再走全局 Canvas 到屏幕坐标的既有变换。

---

## Area 子元素

### Region — 矩形区域

Region 是有面积的矩形区域，用于 OCR 识别、材料识别或点击目标。

#### type — 字段类型

决定 Region 在自动化流程中的**角色**，合法值三种：

| type | 语义 | 典型场景 | OCR 行为 | 可点击 |
|------|------|----------|----------|--------|
| `attr` | **数据属性** — 需要被 OCR 读取的文字内容 | 装备词条（宫/商/角/徵/羽）、装备类型、等级 | 始终 OCR | 不可点击（默认） |
| `slot` | **槽位** — 背包/装备栏中的可点击格子 | 装备槽位、背包格、材料格 | 不 OCR（默认 `is_text: false`） | 可点击 |
| `func` | **功能按钮** — 界面上的可操作控件 | 返回、调律按钮、一键添加、菜单入口 | 视 `is_text` 而定 | 可点击 |

#### is_text — 是否进行 OCR 识别

| 值 | 含义 | 常见搭配 |
|----|------|----------|
| `true`（默认） | 工作流对该区域执行 OCR，提取文字内容 | `attr` 字段默认；部分需要文字匹配的 `func` 按钮 |
| `false` | 不执行 OCR，仅作为点击目标或占位区域 | `slot` 字段默认；图标类按钮（如返回、更多功能） |

#### is_clickable — 是否可点击

| 值 | 含义 | 常见搭配 |
|----|------|----------|
| `false`（默认） | 该区域不会被点击，仅用于读取数据 | `attr` 字段 |
| `true` | 工作流可通过 `click [scene].[region]` 点击该区域中心 | `slot`、`func` 字段 |

#### 属性组合速查

| 组合 | 含义 | 实例 |
|------|------|------|
| `type: attr` | OCR 读文字，不点击 | 词条宫/商/角/徵/羽、装备类型 |
| `type: slot, is_text: false, is_clickable: true` | 纯点击目标，不 OCR | 装备槽位、背包格 |
| `type: func, is_text: false, is_clickable: true` | 图标按钮，点击但无需 OCR | 返回键、更多功能（`...`） |
| `type: func, is_text: true, is_clickable: true` | 文字按钮，既可 OCR 匹配也可点击 | 「调律」「一键添加」「包裹」 |

### Point — 坐标点（圆形交互锚点）

Point 描述场景内的**圆形交互锚点**。YAML 声明「场景里存在这样一个坐标点」（key/name/type/is_text/is_clickable），**不定义半径**——半径属于 Layout JSON 的实例数据，可在画布上通过拖动手柄自由调整。

Point 与 Region 共享相同的 type/is_text/is_clickable 属性体系（含义见上方 Region 章节），默认值不同：

| 属性 | Point 默认值 | 说明 |
|------|-------------|------|
| `type` | `func` | 坐标点通常是功能交互点 |
| `is_text` | `false` | 默认不 OCR（圆形区域较小） |
| `is_clickable` | `true` | 默认可点击 |

```yaml
# game_main_page.yaml 片段
points:
  - key: origin
    name: 起点
    type: func
    is_text: false
    is_clickable: true
  - key: forward
    name: 前进
    type: func
    is_text: false
    is_clickable: true
```

DSL 中通过 `click [game_main_page].[origin]` 点击坐标点中心（带半径内随机偏移，拟人化落点）。

---

## Panel — 可寻址容器

Panel 是 Scene 层定义的**复合区域**，内部包含可寻址的子元素。与 Area（Point/Region）不同，Panel 不直接对应单个交互点，而是提供一个可索引的容器。

### type — 容器类型

Panel 通过 `type` 字段区分内部结构，不同 type 决定不同的寻址方式：

| type | 语义 | 属性 | 寻址方式 | 状态 |
|------|------|------|----------|------|
| `grid` | 行列网格 | `rows`, `cols` | `[r][c]` | ✅ 已实现 |
| `regions` | Region 集合 | `regions: [...]` | `[name]` | 🔜 规划中 |

`type` 默认值为 `grid`，可省略。

### YAML 定义

```yaml
panels:
  # grid 型（默认，type 可省略）
  - key: bag_grid
    name: 背包网格
    rows: 3
    cols: 6

  # regions 型（规划中）
  - key: equip_slots
    name: 装备槽位组
    type: regions
    regions: [main_weapon, sub_weapon, ring, pendant]
```

### grid 型 Panel

当前唯一实现的类型。Panel 定义 `rows`/`cols` 后，内部每个格子通过 `[r][c]` 二维索引寻址（从 1 开始计数）。首次访问时自动触发图像自对齐（`align`），缓存各格子中心坐标。

DSL 中的使用：

```
click [scene].[panel][r][c]         # 点击格子中心
drag [scene].[panel][r][c] up 3     # 从该格子向上拖拽 3 行
align [scene].[panel]           # 手动触发对齐
```

### regions 型 Panel（规划中）

将多个已定义的 Region 合并到一个 Panel 中统一管理，通过 Region 的 key 名寻址。这样可以将逻辑上属于同一组的分散 Region 组合为一个可索引的整体。

> **设计意图**：当一组 Region 在语义上属于同一容器（如装备槽位组），但物理位置上不连续或不等间距时，用 `type=regions` 将它们聚合到一个 Panel 下，统一通过 `[name]` 寻址。

---

## Layout — 布局

一个 Layout 对应一套投屏方案（特定设备/分辨率），全局唯一。每个布局独立保存各场景的实例数据。

Layout 内部包含四个独立层次：

| 层次 | 职责 | 数据内容 |
|------|------|----------|
| **Area-Coord 绑定** | 位置 | 每个 Area（Point / Region）在屏幕上的归一化坐标 |
| **Area-Action 绑定** | 激活方式 | 可选 `activation_key`；为空时默认点击坐标 |
| **Panel-Coord 绑定** | 位置 | 每个 Panel 在屏幕上的归一化坐标（grid 型还包含校准缓存） |
| **Action → Arrow** | 行为 | 基于 Area 的拖拽动作（from → to） |

### Area-Coord 绑定

将 Scene 中定义的每个 Area 绑定到屏幕上的实际坐标：

- **Region** 的坐标：`(x_ratio, y_ratio, w_ratio, h_ratio)` — 画布内归一化矩形
- **Point** 的坐标：`(cx_ratio, cy_ratio, r_ratio)` — 中心 + 半径，画布内归一化

Region / Point 实例还可设置可选的 `activation_key`。它属于 Layout，而不属于
Scene：同一个语义实体在手游布局中可保持默认坐标点击，在桌面布局中可绑定
`SPACE`、`ESC`、`R` 等标准键名。DSL 仍统一写 `click [scene].[entity]`。

布局编辑器中“按键”留空表示默认左键点击；填入按键表示用该键激活。实体的
坐标仍会保留并继续用于 `scan` / `recognize`，按键绑定只改变 `click` 的动作。

#### 删定义要连坐标一起删

删除 Region / Point / Panel 定义时，`delete_item_key_across_all_layouts()`
会把**每一套布局** JSON 里对应的坐标记录一并删掉，画布上的那份也同步移除
（`RegionCanvas.remove_item`）。

三处缺一不可：

- 只删场景 YAML → 坐标留在每份布局文件里；
- 不删画布 → 下一次保存布局会把它原样写回去，刚删就复活；
- 只清内存（加载期的孤儿清理）→ 没打开过的布局文件里那条记录一直躺着。

残留的代价不只是脏数据：回头新建一个**同 key** 的实体，旧坐标会直接被当成
它的坐标，位置莫名其妙且完全静默；同名的跨场景引用也会被它顶掉。

删 Point 连带删掉以它为端点的 arrow（`from_key` / `to_key` 任一命中）——端点
没了的 arrow 既画不出来也跑不了，留着就是下一条残留。

加载期的 `_drop_orphan_coords()` 保留为兜底，处理存量与外部改动，不再是唯一
防线。跨场景迁移走 `migrate_item_across_layouts()`，不经这条路。

被别的场景**引用**的定义删不掉——先报出引用方，见
[03-cross-scene-references.md §8](03-cross-scene-references.md)。

### 跨场景 area 引用

一块屏幕区域只应有一处坐标真源。其他场景需要它时用 `references` 声明引用，
坐标运行期从源场景转读，不复制：

```yaml
references:
- scene: general_control
  entity: confirm
  view: reset_confirm
```

只能引用**一级场景**（其坐标同属画布归一化，零变换），与几何嵌套的
`subscene_refs` 是两件事。详见
[03-cross-scene-references.md](03-cross-scene-references.md)。

### Action → Arrow

Arrow 是**纯 Layout 的行为数据**，不在 Scene YAML 中定义。每条 arrow 描述一次从某 Point 到另一 Point 或绝对坐标的拖拽：

```json
{
  "arrows": {
    "equip_tune_detail": [
      { "key": "tune_drag", "from_key": "slider_handle", "to_key": "target_slot" },
      { "key": "fallback_drag", "from_key": "slider_handle", "to_cx_ratio": 0.8, "to_cy_ratio": 0.5 }
    ]
  }
}
```

- **吸附态**（`to_key`）：终点绑定到另一个 Point，动态查询坐标
- **绝对态**（`to_cx_ratio` / `to_cy_ratio`）：终点固定为画布内归一化坐标

DSL 中通过 `drag [equip_tune_detail].[tune_drag]` 执行拖拽。

### 实例数据

布局 JSON 存储上述数据（`config/local/layouts/{布局名}.json`）：

```json
{
  "canvas": {
    "x_ratio": 0.0, "y_ratio": 0.0,
    "w_ratio": 1.0, "h_ratio": 1.0
  },
  "scenes": {
    "scene_key": {
      "regions": [
        { "key": "region_key", "x_ratio": 0.1, "y_ratio": 0.2, "w_ratio": 0.3, "h_ratio": 0.1, "activation_key": "SPACE" }
      ],
      "points": [
        { "key": "point_key", "cx_ratio": 0.5, "cy_ratio": 0.7, "r_ratio": 0.015 }
      ],
      "panels": [
        { "key": "panel_key", "x_ratio": 0.1, "y_ratio": 0.3, "w_ratio": 0.8, "h_ratio": 0.4 }
      ],
      "arrows": [
        { "key": "arrow_key", "from_key": "point_a", "to_key": "point_b" }
      ]
    }
  }
}
```

---

## 存储结构

```
config/
├── system/
│   └── scenes/                    # Scene YAML 定义（逻辑结构）
│       ├── bag_equip_detail.yaml
│       ├── equip_weapon_detail.yaml
│       └── ...
└── local/
    ├── layouts/                   # Layout JSON（物理绑定）
    │   ├── 默认布局.json
    │   └── 投屏布局.json
    └── screenshots/               # 场景截图（编辑器参考底图）
        ├── 默认布局/
        │   ├── bag_equip_detail.png
        │   └── ...
        └── 投屏布局/
            └── ...
```
