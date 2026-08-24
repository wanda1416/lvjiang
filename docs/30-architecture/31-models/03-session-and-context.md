# Session 与 Context 数据模型

本文档定义工作流引擎的两个核心数据容器：`session`（持久状态）和 `context`（运行时上下文）。

> 架构背景：采用 HTTP 类比 — `session` 类比 localStorage（跨运行持久），`context` 类比 Request Context（单次运行，结束销毁）。

---

## 一、核心概念

| | `session` | `context` |
|---|---|---|
| 生命周期 | 跨运行持久，写回磁盘 | 单次运行，结束销毁 |
| 创建方 | 运行容器从 `users/{username}.json` 加载 | 顶层工作流创建 |
| 子工作流 | 同一引用，无缝读写 | 同一引用，全程透传 |
| 持久化 | 运行结束自动写回 + `save()` 手动写回 | 不持久化 |
| DSL 关键字 | `session`（裸关键字，非 `$` 变量） | `context`（裸关键字，非 `$` 变量） |

两者在 DSL 中均为**普通 dict**，通过字段链访问，不引入方法调用语法。

---

## 二、Session 数据模型

Session 存储于 `config/session/users/{username}.json`，按用户隔离。

### 2.1 顶层结构

```json
{
  "current_user": "测试用户B",
  "current_school": "ming_hong",
  "equipped": { ... },
  "materials": { ... }
}
```

### 2.2 身份字段

| 字段 | 类型 | 说明 | 变更频率 |
|------|------|------|----------|
| `current_user` | str | 当前活跃用户名 | 极低（用户切换时） |
| `current_school` | str | 当前流派标识 | 低（手动切换时） |

`current_school` 取值：

| 值 | 流派 | 对应评估器 |
|----|------|-----------|
| `ming_hong` | 鸣金虹（会意） | `MingHongEvaluator` |
| （后续扩展） | 其他流派 | 对应评估器 |

DSL 示例：
```dsl
# 读取当前用户
log concat("当前用户: ", session.current_user)

# 读取流派
if session.current_school == "ming_hong"
    log "鸣金虹流派"
end
```

### 2.3 已穿戴装备（equipped）

8 个固定槽位，记录当前穿戴装备的摘要信息。由 `equip_analysis` 工作流扫描后写入。

```json
{
  "equipped": {
    "main_weapon": {
      "type": "横刀",
      "name": "流星断水",
      "level": 110,
      "quality": "gold",
      "affix_count": 5,
      "rating": "合格装备",
      "scanned_at": "2026-07-19T12:00:00"
    },
    "sub_weapon": { ... },
    "ring": { ... },
    "pendant": { ... },
    "head": { ... },
    "chest": { ... },
    "leg": { ... },
    "wrist": { ... }
  }
}
```

**槽位 key 固定为 8 个**（与 `bag_equip_detail` 场景中的 slot key 一致）：

| key | 部位 | 类别 |
|-----|------|------|
| `main_weapon` | 主武器 | weapon |
| `sub_weapon` | 副武器 | weapon |
| `ring` | 环 | jewelry |
| `pendant` | 佩 | jewelry |
| `head` | 冠胄 | armor |
| `chest` | 胸甲 | armor |
| `leg` | 胫甲 | armor |
| `wrist` | 腕甲 | armor |

**槽位值字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | 装备类型名（横刀/剑/枪/冠胄...） |
| `name` | str | 装备名称 |
| `level` | int | 装备等级（100/105/110） |
| `quality` | str | 品阶（gold/purple/blue/green） |
| `affix_count` | int | 已调律词条数（0~5） |
| `rating` | str | 上次评估结果（传家宝/合格装备/凑合装备/垃圾装备） |
| `scanned_at` | str | 最后扫描时间（ISO 格式） |

> 注：此处只存摘要，不存完整词条详情。完整数据由 `equip_analysis` 输出到 `users/{name}/equip_analysis_*.json` 历史文件。

DSL 示例：
```dsl
# 检查主武器词条是否已满
if session.equipped.main_weapon.affix_count >= 5
    log "主武器词条已满，无需调律"
end

# 检查某部位是否有装备
if session.equipped.ring.type
    log concat("环: ", session.equipped.ring.name)
end
```

### 2.4 调律材料（materials）

记录当前用户的调律材料库存。由工作流在识别后更新。

```json
{
  "materials": {
    "zhuanlv_stone": 17,
    "bianyin_stone": 1,
    "chengyin_stone": 4,
    "dingyin_stone": 0,
    "purple_dog_food": 50,
    "gold_dog_food": 20,
    "colorful_dog_food": 5,
    "updated_at": "2026-07-19T12:00:00"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `zhuanlv_stone` | int | 转律石数量 |
| `bianyin_stone` | int | 变音石数量 |
| `chengyin_stone` | int | 承音石数量 |
| `dingyin_stone` | int | 定音石数量 |
| `purple_dog_food` | int | 紫色狗粮数量 |
| `gold_dog_food` | int | 金色狗粮数量 |
| `colorful_dog_food` | int | 彩色狗粮数量 |
| `updated_at` | str | 最后更新时间 |

DSL 示例：
```dsl
# 调律前检查材料
if session.materials.zhuanlv_stone < 1
    log "转律石不足，停止调律"
    return
end

# 调律后扣减
eval session.materials.zhuanlv_stone = session.materials.zhuanlv_stone - 1
eval save()
```

### 2.5 未来扩展字段

以下字段当前不实现，预留设计：

```json
{
  "stamina": {
    "xinli_current": 210,
    "xinli_max": 300,
    "tili_current": 1320,
    "tili_max": 2000,
    "snapshot_time": "2026-07-19T12:00:00"
  },
  "currency": {
    "niao_niao": 0,
    "baodi": 0,
    "baoqian": 0,
    "changming": 0,
    "bayin": 0
  },
  "tuning_log": [
    {
      "date": "2026-07-19",
      "equip_type": "横刀",
      "equip_name": "流星断水",
      "result": "合格装备",
      "materials_used": 3
    }
  ]
}
```

---

## 三、Context 数据模型

Context 由顶层工作流创建，不持久化，运行结束销毁。

### 3.1 顶层结构

```python
context = {
    "bag": {},          # 背包格子映射（当前可见）
    "progress": {},     # 执行进度追踪
}
```

### 3.2 背包映射（bag）

记录当前可见的 3×6=18 个背包格子的内容。每次扫描/滑动后更新。

```python
{
    "bag": {
        "bag_1_1": {"type": "横刀", "quality": "gold", "level": 110},
        "bag_1_2": None,                           # 空格子
        "bag_1_3": {"type": "冠胄", "quality": "purple", "level": 105},
        ...
        "bag_3_6": {"type": "佩", "quality": "gold", "level": 110},
    }
}
```

格子 key 与 `bag_equip_detail` 场景定义一致（`bag_1_1` ~ `bag_3_6`）。值为 `None` 表示空格，否则为装备简要信息。

> 注：bag 数据不持久化。每次进入背包页面时重新扫描填充。

### 3.3 执行进度（progress）

追踪当前工作流的执行进度，用于日志和中断恢复。

```python
{
    "progress": {
        "phase": "scanning",          # 当前阶段（scanning/tuning/done）
        "total_candidates": 5,        # 候选装备总数
        "completed": 2,               # 已完成数
        "current_slot": "bag_2_3",    # 当前处理格子
        "materials_used": 3,          # 本次已消耗材料数
    }
}
```

---

## 四、DSL 访问规范

### 4.1 语法前提：裸 NAME 字段访问

本次改造新增语法能力：`.name`（裸 NAME）等价于 `."name"`（字符串字面量），四种字段访问写法统一：

```
$result.$var      — 动态（唯一不同，key 来自变量值）
$result."name"    — 静态（字符串字面量）
$result.[name]    — 静态（括号字面量）
$result.name      — 静态（裸 NAME）← 新增，等价于 ."name"
```

这使得 `session.current_user` 等自然写法成为可能。

### 4.2 session / context 关键字

`session` 和 `context` 为 DSL 裸关键字，不走 `$var` 变量体系。语法上通过新增的 `KeywordRef` AST 节点实现，作为字段访问链的根：

```dsl
# 读取 — 字段链访问
eval $weapon = session.equipped.main_weapon.type
eval $stones = session.materials.zhuanlv_stone
eval $item = context.bag.bag_1_1

# 写入 — eval 字段赋值
eval session.equipped.main_weapon.rating = "合格装备"
eval session.materials.zhuanlv_stone = session.materials.zhuanlv_stone - 1
eval context.bag.bag_1_1 = $scan_result
```

### 4.3 安全约束

- `session` 和 `context` 不可被整体赋值（`eval session = 123` 语法不合法）
- 只能读写其内部字段
- 引擎 `_resolve()` 遇到 `KeywordRef("session")` 或 `KeywordRef("context")` 时，返回对应 dict 引用，不走 `self.variables` 查找
- `session` 和 `context` 为保留字，不可用作用户变量名

### 4.4 手动保存

```dsl
# 关键操作后强制写回磁盘
eval save()
```

`save()` 为内置函数，将当前 session 立即写回 `users/{username}.json`。

---

## 五、存储路径

```
config/session/
├── session.json                       ← 应用级基准（active_user、用户列表、布局列表）
└── users/
    ├── 测试用户A.json                 ← session 持久化数据
    ├── 测试用户B.json
    └── ...
```

`session.json` 仅存储应用级元信息（谁在线、有哪些用户），不包含业务状态。业务状态全部在 `users/{username}.json` 中。

---

## 六、引擎集成

```python
class WorkflowEngine:
    def __init__(self, workflow, session=None):
        self._wf = workflow
        self.variables: dict = {}
        self._coord_meta: dict = {}
        self._session = session          # 容器注入（持久）
        self._context: dict | None = None  # 顶层 wf 创建（临时）

    def _resolve(self, node):
        if isinstance(node, NameRef):
            if node.name == "session":
                return self._session
            if node.name == "context":
                return self._context
            raise WorkflowUserError(f"未知关键字: {node.name}")
        ...

    def _exec_call(self, node):
        sub = WorkflowEngine(self._wf, session=self._session)
        sub._coord_meta = self._coord_meta
        sub._context = self._context       # 透传
        ...
```

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [01-equipment-models.md](01-equipment-models.md) | 装备领域模型完整定义 |
| [02-scene-implementations.md](02-scene-implementations.md) | 场景实现与区域定义 |
| [../../20-requirements/01-auto-tuning.md](../../20-requirements/01-auto-tuning.md) | 自动调律需求（session/context 的消费方） |
