# 页面管理（场景区域编辑）

页面管理（原「区域编辑器」）中的「场景」对应游戏中不同的界面截图。每个场景由 YAML 定义其**字段类型**（`regions` + `points`），由布局 JSON 保存**实例数据**（坐标、半径、方向）。

## 场景列表

当前定义了九个场景：

| 场景 Key | 场景名称 | 用途 |
|----------|----------|------|
| `bag_equip_detail` | 装备背包详情 | 识别背包中各装备槽位的状态 |
| `bag_item_detail` | 道具背包 | 识别道具背包格 |
| `equip_weapon_detail` | 装备武器详情 | 识别武器的基础信息和词条分布 |
| `equip_armor_detail` | 装备防具详情 | 识别防具的基础信息和词条分布 |
| `equip_tune_detail` | 装备调律详情 | 识别调律后的词条变化 |
| `equip_tune_result` | 调律结果 | 识别调律结果页面的词条和关闭按钮 |
| `game_main_page` | 游戏主页 | 主界面功能按钮 + 移动坐标点（origin/前进/后退/向左/向右） |
| `game_menu_page` | 游戏菜单 | 菜单页功能按钮（包裹、培养、商店等） |
| `general_control` | 通用控制 | 跨场景复用的预定义坐标点（材料格、左屏点、背包格） |

---

## 字段属性语义

每个场景的 YAML 顶层结构：

```yaml
key: scene_key
name: 场景名称

regions:                        # OCR 可识别 / 可点击区域
  - key: region_key
    name: 区域名称
    type: attr                  # attr / slot / func
    is_text: true
    is_clickable: false

points:                         # 圆形交互锚点（不参与 OCR）
  - key: point_key
    name: 坐标名称
    # 注意：YAML 不定义 r_ratio，半径属于布局 JSON 实例数据
```

### type — 字段类型

决定字段在自动化流程中的**角色**，合法值三种：

| type | 语义 | 典型场景 | OCR 行为 | 可点击 |
|------|------|----------|----------|--------|
| `attr` | **数据属性** — 需要被 OCR 读取的文字内容 | 装备词条（宫/商/角/徵/羽）、装备类型、等级 | 始终 OCR | 不可点击（默认） |
| `slot` | **槽位** — 背包/装备栏中的可点击格子 | 装备槽位、背包格、材料格 | 不 OCR（默认 `is_text: false`） | 可点击 |
| `func` | **功能按钮** — 界面上的可操作控件 | 返回、调律按钮、一键添加、菜单入口 | 视 `is_text` 而定 | 可点击 |

### is_text — 是否进行 OCR 识别

| 值 | 含义 | 常见搭配 |
|----|------|----------|
| `true`（默认） | 工作流对该区域执行 OCR，提取文字内容 | `attr` 字段默认；部分需要文字匹配的 `func` 按钮 |
| `false` | 不执行 OCR，仅作为点击目标或占位区域 | `slot` 字段默认；图标类按钮（如返回、更多功能） |

**典型用例**：`click_match [scene].[var] "调律"` 指令依赖 `is_text: true` 的字段才能通过 OCR 文字匹配定位按钮。

### is_clickable — 是否可点击

| 值 | 含义 | 常见搭配 |
|----|------|----------|
| `false`（默认） | 该区域不会被点击，仅用于读取数据 | `attr` 字段 |
| `true` | 工作流可通过 `click [scene].[field]` 点击该区域中心 | `slot`、`func` 字段 |

### 属性组合速查

| 组合 | 含义 | 实例 |
|------|------|------|
| `type: attr` | OCR 读文字，不点击 | 词条宫/商/角/徵/羽、装备类型 |
| `type: slot, is_text: false, is_clickable: true` | 纯点击目标，不 OCR | 装备槽位、背包格 |
| `type: func, is_text: false, is_clickable: true` | 图标按钮，点击但无需 OCR | 返回键、更多功能（`...`） |
| `type: func, is_text: true, is_clickable: true` | 文字按钮，既可 OCR 匹配也可点击 | 「调律」「一键添加」「包裹」 |

---

## points — 坐标点（圆形交互锚点）

`points` 段描述场景内**不参与 OCR 的纯坐标交互点**。YAML 只声明「场景里存在这样一个坐标点」（key/name），**不定义半径**——半径属于布局 JSON 的 `Point` 实例数据，可在画布上通过拖动手柄自由调整。

```yaml
# game_main_page.yaml 片段
points:
  - key: origin
    name: 起点
  - key: forward
    name: 前进
```

布局 JSON 中对应的实例数据：

```json
{
  "points": {
    "game_main_page": [
      { "key": "origin", "cx_ratio": 0.50, "cy_ratio": 0.70, "r_ratio": 0.015 },
      { "key": "forward", "cx_ratio": 0.60, "cy_ratio": 0.70, "r_ratio": 0.020 }
    ]
  }
}
```

DSL 中通过 `click [game_main_page].[origin]` 点击坐标点中心（带半径内随机偏移，拟人化落点）。

---

## arrows — 方向（两点之间的拖拽）

`arrows` 是**纯布局 JSON 数据**，不在 YAML 中定义。每条 arrow 描述一次从某 point 到另一 point 或绝对坐标的拖拽：

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

- **吸附态**（`to_key`）：终点绑定到另一个 point，动态查询坐标
- **绝对态**（`to_cx_ratio` / `to_cy_ratio`）：终点固定为画布内归一化坐标

DSL 中通过 `drag [equip_tune_detail].[tune_drag]` 执行拖拽。

---

## 场景一：装备背包详情 (bag_equip_detail)

**游戏内位置**：打开背包 → 装备总览页

**需要识别的字段**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `slot_main_weapon` | 主武器 | 主武器槽位 |
| `slot_sub_weapon` | 副武器 | 副武器槽位 |
| `slot_ring` | 环 | 环槽位 |
| `slot_pendant` | 佩 | 佩槽位 |
| `slot_head` | 冠胄 | 头部装备槽位 |
| `slot_chest` | 胸甲 | 胸部装备槽位 |
| `slot_leg` | 胫甲 | 腿部装备槽位 |
| `slot_wrist` | 腕甲 | 腕部装备槽位 |
| `slot_bow` | 弓箭 | 弓箭槽位 |
| `slot_arrow` | 射玦 | 射玦槽位 |

**截图时机**：背包装备总览页完整显示时截图，用于快速扫描所有装备槽位状态。

---

## 场景二：道具背包 (bag_item_detail)

**游戏内位置**：打开背包 → 道具页

**需要识别的字段**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `bag_1_1` | 背包格1_1 | 道具背包格 |
| `bag_1_2` | 背包格1_2 | 道具背包格 |

**截图时机**：道具背包页完整显示时截图。

---

## 场景三：装备武器详情 (equip_weapon_detail)

**游戏内位置**：打开背包 → 选中武器 → 查看详情页

**需要识别的字段**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `equip_type` | 装备类型 | 如「横刀」「剑」「枪」等 |
| `equip_level` | 装备等级 | 如「100」「105」「110」 |
| `base_attr` | 基础属性 | 武器的基础攻击值 |
| `affix_gong` | 词条宫 | 宫位词条 |
| `affix_shang` | 词条商 | 商位词条 |
| `affix_jue` | 词条角 | 角位词条 |
| `affix_zhi` | 词条徵 | 徵位词条 |
| `affix_yu` | 词条羽 | 羽位词条 |

**截图时机**：装备详情页完整显示时截图，用于判断装备初始状态。

---

## 场景四：装备防具详情 (equip_armor_detail)

**游戏内位置**：打开背包 → 选中防具（头/胸/腿/腕）→ 查看详情页

**需要识别的字段**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `equip_type` | 装备类型 | 如「冠胄」「胸甲」「胫甲」「腕甲」等 |
| `equip_level` | 装备等级 | 如「100」「105」「110」 |
| `base_attr_1` | 基础属性1 | 防具第一基础属性（如气血） |
| `base_attr_2` | 基础属性2 | 防具第二基础属性（如防御） |
| `affix_gong` | 词条宫 | 宫位词条 |
| `affix_shang` | 词条商 | 商位词条 |
| `affix_jue` | 词条角 | 角位词条 |
| `affix_zhi` | 词条徵 | 徵位词条 |
| `affix_yu` | 词条羽 | 羽位词条 |

**截图时机**：防具详情页完整显示时截图，用于判断防具初始状态。

---

## 场景五：装备调律详情 (equip_tune_detail)

**游戏内位置**：调律界面 → 放入装备后 → 显示调律结果

**需要识别的字段**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `affix_gong` | 词条宫 | 调律后宫位词条 |
| `affix_shang` | 词条商 | 调律后商位词条 |
| `affix_jue` | 词条角 | 调律后角位词条 |
| `affix_zhi` | 词条徵 | 调律后徵位词条 |
| `affix_yu` | 词条羽 | 调律后羽位词条 |

**截图时机**：调律完成后、结果展示界面截图，用于对比调律前后词条变化。

---

## 场景六：调律结果 (equip_tune_result)

**游戏内位置**：调律完成后 → 结果展示弹窗

**需要识别的字段**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `tune_affix` | 调律词条 | 调律后获得的词条 |
| `close_btn` | 关闭 | 关闭按钮（功能按钮，不 OCR） |

**截图时机**：调律结果弹窗显示时截图，用于识别新获得的词条。

---

## 场景七：游戏主页 (game_main_page)

**游戏内位置**：登录后的主界面

**需要识别的字段（regions）**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `menu` | 菜单 | 菜单入口 |
| `activity` | 活动 | 活动入口 |
| `wulinlu` | 武林录 | 武林录入口 |
| `shop` | 商店 | 商店入口 |
| `battle_pass` | 战令 | 战令入口 |
| `harmony` | 和鸣 | 和鸣入口 |
| `map` | 地图 | 地图入口 |
| `team` | 组队 | 组队入口 |
| `summon_horse` | 唤马 | 唤马入口 |
| `listen_wind` | 听风辩位 | 听风辩位入口 |
| `more_func` | 更多功能 | `...` 折叠菜单 |
| `greet` | 动作 | 动作按钮 |
| `chat` | 聊天 | 聊天入口 |

**预定义坐标点（points）**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `origin` | 起点 | 角色默认站立位置 |
| `forward` | 前进 | 前进方向锚点 |
| `backward` | 后退 | 后退方向锚点 |
| `turn_left` | 向左 | 左转方向锚点 |
| `turn_right` | 向右 | 右转方向锚点 |

**截图时机**：主界面完整显示时截图。

---

## 场景八：游戏菜单 (game_menu_page)

**游戏内位置**：按 ESC 或点击菜单按钮打开

**需要识别的字段**：菜单内各功能入口（包裹、培养、商店等），详见 `config/system/scenes/game_menu_page.yaml`。

---

## 场景九：通用控制 (general_control)

**游戏内位置**：跨场景复用的通用坐标点

**预定义坐标点（points）**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `material_slot_1` | 材料格1 | 通用材料格 |
| `material_slot_2` | 材料格2 | 通用材料格 |
| `left_screen_1` | 左屏点1 | 左屏通用锚点 |
| `left_screen_2` | 左屏点2 | 左屏通用锚点 |
| `equip_slot_1` | 背包格1 | 通用背包格 |
| `equip_slot_2` | 背包格2 | 通用背包格 |

该场景无 `regions`，仅声明跨场景复用的坐标点。

---

## 场景与布局的关系

- **布局** = 一套投屏方案（对应特定设备/分辨率），全局唯一（不再绑定用户）
- 每个布局独立保存各场景的截图、`regions` 实例、`points` 实例、`arrows` 实例
- 存储路径：
  - 截图：`config/local/screenshots/{布局名}/{场景key}.png`
  - 布局：`config/local/layouts/{布局名}.json`

```
config/local/
├── layouts/
│   ├── 默认布局.json          # 含 canvas + scenes.{regions,points,arrows}
│   └── 投屏布局.json
└── screenshots/
    ├── 默认布局/
    │   ├── bag_equip_detail.png
    │   ├── equip_weapon_detail.png
    │   ├── equip_armor_detail.png
    │   ├── equip_tune_detail.png
    │   ├── equip_tune_result.png
    │   ├── game_main_page.png
    │   └── game_menu_page.png
    └── 投屏布局/
        └── ...
```

## 页面管理 UI 结构

打开方式：菜单「设置 → 页面管理」或 `F3`。

每个场景 Tab 由左右分栏构成：

- **左侧画布**：顶部工具栏（➕ 创建坐标 / → 创建方向）+ 截图画布
  - 黄色圆环 = 已放置的 point（可拖动中心、拖动手柄调半径、右上角 + 按钮拉 arrow）
  - 黄色矩形 = region（可拖动/缩放/吸附对齐）
  - 彩色带箭头线 = arrow（从 point 到 point 或绝对坐标）
  - 右键 point → 「复制 / 删除」菜单，复制沿用源半径便于快速创建同半径新坐标
  - 右键 region → 「复制 / 删除」菜单
- **右侧三 Tab**：
  - 区域列表（来自 YAML `regions`，✓ 已绑定 / ○ 未绑定）
  - 坐标列表（来自 YAML `points`，✓ 已放置 / ○ 未放置）
  - 方向列表（来自布局 JSON `arrows`，显示 `from_key → to_key` 或绝对坐标）

## 扩展新场景

在 `config/system/scenes/` 下新建 YAML 文件，并在 `config/default.yaml` 的 `layout_scenes` 中添加场景 key：

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
