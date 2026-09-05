# 数据通道

DSL 工作流有五条独立的数据通道，各自承担不同职责：

| 通道 | 生命周期 | 隔离性 | 用途 |
|------|----------|--------|------|
| **session** | 永久（跨执行） | 共享引用 | 角色级持久状态 |
| **context** | 单次执行内 | 共享引用 | 过程间数据传递 |
| **global 变量** | 单次执行内 | 显式声明后共享 | 跨过程共享简单值 |
| **variables** | 单次执行内 | **按 call 隔离** | 过程局部计算 |
| **output** | 单次执行内 | **按 call 隔离** | 返回给上层调度者 |

## 一、session — 持久状态

**生命周期**：跨工作流执行保留，由 `SessionManager` 管理，存储于 `users/{username}.json`。

**注入时机**：UI 层在创建引擎时从磁盘加载，注入到 `engine.session`。

**保存时机**：
- 工作流正常结束时自动保存
- 工作流中调用 `save()` 函数可强制即时保存
- 异常/中断时不保存（状态不完整）

**DSL 语法**：

```dsl
# 读取
eval $user = session.current_user
if session.equipped.$slot

# 写入
eval session.equipped = {}
eval session.equipped.$slot = to_equipment($scan_result)

# 强制保存
eval save()
```

**典型用途**：存储角色装备数据、调律记录、用户偏好等需要跨工作流保留的状态。

## 二、context — 运行时共享

**生命周期**：单次工作流执行内有效，执行结束即抛弃。

**隔离性**：所有过程共享同一 `context` 引用，子过程的修改对调用方可见。

**DSL 语法**：

```dsl
# 写入（供子过程使用）
eval context.equip_scene = "equip_detail"
eval context.bag_fingerprints = {}

# 子过程读取
eval $scene = context.equip_scene

# 子过程写入（调用方可读取）
eval context.status = "ok"
eval context.bag_fingerprints.$slot_key = $fp
```

**典型用途**：
- 主流程向子过程传递参数（如场景名、配置项）
- 子过程向主流程返回辅助信息（如状态标记、中间结果）
- 跨多个子过程共享数据（如指纹记录）

**与 `call $var = proc()` 的区别**：`context` 是共享引用传递，适合传递多个键值对；`call $var = proc()` 是局部变量绑定，语义更清晰，适合单一返回值。

## 三、global — 显式共享变量

**生命周期**：单次工作流执行内有效，执行结束即清除。

**隔离性**：名称一旦通过 `global` 声明，主流程和任意调用深度的子过程都
读写同一个值。未声明的变量仍保持局部隔离。

```dsl
global $count, $status
eval $count = 0

def increment()
    eval $count = $count + 1
    eval $status = "updated"
end

call increment()
# $count == 1，$status == "updated"
```

声明可以出现在主流程或子过程中。若声明时当前作用域已经有同名变量，当前值
会被提升并保留；声明本身不会创建默认值。简单共享状态可用 `global`，结构化
共享数据仍优先使用 `context`，过程输出仍优先使用 `return`。

## 四、variables — 局部变量

**生命周期**：单次工作流执行内有效，**按 call 隔离**。

**隔离机制**：进入子过程时保存变量快照，退出时恢复。子过程内的变量修改不影响调用方。

```python
# 引擎内部实现
saved_vars = self.variables          # 保存调用方作用域
...
self.variables = saved_vars          # 退出后恢复
```

**DSL 语法**：

```dsl
# 主流程
eval $x = "hello"
call my_proc($x)
# $x 仍然是 "hello"（子过程内的修改不影响调用方）

# 子过程
def my_proc($arg)
    eval $x = "world"    # 局部变量，不影响调用方
    eval $y = $arg + 1   # 参数也是局部变量
end
```

**参数传递**：子过程通过 `def proc($param1, $param2)` 声明参数，调用时 `call proc($arg1, $arg2)` 按位置绑定。

**返回值接收**：

```dsl
call $result = proc()           # 接收 return 值
call proc() as $output          # 接收 collect 结果
call $result = proc() as $output  # 同时接收
```

**典型用途**：过程内部的临时计算、循环计数器、中间结果暂存。

## 五、output — 返回结果

**生命周期**：单次工作流执行内有效，**按 call 隔离**。

**隔离机制**：与 `variables` 相同，进入子过程时保存并清空，退出时恢复。

**写入方式**：通过 `collect` 指令累积：

```dsl
collect 42 as "value"
collect "hello" as "msg"
collect session.equipped as "equipment"
```

**获取方式**：

| 场景 | 获取方式 |
|------|----------|
| 主工作流 | `engine.execute()` 返回值（Python 调用方） |
| 子过程调用 | `call proc() as $output` 绑定到变量 |
| 场景编辑器 | 结果区「结果集」显示 |
| 批处理 | 落盘到 `output/{role}/{script_id}_{timestamp}.json` |

**典型用途**：向调度者报告执行结果（成功/失败、统计数据、采集的数据）。

## 六、选择指南

```
需要跨工作流保留？
├── 是 → session
└── 否 → 需要跨过程传递？
         ├── 是 → 多个过程都要持续读写？
         │        ├── 是 → 简单命名状态用 global；结构化状态用 context
         │        └── 否 → call $var = proc()
         └── 否 → 需要告诉上层结果？
                  ├── 是 → output（collect）
                  └── 否 → variables（局部计算）
```

## 七、完整示例

```dsl
# 主工作流
eval session.run_count = session.run_count + 1    # 持久化计数

eval context.target_material = "玄铁"              # 传递给子过程
eval context.max_attempts = 3

call $result = find_material() as $output          # 调用子过程
# $result = return 值（如 -1 表示失败）
# $output = collect 结果（如 {"found": true, "slot": "bag_1_1"}）

if $result < 0
    log "材料查找失败"
    return -1
end

eval $slot = $output.slot                          # 使用子过程的 output
collect $slot as "material_slot"                   # 写入自己的 output
return 0
```

```dsl
# 子过程
def find_material()
    eval $material = context.target_material       # 读取 context
    eval $attempts = context.max_attempts
    
    eval context.bag_fingerprints = {}             # 写入 context（调用方可读）
    
    loop $attempts
        # ... 查找逻辑 ...
        if $found
            collect true as "found"                # 写入 output
            collect $slot as "slot"
            return 0                               # 成功返回
        end
    end
    
    collect false as "found"                       # 失败也写入 output
    return -1                                      # 异常返回
end
```

## 八、Profile — 玩家档案（独立数据源）

除上述四条通道外，DSL 还可通过内置函数访问 **ProfileDB**（玩家档案数据库），获取角色级的持久化数据。ProfileDB 按四模型组织（中文名与「用户总览」页「数据模型」对话框的分区一致）：

| 模型 | 中文名 | 语义 | 典型 key |
|------|--------|------|----------|
| **quota** | 配额 | 周期配额（跨周期重置） | `niaoniao_of_week`、`bugan_of_week` |
| **regen** | 再生 | 再生值（按时间恢复） | `tili`（体力）、`xinli`（心力） |
| **stock** | 库存 | 资源计数（只增只减） | `niaoniao`、`tongbao`、`baoqian` |
| **note** | 备注 | 轻量文本标记，不参与数值管线与同步 | 自定义备忘 key |

### regen 模型两种类型

regen 模型区分两种刷新机制，DSL 函数自动处理：

| 类型 | 语义 | 示例 | 存储方式 |
|------|------|------|----------|
| **boundary**（准点刷新） | 经过时间边界时跳变 +N | 体力每日 05:00 +450 | `{value, updated_at}` |
| **realtime**（实时刷新） | 按速率连续恢复 | 心力每分钟 +1 | `{value, anchor_time}` |

`profile_get` / `profile_inc` 对 realtime 类型的 key 会自动计算当前实时值（含未落库的累积恢复量），无需手动处理。

### DSL 函数

| 函数 | 签名 | 说明 |
|------|------|------|
| `profile_get` | `(key) -> float \| str \| null` | 读取 profile 值，自动识别模型；regen key 返回实时计算值，note 返回文本 |
| `profile_set` | `(key, value) -> float \| str` | 写入 profile 值；realtime regen 自动规范化时间锚点，note 直接写文本 |
| `profile_inc` | `(key, delta?) -> float` | 增减 profile 值（delta 默认 1），返回新值；note 不支持，仅记警告 |
| `profile_model` | `(key) -> str` | 查询 key 所属模型：`"quota"` / `"regen"` / `"stock"` / `"note"`；key 未定义返回 `""` |
| `profile_all` | `() -> dict` | 获取全部 profile 数据，regen 条目返回计算后的当前值 |

### DSL 用法

```dsl
# 读取配额剩余
eval $remain = profile_get("niaoniao_of_week")
if $remain != null
    log concat("袅袅剩余: ", $remain)
end

# 消耗体力（realtime regen，自动处理时间锚点）
eval $tili = profile_inc("tili", -900)
log concat("体力剩余: ", $tili)

# 查询模型类型
eval $model = profile_model("tili")
# $model = "regen"

# 批量获取
eval $all = profile_all()
eval $quota_data = $all.quota
eval $regen_data = $all.regen
```

### 与 session 的区别

| | session | profile |
|---|---|---|
| **数据来源** | 工作流运行时写入 | 用户手动 / UI 同步 / 引擎 tick |
| **存储位置** | `users/{username}.json` | `profile.db`（SQLite） |
| **访问方式** | `session.key` 直接访问 | `profile_get("key")` 函数调用 |
| **自动计算** | 无 | regen key 自动计算实时值 |
| **典型用途** | 工作流内部状态 | 角色级游戏数据（配额、体力、库存） |

## 九、注意事项

1. **session 写入时机**：`eval session.key = value` 只修改内存，需要显式 `save()` 或等待正常结束才落盘
2. **context 非隔离**：子过程对 `context` 的修改会立即影响调用方，注意命名冲突
3. **global 名称共享**：声明后同名变量在所有过程里指向同一状态，应避免与过程形参重名
4. **variables 隔离**：子过程无法直接修改调用方的局部变量，需要通过 `return`、`global` 或 `context` 传递
5. **output 隔离**：子过程的 `collect` 不会污染调用方的 `output`，需要通过 `as $output` 显式获取
6. **profile 是独立数据源**：profile 函数可读可写（`profile_set` / `profile_inc`），但走的是独立数据库，不经过也不影响 session/context/global/variables/output 五条通道
