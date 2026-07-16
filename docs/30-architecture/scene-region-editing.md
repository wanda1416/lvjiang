# 场景区域编辑

区域编辑器中的「场景」对应游戏中不同的界面截图，每个场景包含需要 OCR 识别的字段区域。

## 场景列表

当前定义了五个场景：

| 场景 Key | 场景名称 | 用途 |
|----------|----------|------|
| `bag_equip_detail` | 装备背包详情 | 识别背包中各装备槽位的状态 |
| `equip_weapon_detail` | 装备武器详情 | 识别武器的基础信息和词条分布 |
| `equip_armor_detail` | 装备防具详情 | 识别防具的基础信息和词条分布（含双基础属性） |
| `equip_tune_detail` | 装备调律详情 | 识别调律后的词条变化 |
| `equip_tune_result` | 调律结果 | 识别调律结果页面的词条和关闭按钮 |
| `game_main_page` | 游戏主页 | 主界面功能按钮（菜单、地图、移动等） |
| `game_menu_page` | 游戏菜单 | 菜单页功能按钮（包裹、培养、商店等） |

---

## 字段属性语义

每个场景字段由 YAML 定义，包含以下属性：

```yaml
fields:
  - key: affix_gong       # 字段唯一标识（代码引用名）
    name: 词条宫           # 显示名称（UI / 日志用）
    type: attr             # 字段类型：attr / slot / func
    is_text: true          # 是否需要 OCR 文字识别（仅 func 可设为 false）
    is_clickable: true     # 是否可作为点击目标
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

**典型用例**：`click_match "调律"` 指令依赖 `is_text: true` 的字段才能通过 OCR 文字匹配定位按钮。

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

## 场景二：装备武器详情 (equip_weapon_detail)

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

## 场景三：装备防具详情 (equip_armor_detail)

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

## 场景四：装备调律详情 (equip_tune_detail)

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

## 场景五：调律结果 (equip_tune_result)

**游戏内位置**：调律完成后 → 结果展示弹窗

**需要识别的字段**：

| 字段 Key | 字段名称 | 说明 |
|----------|----------|------|
| `tune_affix` | 调律词条 | 调律后获得的词条 |
| `close_btn` | 关闭 | 关闭按钮（功能按钮，不 OCR） |

**截图时机**：调律结果弹窗显示时截图，用于识别新获得的词条。

---

## 场景与布局的关系

- **布局** = 一套投屏方案（对应特定设备/分辨率）
- 每个布局独立保存各场景的截图和区域坐标
- 存储路径：`config/local/screenshots/{布局名}/{场景key}.png`

```
config/local/screenshots/
├── 默认布局/
│   ├── bag_equip_detail.png        # 背包总览页截图
│   ├── equip_weapon_detail.png   # 武器详情页截图
│   ├── equip_armor_detail.png    # 防具详情页截图
│   └── equip_tune_detail.png     # 调律结果页截图
└── 投屏布局/
    ├── bag_equip_detail.png
    ├── equip_weapon_detail.png
    ├── equip_armor_detail.png
    └── equip_tune_detail.png
```

## 扩展新场景

在 `config/system/scenes/` 下新建 YAML 文件，并在 `config/system/app.yaml` 的 `layout_scenes` 中添加场景 key：

```yaml
# config/system/scenes/transfer.yaml
key: transfer
name: 转律界面
fields:
  - key: cost
    name: 消耗材料
    type: attr
  - key: confirm_btn
    name: 确认
    type: func
    is_text: true
    is_clickable: true
  # ...
```

```yaml
# config/system/app.yaml
layout_scenes:
  - bag_equip_detail
  - equip_weapon_detail
  - equip_armor_detail
  - equip_tune_detail
  - equip_tune_result
  - transfer  # 新增
```
