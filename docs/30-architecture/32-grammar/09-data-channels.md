# 数据通道

DSL 工作流有四条独立的数据通道，各自承担不同职责：

| 通道 | 生命周期 | 隔离性 | 用途 |
|------|----------|--------|------|
| **session** | 永久（跨执行） | 共享引用 | 角色级持久状态 |
| **context** | 单次执行内 | 共享引用 | 过程间数据传递 |
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

## 三、variables — 局部变量

**生命周期**：单次工作流执行内有效，**按 call 隔离**。

**隔离机制**：进入子过程时保存变量快照，退出时恢复。子过程内的变量修改不影响调用方。

```python
# 引擎内部实现
saved_vars = dict(self.variables)    # 进入前保存
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

## 四、output — 返回结果

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

## 五、选择指南

```
需要跨工作流保留？
├── 是 → session
└── 否 → 需要跨过程传递？
         ├── 是 → 传递多个键值对？
         │        ├── 是 → context
         │        └── 否 → call $var = proc()
         └── 否 → 需要告诉上层结果？
                  ├── 是 → output（collect）
                  └── 否 → variables（局部计算）
```

## 六、完整示例

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

## 七、注意事项

1. **session 写入时机**：`eval session.key = value` 只修改内存，需要显式 `save()` 或等待正常结束才落盘
2. **context 非隔离**：子过程对 `context` 的修改会立即影响调用方，注意命名冲突
3. **variables 隔离**：子过程无法直接修改调用方的局部变量，需要通过 `return` 或 `context` 传递
4. **output 隔离**：子过程的 `collect` 不会污染调用方的 `output`，需要通过 `as $output` 显式获取
