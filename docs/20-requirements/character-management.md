# 角色管理需求说明

## 1. 背景

当前程序的用户管理仅存储用户名和创建时间，功能过于简单。通过分析 Excel 角色管理表，可将以下功能整合到程序中，实现多角色的游戏数据追踪。

---

## 2. 数据模型

### 2.1 角色核心属性

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| name | str | 角色名（主键） | 蔡元君 |
| niao_level | int | 袅袅等级 | 1-3 |
| shop_enabled | bool | 是否开启商店 | Y/空 |
| note | str | 角色备注/定位说明 | "主玩会心双刀" |

### 2.2 体力/心力追踪

| 字段 | 类型 | 说明 |
|------|------|------|
| xinli_current | int | 心力当前值 |
| xinli_max | int | 心力上限 |
| tili_current | int | 体力当前值 |
| tili_max | int | 体力上限 |
| snapshot_time | datetime | 上次记录时间 |
| snapshot_realtime | int | 记录时的实时值 |

**计算字段**：
- 回满预计时间 = 基于当前回复速率计算

### 2.3 货币系统

| 字段 | 类型 | 说明 |
|------|------|------|
| niao_niao | int | 袅袅数量 |
| baodi | int | 保底数量 |
| bu_gan | int | 不肝数量 |
| tongbao | int | 通宝数量 |
| baoqian | int | 宝钱数量 |
| changming | int | 长鸣数量 |
| bayin | int | 八音数量 |
| shengyu | int | 剩余数量 |

### 2.4 调律材料库存

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

### 2.5 玩法进度（可选）

| 字段 | 类型 | 说明 |
|------|------|------|
| xiajing | bool | 侠境 |
| zuochuan | bool | 坐船 |
| chuanxiang | bool | 船箱 |
| kouyu | bool | 鯫鱼 |
| zhige | bool | 止戈 |
| jue zhang | bool | 觉樟 |
| zuiye | bool | 罪叶 |
| huashu | bool | 话术 |

### 2.6 地区解锁状态

| 字段 | 类型 | 说明 |
|------|------|------|
| region_qinghe | bool | 清河 |
| region_kaifeng | bool | 开封 |
| region_hexi | bool | 河西 |
| region_bujianshan | bool | 不见山 |
| region_huanggong | bool | 皇宫 |
| region_qingzhou | bool | 青州 |
| region_jiangnan | bool | 江南 |

---

## 3. UI 设计

### 3.1 用户管理 Dialog 扩展

在现有用户管理 Dialog 中增加：
- 角色基本信息编辑（袅袅等级、商店、备注）
- 角色列表显示扩展信息

### 3.2 角色状态 Dialog（新建）

**入口**：主窗口菜单或按钮

**功能**：
- 显示所有角色的体力/心力状态表格
- 支持手动刷新"实时值"
- 自动计算并显示回满预计时间
- 颜色标记：低体力（红色）、即将回满（绿色）

**表格结构**：
```
| 角色名 | 袅袅 | 心力 | 体力 | 记录时间 | 实时 | 回满预计 | 备注 |
```

### 3.3 调律库存 Dialog（新建）

**入口**：主窗口菜单或按钮

**功能**：
- 显示各角色的调律材料库存汇总
- 支持手动输入或 OCR 识别后自动更新
- 显示总转律石/总变音石计算值

**表格结构**：
```
| 角色名 | 承音石 | 转律石 | 变音石 | 储备转 | 储备变 | 彩狗粮 | 定音石 | 总转 | 总变 |
```

---

## 4. 实施路径

### Phase 1：数据模型扩展
- [ ] 扩展 `User` 数据类，添加核心属性字段
- [ ] 扩展 `UserConfigManager`，支持新字段的读写
- [ ] 更新 session.json 或新建角色数据文件

### Phase 2：用户管理 Dialog 增强
- [ ] 用户列表显示袅袅等级、备注
- [ ] 新建/编辑用户时支持填写新字段

### Phase 3：角色状态 Dialog
- [ ] 新建角色状态追踪 Dialog
- [ ] 实现体力/心力表格展示
- [ ] 实现回满时间计算逻辑
- [ ] 支持手动刷新实时值

### Phase 4：调律库存 Dialog
- [ ] 新建调律库存 Dialog
- [ ] 实现材料库存表格展示
- [ ] 支持与 OCR 识别结果联动（可选）

---

## 5. 数据存储方案

### 方案 A：扩展现有 session.json

```json
{
  "active_user": "蔡元君",
  "users": [
    {
      "name": "蔡元君",
      "created_at": "2026-07-14T17:46:47",
      "niao_level": 3,
      "shop_enabled": true,
      "note": "主玩会心双刀",
      "stamina": {
        "xinli": 210,
        "tili": 1320,
        "snapshot_time": "2026-07-18T23:37:41",
        "realtime": 353
      },
      "materials": {
        "chengyin": 4,
        "zhuanlv": 17,
        "bianyin": 1,
        "snapshot_time": "2026-07-13T06:22:04"
      }
    }
  ]
}
```

### 方案 B：独立角色数据文件

每个角色一个 JSON 文件：`config/local/users/{username}/character.json`

**优点**：数据隔离，单文件不会过大
**缺点**：跨角色汇总查询较复杂

**建议**：Phase 1-2 使用方案 A，Phase 3-4 如数据量增大再考虑迁移到方案 B。

---

## 6. 相关文档

| 文档 | 内容 |
|------|------|
| [README.md](README.md) | 律匠主需求文档 |
| [game-rules.md](game-rules.md) | 游戏机制细节 |
