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

| 子需求 | 文档 | 核心能力 | 优先级 | 状态 |
|--------|------|----------|--------|------|
| 玩家数据模型 | 本文档 §3-§7 | quota/regen/stock 三模型 + SQLite + UI | P0 | ✅ 已完成 |
| 毕业率分析 | [01-graduation-rate.md](02-player-profile/01-graduation-rate.md) | 角色信息采集 → Excel 计算 → 毕业率展示 | P2 | 🔲 未开始 |
| 货币追踪 | [02-currency-tracking.md](02-player-profile/02-currency-tracking.md) | 货币 OCR 识别 → 历史趋势 → 资产面板 | P1 | 🔲 未开始 |
| 心力体力管理 | [03-stamina-management.md](02-player-profile/03-stamina-management.md) | 资源监控 → 恢复预测 → 超标预警 | P0 | ✅ 已完成（基础框架） |

### 2.1 已完成功能（v0.2.0+）

**数据模型层**
- 三模型架构：quota（配额/周期任务）、regen（再生/恢复状态）、stock（存量/资源计数）
- SQLite 持久化：`config/session/profile.db`（WAL 模式 + busy_timeout）
- 变更历史：`profile_history` 表记录所有变更（action/manual/tick 三类）
- 周期自动重置：quota 到期自动清零，支持 day/week/month/season/half_season
- 再生自动计算：regen 按 regen_period + regen_value 回复，封顶 cap
- 超标预警：regen 达到 alert_above 阈值时触发提醒

**配置层**
- `config/session/profile.yaml`：按模型归档的 key 定义
- 支持自定义增减幅度（steps）、同步目标（sync_to）、增量模式（increment_only）
- 支持自定义重置日（reset_day）、软上限（soft）

**UI 层**
- 档案总览 Tab：宽表展示所有角色的概要信息，交互式列头配置
- 其他信息 Tab：按模型类型分区展示当前用户的详细信息
- 右键菜单：支持覆写、查看历史记录
- 定义面板：支持编辑 key 定义（增删改查）
- 分组管理：支持自定义分组和列配置

**后台引擎**
- ProfileEngine（QThread）：每 60 秒 tick 一次
- 周期检查与重置
- 再生计算
- 超标预警触发

### 2.2 待实现功能

**角色基础数据（跨子需求共享）**

| 字段 | 类型 | 说明 | 示例 | 状态 |
|------|------|------|------|------|
| name | str | 角色名（主键） | 蔡元君 | 🔲 |
| niao_level | int | 袅袅等级 | 1-3 | 🔲 |
| shop_enabled | bool | 是否开启商店 | Y/空 | 🔲 |
| note | str | 角色备注/定位说明 | "主玩会心双刀" | 🔲 |

**玩法进度（可选，后续扩展）**

| 字段 | 类型 | 说明 | 状态 |
|------|------|------|------|
| xiajing | bool | 侠境 | 🔲 |
| zuochuan | bool | 坐船 | 🔲 |
| chuanxiang | bool | 船箱 | 🔲 |
| kouyu | bool | 鯫鱼 | 🔲 |
| zhige | bool | 止戈 | 🔲 |
| jue_zhang | bool | 觉樟 | 🔲 |
| zuiye | bool | 罪叶 | 🔲 |
| huashu | bool | 话术 | 🔲 |

**地区解锁状态（可选，后续扩展）**

| 字段 | 类型 | 说明 | 状态 |
|------|------|------|------|
| region_qinghe | bool | 清河 | 🔲 |
| region_kaifeng | bool | 开封 | 🔲 |
| region_hexi | bool | 河西 | 🔲 |
| region_bujianshan | bool | 不见山 | 🔲 |
| region_huanggong | bool | 皇宫 | 🔲 |
| region_qingzhou | bool | 青州 | 🔲 |
| region_jiangnan | bool | 江南 | 🔲 |

**调律材料库存（独立模块，后续扩展）**

| 字段 | 类型 | 说明 | 状态 |
|------|------|------|------|
| chengyin_stone | int | 承音石数量 | 🔲 |
| zhuanlv_stone | int | 转律石数量 | 🔲 |
| bianyin_stone | int | 变音石数量 | 🔲 |
| zhuanlv_reserve | int | 转律石储备 | 🔲 |
| bianyin_reserve | int | 变音石储备 | 🔲 |
| colorful_food | int | 彩色狗粮数量 | 🔲 |
| dingyin_stone | int | 定音石数量 | 🔲 |
| total_zhuanlv | int | 总转律石（计算值） | 🔲 |
| total_bianyin | int | 总变音石（计算值） | 🔲 |
| material_snapshot_time | datetime | 材料快照时间 | 🔲 |

> 调律材料库存可作为后续独立子需求，支持 OCR 识别 + 手动录入双模式。

---

## 3. 系统架构

### 3.1 数据流总览

```
游戏场景
    ↓ 导航 subcall（待实现）
场景截屏
    ↓ OCR 识别（待实现）
结构化数据（角色信息/货币/心力体力）
    ↓ 持久化
玩家档案存储（SQLite: profile.db）
    ↓ 计算/预测
分析结果（毕业率/趋势/预警）
    ↓ UI 渲染
主页面面板展示
```

### 3.2 模块划分

```
src/lvjiang/apps/yysls/
├── config/                    # 配置模块（统一管理游戏配置 + 玩家元数据）
│   ├── constants.py           # 游戏常量
│   ├── manager.py             # 游戏配置管理器
│   ├── models.py              # 游戏配置模型
│   ├── profile_models.py      # 玩家数据模型定义（QuotaKeyDef/RegenKeyDef/StockKeyDef）
│   ├── profile_store.py       # 档案总览会话数据存储（分组配置、提醒历史）
│   └── user_profile.py        # 玩家数据模型配置加载（profile.yaml）
├── profile/                   # 玩家档案核心
│   ├── profile_db.py          # SQLite 存储层（profile_entries + profile_history）
│   └── profile_engine.py      # 后台计算引擎（周期重置 + 再生计算 + 预警）
└── ui/
    ├── profile_tab.py         # 档案总览 + 其他信息 Tab
    └── profile_settings_dialog.py  # 定义面板（编辑 key 定义）
```

### 3.3 存储方案

```
config/
  session/
    profile.yaml              # 玩家数据模型 key 定义（按 quota/regen/stock 归档）
    profile.db                # SQLite 数据库（当前值 + 变更历史）
    session.json              # 会话状态（分组配置、提醒历史、UI 状态）
```

**profile.yaml 示例**：

```yaml
quota:
- key: niaoniao_of_week
  label: 袅袅进度
  cap: 3
  steps: [1]
  sync_to: niaoniao
  increment_only: true
- key: bugan_of_week
  label: 不肝进度
  cap: 23000
  steps: [1000, 23000]
  sync_to: bugan
  increment_only: true

regen:
- key: tili
  label: 体力
  cap: 2500
  regen_period: day
  regen_value: 450.0
  alert_above: 2150
  steps: [-900, -1100, -2400]
- key: xinli
  label: 心力
  cap: 600
  regen_value: 0.125
  alert_above: 480
  steps: [-60, -180, -360, -480]

stock:
- key: niaoniao
  label: 袅袅之音
  steps: [-1, -10]
- key: baoqian
  label: 宝钱(万)
- key: changmingyu
  label: 长鸣玉
  steps: [-200, -400]
```

**profile.db schema**：

```sql
-- 当前值（upsert 覆盖）
CREATE TABLE profile_entries (
    username   TEXT NOT NULL,
    type       TEXT NOT NULL,  -- quota/regen/stock
    key        TEXT NOT NULL,
    value      REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (username, type, key)
);

-- 变更历史（append-only）
CREATE TABLE profile_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    username    TEXT    NOT NULL,
    type        TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    old_value   REAL,
    new_value   REAL    NOT NULL,
    change_type TEXT    NOT NULL,  -- reset/regen/manual/override/sync_from/sync_to
    detail      TEXT    DEFAULT ''
);
```

---

## 4. UI 设计

### 4.1 档案总览 Tab（已实现）

宽表展示所有角色的概要信息，支持交互式列头配置：

```
┌─────────────────────────────────────────────────────────────┐
│  [分组: 默认]  [刷新]                                        │
├─────────────────────────────────────────────────────────────┤
│  角色名  │ 袅袅  │ 不肝   │ 体力    │ 心力   │ 宝钱  │ ...  │
├─────────────────────────────────────────────────────────────┤
│  蔡元君  │ 2/3   │ 12000  │ 1800/2500│ 450/600│ 1.2万 │ ...  │
│  李元霸  │ 0/3   │ 5000   │ 2400/2500│ 580/600│ 0.8万 │ ...  │
└─────────────────────────────────────────────────────────────┘
```

**交互功能**：
- 双击 cell 编辑（计算 delta，走 action 路径）
- 右键菜单：覆写、查看历史记录
- 列头拖拽调整顺序和宽度
- 分组切换

### 4.2 其他信息 Tab（已实现）

按模型类型分区展示当前用户的详细信息：

```
┌─────────────────────────────────────────┐
│  配额（quota）                           │
│  袅袅进度: 2/3  ████████░░  67%         │
│  不肝进度: 12000/23000  ████████░░  52% │
├─────────────────────────────────────────┤
│  再生（regen）                           │
│  体力: 1800/2500  ████████████░░  72%   │
│  心力: 450/600  ████████████████░░  75% │
├─────────────────────────────────────────┤
│  存量（stock）                           │
│  袅袅之音: 12  宝钱: 1.2万  长鸣玉: 450 │
└─────────────────────────────────────────┘
```

### 4.3 定义面板（已实现）

编辑 key 定义的对话框：

```
┌─────────────────────────────────────────┐
│  [新增] [删除]                           │
├─────────────────────────────────────────┤
│  key: niaoniao_of_week                  │
│  label: 袅袅进度                         │
│  cap: 3                                 │
│  steps: [1]                             │
│  sync_to: niaoniao                      │
│  increment_only: ☑                      │
└─────────────────────────────────────────┘
```

### 4.4 待实现 UI

**毕业率面板**（待实现）
```
┌─────────────────────────────────────────┐
│  [流派: 会心双刀]  ████████░░  82%       │
└─────────────────────────────────────────┘
```

**货币面板**（待实现）
```
┌─────────────────────────────────────────┐
│  宝钱: 12,345  长鸣玉: 567  不肝: 89    │
│  长鸣珠: 12    通宝: 3,456              │
└─────────────────────────────────────────┘
```

---

## 5. 导航链路

需要新增以下 DSL subcall（待实现）：

| subcall | 功能 | 起点 | 终点 | 状态 |
|---------|------|------|------|------|
| `nav_to_character.wf` | 导航到角色详情 | 主页面 | 角色详情页 | 🔲 |
| `nav_to_currency.wf` | 导航到货币页面 | 主页面 | 货币页面 | 🔲 |
| `nav_to_stamina.wf` | 导航到心力体力页面 | 主页面 | 心力体力页面 | 🔲 |
| `read_character_info.wf` | 读取角色信息 | 角色详情页 | 返回主页面 | 🔲 |
| `read_currencies.wf` | 读取货币数据 | 货币页面 | 返回主页面 | 🔲 |
| `read_stamina.wf` | 读取心力体力 | 心力体力页面 | 返回主页面 | 🔲 |

---

## 6. 依赖关系

### 6.1 与现有模块的关系

- **装备分析**：毕业率分析需要读取装备数据，复用现有 `EquipmentData` 模型
- **批处理**：多账号场景下，玩家档案按账号隔离
- **用户管理**：与现有 `UserConfigManager` 集成

### 6.2 外部依赖

- **SQLite**：✅ 已内置（Python 标准库）
- **Excel 集成**：🔲 毕业率计算需要引入 `openpyxl` 依赖（待实现）
- **图表库**：🔲 历史趋势图需要引入 `pyqtgraph` 或 `matplotlib`（待实现）

---

## 7. 实施路线

### Phase 1（MVP）— 玩家数据模型 ✅ 已完成
- ✅ 三模型架构（quota/regen/stock）
- ✅ SQLite 持久化
- ✅ 后台计算引擎
- ✅ UI 总览 + 详情
- ✅ 定义面板
- ✅ 超标预警

### Phase 2 — 心力体力 OCR 识别 🔲 待实现
- 🔲 心力体力 OCR 识别
- 🔲 自动采集工作流
- 🔲 恢复预测增强
- 预计工时：2-3 天

### Phase 3 — 货币追踪 🔲 待实现
- 🔲 货币 OCR 识别
- 🔲 货币历史追踪
- 🔲 资产面板展示
- 预计工时：2-3 天

### Phase 4 — 毕业率分析 🔲 待实现
- 🔲 角色信息 OCR 识别
- 🔲 Excel 集成与计算
- 🔲 毕业率面板展示
- 预计工时：4-6 天

---

## 8. 风险与待确认

### 8.1 风险点

1. **OCR 准确率**：角色信息、货币数字的 OCR 准确率需要验证
2. **Excel 依赖**：引入 Excel 计算会增加外部依赖，需要考虑跨平台兼容性
3. **导航稳定性**：新增场景导航需要充分测试
4. **数据一致性**：多账号场景下数据并发写入问题（已通过 WAL + busy_timeout 缓解）

### 8.2 待确认事项

1. ~~角色信息具体包含哪些字段？~~ → 已通过 profile.yaml 的 quota/regen/stock 定义解决
2. Excel 模板由用户提供还是我们设计？
3. 毕业率计算逻辑是否涉及多维度（PVE/PVP/副本）？
4. ~~体力 +450 是什么？每日登录奖励？~~ → 已确认为每日 05:00 重置的体力恢复量
5. 是否需要系统通知提醒（Windows 通知）？

---

## 9. 相关文档

| 文档 | 内容 | 状态 |
|------|------|------|
| 本文档 | 玩家档案系统需求总览 | ✅ |
| [01-graduation-rate.md](02-player-profile/01-graduation-rate.md) | 毕业率分析子需求 | 🔲 待更新 |
| [02-currency-tracking.md](02-player-profile/02-currency-tracking.md) | 货币追踪子需求 | 🔲 待更新 |
| [03-stamina-management.md](02-player-profile/03-stamina-management.md) | 心力体力管理子需求 | ✅ 基础框架已完成 |
| [README.md](README.md) | 律匠主需求文档 | ✅ |
