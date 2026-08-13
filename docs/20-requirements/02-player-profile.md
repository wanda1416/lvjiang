# 玩家档案系统 — 需求总览

## 1. 背景

当前燕云插件仅实现了装备分析功能（读取当前穿戴装备、调律决策），但玩家在游戏中还有大量其他维度的数据需要追踪和管理：

- **毕业率**：角色装备距离流派毕业标准的进度
- **货币资产**：各类货币的存量与趋势
- **心力体力**：资源恢复状态与超标预警

本需求旨在构建完整的「玩家档案系统」，实现多维度游戏数据的自动采集、持久化存储、智能分析与可视化展示。

---

## 2. 需求范围

本需求拆分为三个子需求，分别独立实施：

| 子需求 | 文档 | 核心能力 | 优先级 |
|--------|------|----------|--------|
| 毕业率分析 | [01-graduation-rate.md](02-player-profile/01-graduation-rate.md) | 角色信息采集 → Excel 计算 → 毕业率展示 | P2 |
| 货币追踪 | [02-currency-tracking.md](02-player-profile/02-currency-tracking.md) | 货币 OCR 识别 → 历史趋势 → 资产面板 | P1 |
| 心力体力管理 | [03-stamina-management.md](02-player-profile/03-stamina-management.md) | 资源监控 → 恢复预测 → 超标预警 | P0 |

### 2.1 角色基础数据（跨子需求共享）

以下数据属于角色档案基础信息，由三个子需求共享引用：

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| name | str | 角色名（主键） | 蔡元君 |
| niao_level | int | 袅袅等级 | 1-3 |
| shop_enabled | bool | 是否开启商店 | Y/空 |
| note | str | 角色备注/定位说明 | "主玩会心双刀" |

### 2.2 玩法进度（可选，后续扩展）

| 字段 | 类型 | 说明 |
|------|------|------|
| xiajing | bool | 侠境 |
| zuochuan | bool | 坐船 |
| chuanxiang | bool | 船箱 |
| kouyu | bool | 鯫鱼 |
| zhige | bool | 止戈 |
| jue_zhang | bool | 觉樟 |
| zuiye | bool | 罪叶 |
| huashu | bool | 话术 |

### 2.3 地区解锁状态（可选，后续扩展）

| 字段 | 类型 | 说明 |
|------|------|------|
| region_qinghe | bool | 清河 |
| region_kaifeng | bool | 开封 |
| region_hexi | bool | 河西 |
| region_bujianshan | bool | 不见山 |
| region_huanggong | bool | 皇宫 |
| region_qingzhou | bool | 青州 |
| region_jiangnan | bool | 江南 |

### 2.4 调律材料库存（独立模块，后续扩展）

| 字段 | 类型 | 说明 |
|------|------|------|
| chengyin_stone | int | 承音石数量 |
| zhuanlv_stone | int | 转律石数量 |
| bianyin_stone | int | 变音石数量 |
| zhuanlv_reserve | int | 转律石储备 |
| bianyin_reserve | int | 变音石储备 |
| colorful_food | int | 彩色狗粮数量 |
| dingyin_stone | int | 定音石数量 |
| total_zhuanlv | int | 总转律石（计算值） |
| total_bianyin | int | 总变音石（计算值） |
| material_snapshot_time | datetime | 材料快照时间 |

> 调律材料库存可作为后续独立子需求，支持 OCR 识别 + 手动录入双模式。

---

## 3. 系统架构

### 3.1 数据流总览

```
游戏场景
    ↓ 导航 subcall
场景截屏
    ↓ OCR 识别
结构化数据（角色信息/货币/心力体力）
    ↓ 持久化
玩家档案存储（按账号隔离）
    ↓ 计算/预测
分析结果（毕业率/趋势/预警）
    ↓ UI 渲染
主页面面板展示
```

### 3.2 模块划分

```
src/lvjiang/apps/yysls/
├── character/          # 角色信息模块
│   ├── parser.py       # 角色信息解析器
│   └── graduation.py   # 毕业率计算（Excel 集成）
├── currency/           # 货币模块
│   ├── recognizer.py   # 货币识别
│   └── tracker.py      # 货币追踪
├── stamina/            # 心力体力模块
│   ├── monitor.py      # 资源监控
│   └── predictor.py    # 超标预测
└── profile/            # 玩家档案核心
    ├── models.py       # 数据模型
    ├── storage.py      # 持久化存储
    └── manager.py      # 档案管理器
```

### 3.3 存储方案

```
config/
  session/
    users/
      {account}/
        profile.yaml      # 角色基本信息（含核心属性、玩法进度、地区解锁）
        currencies.jsonl  # 货币历史（追加写入）
        stamina.jsonl     # 心力体力历史（追加写入）
        graduation.json   # 毕业率快照
        materials.yaml    # 调律材料库存（后续扩展）
```

**profile.yaml 示例**：

```yaml
name: 蔡元君
niao_level: 3
shop_enabled: true
note: 主玩会心双刀
regions:
  qinghe: true
  kaifeng: true
  hexi: false
progress:
  xiajing: true
  zuochuan: false
```

---

## 4. UI 设计

### 4.1 装备状态 Tab 扩展

在现有装备状态 Tab 基础上新增三个面板：

```
┌─────────────────────────────────────────┐
│  毕业率面板（顶部）                       │
│  [流派: 会心双刀]  ████████░░  82%       │
├─────────────────────────────────────────┤
│  货币面板（中部）                         │
│  宝钱: 12,345  长鸣玉: 567  不肝: 89     │
│  长鸣珠: 12    通宝: 3,456              │
├─────────────────────────────────────────┤
│  心力体力面板（底部）                     │
│  账号A: 心力 580/600 ⚠️ 体力 1200/2500  │
│  账号B: 心力 300/600  体力 2400/2500 ⚠️ │
└─────────────────────────────────────────┘
```

### 4.2 交互设计

- **刷新按钮**：手动触发 OCR 识别当前场景数据
- **自动采集**：执行调律工作流时自动采集相关数据
- **历史趋势**：点击面板可展开历史趋势图（折线图）

---

## 5. 导航链路

需要新增以下 DSL subcall：

| subcall | 功能 | 起点 | 终点 |
|---------|------|------|------|
| `nav_to_character.wf` | 导航到角色详情 | 主页面 | 角色详情页 |
| `nav_to_currency.wf` | 导航到货币页面 | 主页面 | 货币页面 |
| `nav_to_stamina.wf` | 导航到心力体力页面 | 主页面 | 心力体力页面 |
| `read_character_info.wf` | 读取角色信息 | 角色详情页 | 返回主页面 |
| `read_currencies.wf` | 读取货币数据 | 货币页面 | 返回主页面 |
| `read_stamina.wf` | 读取心力体力 | 心力体力页面 | 返回主页面 |

---

## 6. 依赖关系

### 6.1 与现有模块的关系

- **装备分析**：毕业率分析需要读取装备数据，复用现有 `EquipmentData` 模型
- **批处理**：多账号场景下，玩家档案按账号隔离
- **用户管理**：与现有 `UserConfigManager` 集成

### 6.2 外部依赖

- **Excel 集成**：毕业率计算需要引入 `openpyxl` 依赖
- **图表库**：历史趋势图需要引入 `pyqtgraph` 或 `matplotlib`

---

## 7. 实施路线

### Phase 1（MVP）— 心力体力管理
- 心力体力 OCR 识别
- 数据持久化与恢复预测
- 超标预警报表
- 预计工时：3-5 天

### Phase 2 — 货币追踪
- 货币 OCR 识别（复用参考图库）
- 货币历史追踪
- 资产面板展示
- 预计工时：2-3 天

### Phase 3 — 毕业率分析
- 角色信息 OCR 识别
- Excel 集成与计算
- 毕业率面板展示
- 预计工时：4-6 天

---

## 8. 风险与待确认

### 8.1 风险点

1. **OCR 准确率**：角色信息、货币数字的 OCR 准确率需要验证
2. **Excel 依赖**：引入 Excel 计算会增加外部依赖，需要考虑跨平台兼容性
3. **导航稳定性**：新增场景导航需要充分测试
4. **数据一致性**：多账号场景下数据并发写入问题

### 8.2 待确认事项

1. 角色信息具体包含哪些字段？
2. Excel 模板由用户提供还是我们设计？
3. 毕业率计算逻辑是否涉及多维度（PVE/PVP/副本）？
4. 体力 +450 是什么？每日登录奖励？
5. 是否需要系统通知提醒（Windows 通知）？

---

## 9. 相关文档

| 文档 | 内容 |
|------|------|
| [01-graduation-rate.md](02-player-profile/01-graduation-rate.md) | 毕业率分析子需求 |
| [02-currency-tracking.md](02-player-profile/02-currency-tracking.md) | 货币追踪子需求 |
| [03-stamina-management.md](02-player-profile/03-stamina-management.md) | 心力体力管理子需求 |
| [README.md](README.md) | 律匠主需求文档 |
