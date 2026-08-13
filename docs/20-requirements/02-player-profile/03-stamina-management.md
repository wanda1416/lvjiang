# 心力体力管理 — 子需求文档

## 1. 需求概述

自动读取游戏内各账号的心力与体力值，基于恢复规则预测未来状态，生成用户报表提醒即将超标的账号，帮助用户合理安排游戏时间，避免资源浪费。

---

## 2. 恢复规则

### 2.1 心力（xinli）

| 参数 | 值 | 说明 |
|------|-----|------|
| 恢复速率 | 1 点 / 8 分钟 | 每 8 分钟恢复 1 点 |
| 上限 | 600 点 | 超过不再恢复 |
| 恢复公式 | `current + (now - snapshot) / 480` | 480 秒 = 8 分钟 |

### 2.2 体力（tili）

| 参数 | 值 | 说明 |
|------|-----|------|
| 基础恢复 | 5 点 / 天 | 每日固定恢复 |
| 登录奖励 | +450 点 | 每日登录额外获得 |
| 上限 | 2500 点 | 超过不再恢复 |
| 恢复公式 | `current + 5 * days + 450 * login_days` | 需确认 +450 的具体机制 |

> **待确认**：体力 +450 是每日登录奖励还是其他机制？需要验证实际恢复规则。

---

## 3. 核心流程

```
导航到心力体力页面（nav_to_stamina.wf）
    ↓
截屏 → OCR 识别心力值、体力值
    ↓
记录当前值 + 时间戳
    ↓
追加写入 stamina.jsonl
    ↓
基于恢复规则预测当前值
    ↓
计算距离上限的时间
    ↓
生成预警报表（哪些账号即将超标）
    ↓
展示在装备状态 Tab 底部
```

---

## 4. 数据模型

### 4.1 体力快照（StaminaSnapshot）

```python
class StaminaSnapshot:
    account: str           # 账号名
    timestamp: datetime    # 采集时间
    energy: int            # 心力当前值
    energy_max: int        # 心力上限（默认 600）
    stamina: int           # 体力当前值
    stamina_max: int       # 体力上限（默认 2500）
```

### 4.2 预测结果（StaminaPrediction）

```python
class StaminaPrediction:
    account: str
    current_energy: int        # 预测当前心力
    current_stamina: int       # 预测当前体力
    energy_full_at: datetime   # 心力预计回满时间
    stamina_full_at: datetime  # 体力预计回满时间
    will_exceed_24h: bool      # 24 小时内是否超标
    warning_level: str         # 预警等级: none/warning/critical
```

---

## 5. 预测算法

### 5.1 心力预测

```python
def predict_energy(snapshot: StaminaSnapshot, now: datetime) -> int:
    """基于快照预测当前心力值"""
    elapsed_seconds = (now - snapshot.timestamp).total_seconds()
    recovered = int(elapsed_seconds / 480)  # 每 480 秒恢复 1 点
    return min(snapshot.energy + recovered, snapshot.energy_max)

def predict_energy_full(snapshot: StaminaSnapshot) -> datetime:
    """预测心力回满时间"""
    remaining = snapshot.energy_max - snapshot.energy
    seconds_needed = remaining * 480
    return snapshot.timestamp + timedelta(seconds=seconds_needed)
```

### 5.2 体力预测

```python
def predict_stamina(snapshot: StaminaSnapshot, now: datetime) -> int:
    """基于快照预测当前体力值"""
    elapsed_days = (now - snapshot.timestamp).total_seconds() / 86400
    recovered = int(elapsed_days * 5)  # 每天恢复 5 点
    # TODO: 登录奖励 +450 的计算逻辑待确认
    return min(snapshot.stamina + recovered, snapshot.stamina_max)

def predict_stamina_full(snapshot: StaminaSnapshot) -> datetime:
    """预测体力回满时间"""
    remaining = snapshot.stamina_max - snapshot.stamina
    days_needed = remaining / 5
    return snapshot.timestamp + timedelta(days=days_needed)
```

### 5.3 预警等级

| 等级 | 条件 | 颜色 |
|------|------|------|
| none | 距离回满 > 24 小时 | 绿色 |
| warning | 距离回满 12-24 小时 | 黄色 |
| critical | 距离回满 < 12 小时 | 红色 |

---

## 6. 存储方案

### 6.1 文件结构

```
config/session/users/{account}/
    stamina.jsonl
```

### 6.2 JSONL 格式

```json
{"timestamp": "2026-08-04T10:30:00", "energy": 580, "energy_max": 600, "stamina": 2400, "stamina_max": 2500}
{"timestamp": "2026-08-04T18:45:00", "energy": 350, "energy_max": 600, "stamina": 1200, "stamina_max": 2500}
```

### 6.3 数据清理

- 保留最近 30 天的数据
- 超过 30 天的记录自动删除

---

## 7. 场景定义

### 7.1 新增场景

| 场景 key | 名称 | 说明 |
|----------|------|------|
| `stamina_page` | 心力体力页面 | 心力体力展示页面 |

### 7.2 新增区域

| 区域 key | 名称 | 类型 | 说明 |
|----------|------|------|------|
| `stamina_energy` | 心力值 | text | OCR 识别心力数值 |
| `stamina_tili` | 体力值 | text | OCR 识别体力数值 |

---

## 8. UI 设计

### 8.1 心力体力面板

位置：装备状态 Tab 底部

```
┌─────────────────────────────────────────────────────┐
│  心力体力                                    [刷新] │
│  ┌───────────────────────────────────────────────┐ │
│  │ 账号A                                         │ │
│  │   心力: ████████████░░ 580/600  ⚠️ 2.7h 后满  │ │
│  │   体力: ████████████░░ 2400/2500 ⚠️ 2h 后满   │ │
│  │                                                │ │
│  │ 账号B                                         │ │
│  │   心力: ██████░░░░░░ 300/600   ✓              │ │
│  │   体力: ████████████░░ 1200/2500 ✓            │ │
│  └───────────────────────────────────────────────┘ │
│  采集时间: 2026-08-04 18:45                       │
└─────────────────────────────────────────────────────┘
```

### 8.2 交互

- **刷新按钮**：手动触发导航 + OCR 识别所有账号
- **预警标记**：⚠️ 标记即将超标的账号
- **进度条颜色**：绿色（安全）→ 黄色（警告）→ 红色（危险）

### 8.3 预警报表（可选扩展）

```
┌─────────────────────────────────────────────────────┐
│  资源预警报表                                        │
│  ┌───────────────────────────────────────────────┐ │
│  │ 🔴 账号A: 心力 580/600，预计 2.7 小时后回满    │ │
│  │ 🔴 账号A: 体力 2400/2500，预计 2 小时后回满    │ │
│  │ 🟡 账号C: 心力 550/600，预计 6.7 小时后回满    │ │
│  │ 🟢 账号B: 心力 300/600，预计 40 小时后回满     │ │
│  └───────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

---

## 9. DSL 工作流

### 9.1 nav_to_stamina.wf

```
# 从主页面导航到心力体力页面
call $ok = nav_main_to_equip()
if $ok < 0
    return -1
click "more_func"
wait "page_refresh"
# 找到心力体力按钮（需要确认具体位置）
click "sub_func_4"  # 待确认
wait "page_refresh"
return 0
```

### 9.2 read_stamina.wf

```
# 读取心力体力数值
scan $energy = stamina_energy by ocr
scan $tili = stamina_tili by ocr
return 0
```

---

## 10. 实施计划

### Phase 1：数据模型与存储
- [ ] 定义 `StaminaSnapshot` 数据类
- [ ] 定义 `StaminaPrediction` 数据类
- [ ] 实现 JSONL 追加写入
- [ ] 实现数据清理（30 天过期）

### Phase 2：预测算法
- [ ] 实现心力预测逻辑
- [ ] 实现体力预测逻辑
- [ ] 实现回满时间预测
- [ ] 实现预警等级判定
- [ ] 单元测试覆盖

### Phase 3：场景与导航
- [ ] 定义 `stamina_page` 场景
- [ ] 编写 `nav_to_stamina.wf`
- [ ] 编写 `read_stamina.wf`
- [ ] 场景截图与区域标注

### Phase 4：OCR 识别
- [ ] 心力数值 OCR
- [ ] 体力数值 OCR
- [ ] 数字清洗（去除干扰字符）
- [ ] 识别准确率验证

### Phase 5：UI 面板
- [ ] 心力体力面板 UI
- [ ] 进度条组件（带颜色）
- [ ] 预警标记
- [ ] 刷新按钮 + 加载状态

### Phase 6：多账号支持
- [ ] 批量读取所有账号
- [ ] 按账号分别展示
- [ ] 预警汇总

---

## 11. 风险与待确认

### 11.1 风险

1. **体力恢复规则不明确**：+450 的具体机制需要验证
2. **OCR 准确率**：心力体力数值区域可能复杂
3. **多账号并发**：批量读取时需要处理并发问题

### 11.2 待确认

1. 体力 +450 是什么？每日登录奖励？
2. 心力体力页面的导航路径是什么？
3. 是否需要系统通知提醒（Windows 通知）？
4. 是否需要"自动使用"功能（当即将超标时提醒用户上线消耗）？
5. 是否需要历史趋势图？

---

## 12. 相关文档

| 文档 | 内容 |
|------|------|
| [02-player-profile.md](../02-player-profile.md) | 玩家档案系统总览 |
