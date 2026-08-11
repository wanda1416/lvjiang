# 毕业率分析 — 子需求文档

## 1. 需求概述

基于用户角色的装备信息，结合指定流派的毕业标准，通过 Excel 计算引擎得出角色的「毕业率」，展示在装备状态 Tab 顶部，帮助用户直观了解角色装备距离毕业目标的进度。

---

## 2. 核心流程

```
用户选择流派
    ↓
导航到角色详情页（nav_to_character.wf）
    ↓
OCR 读取角色信息（等级、装备评分、各部位装备详情）
    ↓
将角色数据写入 Excel 指定 cell
    ↓
Excel 公式自动计算毕业率
    ↓
读取结果 cell 的毕业率数值
    ↓
展示在装备状态 Tab 顶部（进度条 + 百分比）
```

---

## 3. 数据模型

### 3.1 角色信息（CharacterProfile）

| 字段 | 类型 | 说明 | 来源 |
|------|------|------|------|
| name | str | 角色名 | OCR |
| level | int | 角色等级 | OCR |
| equip_score | int | 装备评分总分 | OCR |
| weapon | EquipmentData | 武器详情 | OCR |
| head | EquipmentData | 头部装备 | OCR |
| body | EquipmentData | 身体装备 | OCR |
| hands | EquipmentData | 手部装备 | OCR |
| legs | EquipmentData | 腿部装备 | OCR |
| feet | EquipmentData | 脚部装备 | OCR |
| accessory_1 | EquipmentData | 饰品1 | OCR |
| accessory_2 | EquipmentData | 饰品2 | OCR |

### 3.2 毕业率结果

| 字段 | 类型 | 说明 |
|------|------|------|
| graduation_rate | float | 毕业率（0-100） |
| target_school | str | 目标流派 |
| missing_ratings | list[str] | 缺失的关键评级 |
| suggestion | str | 优化建议 |

---

## 4. Excel 集成方案

### 4.1 技术方案

- **依赖库**：`openpyxl`（纯 Python，跨平台）
- **Excel 文件位置**：`config/system/graduation/{school_name}.xlsx`
- **并发控制**：写入时加文件锁（`filelock` 库）

### 4.2 Excel 模板结构

```
Sheet: 毕业率计算

输入区（A1:D10）:
  A1: 角色名
  A2: 等级
  A3: 装备评分
  A5: 武器评级
  A6: 头部评级
  ...

计算区（隐藏列）:
  F1: 各部位权重
  F2: 流派匹配度
  ...

输出区:
  H1: 毕业率（百分比）
  H2: 缺失项
  H3: 优化建议
```

### 4.3 流派模板管理

每个流派一个 Excel 文件：
- `会心双刀.xlsx`
- `精准太刀.xlsx`
- `势重锤.xlsx`
- ...

模板由用户提供或我们设计，需包含：
- 各部位的权重配置
- 流派匹配度计算逻辑
- 毕业率公式

---

## 5. 场景定义

### 5.1 新增场景

| 场景 key | 名称 | 说明 |
|----------|------|------|
| `character_detail` | 角色详情 | 角色信息展示页面 |

### 5.2 新增区域

| 区域 key | 名称 | 类型 | 说明 |
|----------|------|------|------|
| `char_name` | 角色名 | text | OCR 识别角色名 |
| `char_level` | 角色等级 | text | OCR 识别等级 |
| `equip_score` | 装备评分 | text | OCR 识别总评分 |
| `equip_weapon` | 武器区域 | area | 武器详情区域 |
| `equip_head` | 头部区域 | area | 头部装备区域 |
| ... | ... | ... | ... |

---

## 6. UI 设计

### 6.1 毕业率面板

位置：装备状态 Tab 顶部

```
┌─────────────────────────────────────────────────────┐
│  毕业率分析                                          │
│  ┌─────────────────────────────────────────────┐   │
│  │ 流派: [会心双刀 ▼]                           │   │
│  │ ████████████████░░░░  82%                   │   │
│  │ 缺失: 武器评级未达标、饰品词条不匹配          │   │
│  └─────────────────────────────────────────────┘   │
│  [刷新] [详情]                                     │
└─────────────────────────────────────────────────────┘
```

### 6.2 交互

- **流派选择**：下拉框选择目标流派
- **刷新按钮**：手动触发 OCR 识别 + 重新计算
- **详情按钮**：弹窗显示各部位详细评分

---

## 7. DSL 工作流

### 7.1 nav_to_character.wf

```
# 从主页面导航到角色详情页
call $ok = nav_main_to_equip()
if $ok < 0
    return -1
click "more_func"
wait "page_refresh"
click "sub_func_1"  # 角色按钮
wait "page_refresh"
return 0
```

### 7.2 read_character_info.wf

```
# 读取角色信息并返回
scan $name = char_name by ocr
scan $level = char_level by ocr
scan $score = equip_score by ocr
# ... 读取各部位装备
return 0
```

---

## 8. 实施计划

### Phase 1：数据模型与存储
- [ ] 定义 `CharacterProfile` 数据类
- [ ] 实现角色信息持久化（`profile.yaml`）
- [ ] 与现有 `EquipmentData` 集成

### Phase 2：场景与导航
- [ ] 定义 `character_detail` 场景
- [ ] 编写 `nav_to_character.wf`
- [ ] 编写 `read_character_info.wf`
- [ ] 场景截图与区域标注

### Phase 3：OCR 识别
- [ ] 角色名/等级/评分 OCR
- [ ] 各部位装备 OCR（复用现有装备解析器）
- [ ] 识别准确率验证

### Phase 4：Excel 集成
- [ ] 引入 `openpyxl` 依赖
- [ ] 实现 Excel 读写模块
- [ ] 实现文件锁（并发控制）
- [ ] 流派模板管理

### Phase 5：UI 面板
- [ ] 毕业率面板 UI
- [ ] 进度条组件
- [ ] 流派选择下拉框
- [ ] 详情弹窗

---

## 9. 风险与待确认

### 9.1 风险

1. **Excel 跨平台兼容性**：macOS 上 Excel 路径不同
2. **模板维护成本**：流派规则变化需要更新 Excel
3. **OCR 准确率**：角色信息区域可能复杂

### 9.2 待确认

1. Excel 模板由谁提供？
2. 毕业率计算逻辑是否涉及多维度（PVE/PVP）？
3. 是否需要支持自定义毕业标准？
4. 是否考虑纯 Python 实现（不用 Excel）？

---

## 10. 相关文档

| 文档 | 内容 |
|------|------|
| [02-player-profile.md](../02-player-profile.md) | 玩家档案系统总览 |
| [01-auto-tuning.md](../01-auto-tuning.md) | 自动调律（装备数据模型参考） |
