# 用户 Profile 系统 — 需求总览
> 状态：部分实现（2026-10-08，基线 v0.13.14）。四模型数据层已实现；角色基础数据、玩法进度、地区解锁与材料库存字段待补。

> 最后更新：2026-10-08（基线 v0.13.14）。本层描述如与代码或配置不符，以代码与配置为准。

## 1. 背景

Profile 是主引擎共享的用户数据能力。它不属于燕云插件；燕云只在
通用四模型上注册赛季周期，并使用这份共享数据追踪游戏内的业务指标：

- **毕业率**：角色装备距离流派毕业标准的进度
- **货币资产**：各类货币的存量与趋势
- **心力体力**：资源恢复状态与超标预警

本需求旨在构建完整的「玩家档案系统」，实现多维度游戏数据的自动采集、持久化存储、智能分析与可视化展示。

---

## 2. 需求范围

本需求拆分为三个子需求，分别独立实施：

| 子需求 | 文档 | 核心能力 | 优先级 | 状态 |
|--------|------|----------|--------|------|
| 玩家数据模型 | 本文档 §3-§7 | quota/regen/stock/note 四模型 + SQLite + UI | P0 | ✅ 已完成 |
| 毕业率分析 | [01-graduation-rate.md](01-graduation-rate.md) | 角色装备 → 毕业率模型 → 毕业率展示 | P2 | ✅ 已实现（实现方式与子文档早期方案不同） |
| 货币追踪 | [02-currency-tracking.md](02-currency-tracking.md) | 钱袋 OCR 识别 → Profile stock 模型 | P1 | ✅ 已实现（实现方式与子文档早期方案不同） |
| 心力体力管理 | [03-stamina-management.md](03-stamina-management.md) | 资源监控 → 恢复预测 → 超标预警 | P0 | ✅ 已完成（基础框架） |

### 2.1 已完成功能（v0.2.0+）

**数据模型层**
- 四模型架构：quota（配额/周期任务）、regen（再生/恢复状态）、stock（存量/资源计数）、
  note（自由文本备注——非数值状态，如「主玩会心双刀」这类无法归入前三种数值模型的标记）
- SQLite 持久化：`config/session/profile.db`（WAL 模式 + busy_timeout）
- 变更历史：区分主动操作 action、覆写 override、自动恢复 tick 和周期重置 reset
- 周期自动重置：quota 到期自动清零，支持 day/week/month/season/half_season
- 再生自动计算：regen 显式区分 realtime（按速率连续恢复）与 boundary（按准点边界恢复），封顶 cap
- 超标预警：regen 达到 alert_above 阈值时触发提醒

**配置层**
- `config/session/profile.yaml`：按模型归档的 key 定义
- 支持自定义增减幅度（steps）、同步目标（sync_to）、增量模式（increment_only）
- 支持自定义重置日（reset_day）、软上限（soft）

**UI 层**
- 用户总览 Tab：宽表展示所有用户的概要信息，交互式列头配置
- 用户信息 Tab：按模型类型分区展示当前用户的详细信息
- 右键菜单：支持覆写、查看历史记录
- 定义面板：支持编辑 key 定义（增删改查）；已有 key 保持只读但可选中、复制，点击“编辑”后才能改名
- 分组管理：支持自定义分组和列配置；表头右侧新增列支持搜索、多选与整组勾选

**后台引擎**
- ProfileEngine（QThread）：每 60 秒 tick 一次
- 周期检查与重置
- 再生计算
- 超标预警触发
- 数据模型可关联一个变更脚本；数据实际变化后只提交事件，脚本由独立 FIFO
  队列异步串行执行，不阻塞 UI 或发起写入的工作流
- 变更脚本不获取也不检查用户执行锁，可以与同一用户的设备任务并行；触发链
  携带原始 key，并在写入前阻止循环
- 用户总览工具栏仅用临时绿色状态灯表示队列忙碌，不因队列状态刷新总览数据

### 2.2 待实现功能

**角色基础数据（跨子需求共享）**

| 字段 | 类型 | 说明 | 示例 | 状态 |
|------|------|------|------|------|
| name | str | 角色名（主键） | 测试用户A | 🔲 |
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
src/lvjiang/
├── core/profile/
│   ├── models.py       # quota/regen/stock/note 数据定义
│   ├── schema.py       # profile.yaml 加载与校验
│   ├── store.py        # session.json.profile 分组和告警状态
│   ├── repository.py   # profile.db 存储层
│   ├── engine.py       # 周期重置、再生计算和预警
│   ├── service.py      # 统一读写语义
│   ├── regen.py        # realtime / boundary 计算
│   ├── sync.py         # sync_targets 同步
│   └── periods.py      # 通用周期注册表
├── ui/profile/               # 用户总览、用户信息和定义面板
├── workflows/builtins/profile.py
│                           # 通用 profile_action/profile_read 等 DSL 函数
└── apps/yysls/core/season_periods.py
                            # 燕云 season/half_season 周期边界
```

详细边界与不变的存储契约见
[Profile 共享模块](../../30-architecture/31-models/04-profile.md)。

### 3.3 存储方案

```
config/
  session/
    profile.yaml              # 玩家数据模型 key 定义（按 quota/regen/stock 归档）
    profile.db                # SQLite 数据库（当前值 + 变更历史）
    session.json              # 会话状态（分组配置、提醒历史、UI 状态）
```

系统业务工作流依赖的 key 定义集中在
`config/system/workflows/subcall/profile_registry.wf`。涉及 Profile 的工作流在正文开头把
所需 key 列表传给唯一的 `declare_profiles` 过程；该过程只补齐缺失定义，不覆盖已有同类型
定义。未知 key 或同名异类型定义必须终止声明，避免静默写错模型。批量前置检查不执行正文，
因此若它需要读取 Profile，也必须先调用同一个过程。

**profile.yaml 示例**：

```yaml
quota:
- key: niaoniao_of_week
  group: default
  label: 袅袅进度
  cap: 3
  steps: [1]
  sync_to: niaoniao
  increment_only: true
- key: bugan_of_week
  group: default
  label: 不肝进度
  cap: 23000
  steps: [1000, 23000]
  sync_to: bugan
  increment_only: true

regen:
- key: tili
  group: default
  label: 体力
  cap: 2500
  regen_type: boundary
  regen_amount: 450.0
  regen_period: day
  alert_above: 2150
  steps: [-900, -1100, -2400]
- key: xinli
  group: default
  label: 心力
  cap: 600
  regen_type: realtime
  regen_rate_value: 0.125
  regen_rate_unit: minute
  alert_above: 480
  steps: [-60, -180, -360, -480]

stock:
- key: niaoniao
  group: default
  label: 袅袅之音
  steps: [-1, -10]
- key: baoqian
  group: 资产
  label: 宝钱(万)
- key: changmingyu
  group: 资产
  label: 长鸣玉
  steps: [-200, -400]

note:
- key: build_note
  group: default
  label: 配装定位
  cap: 7            # 可选：note 也能配上限/软上限/展示上限
  show_cap: true    # 值是数字才显示 X/Y；"主玩会心双刀" 这类文本不受影响，原样显示
```

每个 key 通过 `group` 声明类型内分组。缺失或空白值按 `default` 处理，保存时
显式写出；中文界面把 `default` 显示为「默认」。定义编辑器只展示当前分组，
分组列表从该模型的全部 key 按首次出现顺序派生，不单独创建空分组。分组使用与
模型类型一致的 Tab 样式，位于新增、删除、上移、下移按钮左侧；可用宽度不足时
显示滚动按钮。表格右键菜单提供「更改分组」；可编辑下拉框既列出本类型的已有
分组，也允许直接输入新名称。多选行时一次修改所有所选 key，双击分组列不触发
编辑。新增、删除、上移、下移只作用于当前分组中的 key。切换分组属于编辑器状态，
外层保存时提交尚未保存的分组和排序等草稿；单个 key 的内层保存即时生效，外层
取消只放弃剩余草稿，不撤销已经明确保存的定义修改。

这里的类型内分组属于 `profile.yaml` 的 key 定义，只组织定义编辑器中的长列表；
它与 `session.json.profile.overview_groups` 中用户总览的展示分组相互独立，不改变
总览分组、活动分组、数据库 key 或工作流引用。

**note 与其余三个模型的区别**：存的是自由文本而非数值，不进 `profile_history`
的数值变更轨迹，也不参与 `sync_targets` 同步。`cap`/`soft`/`show_cap` 字段
仍然可选：填的值能转成数字时按数值语义取整、按上限截断，展示上限时给出
`X/Y`；填的是纯文本（如「已完成」）则原样存、原样显示，不强行套上限——
真要按数量管理应该用 stock 而不是 note。

**profile.db schema**：

```sql
-- 当前值（upsert 覆盖）
CREATE TABLE profile_entries (
    username   TEXT NOT NULL,
    type       TEXT NOT NULL,  -- quota/regen/stock/note
    key        TEXT NOT NULL,
    value      REAL NOT NULL DEFAULT 0,
    value_text TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    updated_time TEXT NOT NULL DEFAULT '',
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
    old_value_text TEXT DEFAULT '',
    new_value_text TEXT DEFAULT '',
    change_type TEXT    NOT NULL,  -- action/override/tick/reset
    source      TEXT    DEFAULT '',
    delta_value REAL,
    sync_from   TEXT
);
```

---

## 4. UI 设计

### 4.1 用户总览 Tab（已实现）

宽表展示所有用户的概要信息，支持交互式列头配置：

```
┌─────────────────────────────────────────────────────────────┐
│  [分组: 默认]  [刷新]                                        │
├─────────────────────────────────────────────────────────────┤
│  用户名  │ 袅袅  │ 不肝   │ 体力    │ 心力   │ 宝钱  │ ...  │
├─────────────────────────────────────────────────────────────┤
│  测试用户A  │ 2/3   │ 12000  │ 1800/2500│ 450/600│ 1.2万 │ ...  │
│  测试用户B  │ 0/3   │ 5000   │ 2400/2500│ 580/600│ 0.8万 │ ...  │
└─────────────────────────────────────────────────────────────┘
```

**交互功能**：
- 双击 cell 编辑（计算 delta，走 action 路径）
- 单元格右键可覆写、查看该用户对当前 key 的历史；列头右键可查看所有用户对
  当前 key 的历史。两种入口共用一个对话框，单用户模式隐藏用户名列
- 历史按写入顺序倒序查询，默认每页 100 条；底部显示页码、总数和当前范围，
  可调整每页条数并翻到首页、上一页、下一页或末页
- 列头拖拽调整顺序和宽度
- 分组切换

### 4.2 用户信息 Tab（已实现）

按模型类型分区展示当前用户的详细信息（分区顺序：配额 → 库存 → 再生 → 备注）：

```
┌─────────────────────────────────────────┐
│  配额（quota）                           │
│  袅袅进度: 2/3  ████████░░  67%         │
│  不肝进度: 12000/23000  ████████░░  52% │
├─────────────────────────────────────────┤
│  库存（stock）                           │
│  袅袅之音: 12  宝钱: 1.2万  长鸣玉: 450 │
├─────────────────────────────────────────┤
│  再生（regen）                           │
│  体力: 1800/2500  ████████████░░  72%   │
│  心力: 450/600  ████████████████░░  75% │
├─────────────────────────────────────────┤
│  备注（note）                            │
│  配装定位: 主玩会心双刀                  │
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

### 4.4 key 重命名

既有 key 输入框默认禁用，必须通过 key 行的「编辑」按钮开启重命名。
数据模型定义与总览列右键入口共用相同对话框。点击内层「保存」立即完成定义修改
及重命名，不等待外层确认，也不会因外层取消而撤销。重命名作为全用户重操作
执行，不允许目标 key 与任何已有定义或数据冲突，也不允许改变模型类型。
取消尚未保存的内层编辑不落盘。

重命名保留当前值与旧历史，并更新结构化同步来源和相关配置引用。
内层编辑对话框的「查看 key 重命名记录」按钮打开独立审计页，显示曾用名与变更
时间；普通数值历史不展示重命名审计。尚未保存的新定义禁用该按钮。
同步写入同时记录实际变动量和同步来源；周期重置明确使用 reset，而非普通 tick。
旧数据库一次性升级，无法解释的旧详情留存核对，不能猜测为零或静默删除。

运行中的任务或未完成的 Profile 脚本队列必须先结束，再执行重命名。
工作流与变更脚本中的字符串引用不扫描、不自动替换，用户需自行核对和调整。
此能力从 v0.13.12 起正式提供；存储与恢复细节见
[Profile 共享模块](../../30-architecture/31-models/04-profile.md)。

### 4.5 待实现 UI

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

需要以下导航与读取能力；实际落点由装备、钱袋与不肝商店的扫描工作流承担，命名与
实现位置见实现文档，本节只登记需求（起点 / 终点 / 返回）：

| 能力 | 起点 | 终点 |
|------|------|------|
| 读取角色信息 | 主页面 | 角色详情页 → 返回主页面 |
| 读取货币 | 主页面 | 货币页面 → 返回主页面 |
| 读取心力体力 | 主页面 | 心力体力页面 → 返回主页面 |

---

## 6. 依赖关系

### 6.1 与现有模块的关系

- **装备分析**：毕业率分析需要读取装备数据，复用现有 `EquipmentData` 模型
- **批处理**：多用户数据依旧通过 `username` 区分
- **用户管理**：与现有 `UserConfigManager` 集成
- **App 共享**：Profile 不按 app 隔离，不增加 `app_id`；各 app 共享 key 命名空间

### 6.2 外部依赖

- **SQLite**：✅ 已内置（Python 标准库）
- **Excel 集成**：✅ 已引入 `openpyxl`，毕业率与 DPS 的公式模型由工作簿转换生成
- **图表库**：🔲 历史趋势图需要引入 `pyqtgraph` 或 `matplotlib`（待实现）

---

## 7. 实施路线

### Phase 1（MVP）— 玩家数据模型 ✅ 已完成
- ✅ 四模型架构（quota/regen/stock/note）
- ✅ SQLite 持久化
- ✅ 后台计算引擎
- ✅ UI 总览 + 详情
- ✅ 定义面板
- ✅ 超标预警

### Phase 2 — 心力体力 OCR 识别 🚧 部分实现
- 🚧 心力体力 OCR 识别：体力随不肝商店流程写回 `tili`；心力只定义了 `regen:xinli`，还没有采集路径
- 🔲 自动采集工作流：没有独立采集流程
- 🔲 恢复预测增强：基础推算已随 regen 模型上线，历史与报表增强未做

### Phase 3 — 货币追踪 ✅ 已实现
- ✅ 货币 OCR 识别：钱袋页面扫描工作流读宝钱、长鸣玉等
- ✅ 持久化：写入 Profile 的 stock 模型
- ✅ 资产面板展示：装备状态 Tab 的货币资产概览
- 🔲 历史趋势折线图：缺图表库，见 §6.2

### Phase 4 — 毕业率分析 ✅ 已实现
- ✅ 角色信息读取：基础属性与装备扫描工作流
- ✅ Excel 集成与计算：公式模型编译后由运行时求值
- ✅ 毕业率面板展示：装备状态 Tab 顶部

---

## 8. 风险与待确认

### 8.1 风险点

1. **OCR 准确率**：角色信息、货币数字的 OCR 准确率需要验证
2. **Excel 依赖**：引入 Excel 计算会增加外部依赖，需要考虑跨平台兼容性
3. **导航稳定性**：新增场景导航需要充分测试
4. **数据一致性**：多账号场景下数据并发写入问题（已通过 WAL + busy_timeout 缓解）

### 8.2 待确认事项

1. ~~角色信息具体包含哪些字段？~~ → 已通过 profile.yaml 的 quota/regen/stock 定义解决
2. ~~Excel 模板由用户提供还是我们设计？~~ → 已确认为用户提供：在「流派配置 → 方案管理」导入自己的 `.xlsx`
3. 毕业率计算逻辑是否涉及多维度（PVE/PVP/副本）？
4. ~~体力 +450 是什么？每日登录奖励？~~ → 已确认为每日 05:00 重置的体力恢复量
5. 是否需要系统通知提醒（Windows 通知）？

---

## 9. 相关文档

各文档的当前状态以各自首行的状态行为准，本表只登记主题：

| 文档 | 内容 |
|------|------|
| 本文档 | 玩家档案系统需求总览 |
| [01-graduation-rate.md](01-graduation-rate.md) | 毕业率分析子需求 |
| [02-currency-tracking.md](02-currency-tracking.md) | 货币追踪子需求 |
| [03-stamina-management.md](03-stamina-management.md) | 心力体力管理子需求 |
| [README.md](../README.md) | 律匠主需求文档 |
