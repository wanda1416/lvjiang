# DSL 子工作流调用与参数声明

## 一、子工作流调用（call）

`call` 指令用于调用另一个 `.wf` 文件作为子工作流，实现逻辑复用和模块化。父子工作流之间通过 `with` 传入参数、通过 `read` 取回结果，变量完全隔离。

### 1.1 语法总览

```
# 最简调用：无参数、无返回值
call "sub.wf"

# 完整调用：传入参数 + 提取返回值
call "sub.wf" with $x as "param1", $y as "param2" read "key1" as $out1, "key2" as $out2
```

语法结构：

| 部分 | 语法 | 是否必须 | 说明 |
|---|---|---|---|
| 路径 | `"path.wf"` | 必须 | 子工作流文件路径（字符串字面量） |
| with 子句 | `with $var as "name" [, ...]` | 可选 | 向子工作流注入参数 |
| read 子句 | `read "key" as $var [, ...]` | 可选 | 从子工作流输出中提取值 |

- `with` 和 `read` 均可独立使用或同时使用
- 多个参数/返回值用逗号分隔
- `as` 两侧均支持 `$var`（变量引用）或 `"string"`（字面量），语法完全对称

### 1.2 参数传入（with）

`with` 子句将父工作流的变量值传入子工作流，子工作流内部通过参数名访问：

```
# 父工作流
eval $slot = "slot_head"
eval $mode = "quick"
call "check.wf" with $slot as "target_slot", $mode as "run_mode"
```

子工作流 `check.wf` 内部直接使用 `$target_slot` 和 `$run_mode`：

```
# check.wf
if $target_slot equals "slot_head"
    log "检查头部装备"
end
```

**传参规则**：

- `with` 后面是 `$var as "name"`，`$var` 是父级变量引用，`"name"` 是子级参数名
- 传值时机：call 执行时从父 `variables` 中取值，之后父子互不影响
- 可以传任意类型的变量值（字符串、数字、dict 等）
- 子工作流中未通过 `with` 注入的变量不可访问（隔离机制）

**动态参数名**：`as` 右侧也支持 `$var`，此时取变量的值作为参数名：

```
eval $param_key = "target_slot"
call "check.wf" with $slot as $param_key   # 等价于 with $slot as "target_slot"
```

### 1.3 获取返回值（read）

子工作流通过 `collect` 指令将数据写入自身的 `output` 字典。父工作流通过 `read` 子句从 `output` 中提取值。

**子工作流写入 output**：

```
# 子工作流内部
collect $scan_result as "equip_data"    # output["equip_data"] = $scan_result 的值
eval $status = "ok"
collect $status as "status"             # output["status"] = "ok"
```

**父工作流读取 output**：

```
call "sub.wf" read "equip_data" as $data, "status" as $status
# 执行后：$data = 子工作流的 equip_data 值，$status = "ok"
```

**read 规则**：

- `read` 后面是 `"key" as $var`，`"key"` 对应子工作流 `collect ... as "key"` 的标签名
- `as` 左侧也支持 `$var`，此时取变量的值作为 key
- 若子工作流 output 中不存在指定 key，父级 `$var` 不会被赋值（保持原值或不存在），并输出警告日志
- 若子工作流提前 `return`（未执行到 `collect`），则 output 为空，所有 read 均取不到值

### 1.4 隔离机制

每次 `call` 创建一个**独立的子 engine 实例**，拥有自己的 `variables`、`output`。父子之间变量空间完全隔离，不会互相污染。

**`_coord_meta` 全局共享**：`_coord_meta`（scan/recognize 产出的坐标元数据）在父子 engine 间共享引用，子工作流可直接访问父工作流扫描得到的 Area 坐标。

**调用流程**：

```
父 engine
  │
  ├─ 1. 从 variables 中取 $x 的值
  ├─ 2. 创建子 engine，注入 {param_name: value} 到子 variables
  ├─ 3. 子 engine 执行 sub.wf，产出 sub_output（子 engine 的 output 字典）
  ├─ 4. 从 sub_output 提取 read 的 key，写入父 variables
  └─ 5. 子 engine 销毁
```

关键点：

- 子工作流的 `scan`、`eval` 等操作只影响子 engine 的 variables
- 子工作流的 `collect` 只写入子 engine 的 output
- 父工作流只能通过 `read` 获取子工作流显式输出的数据

### 1.5 路径解析

子工作流路径基于**当前 wf 所在目录**解析，支持相对路径：

```
# 若当前 wf 在 config/system/workflows/main.wf
call "subcall/navigate.wf"   # → config/system/workflows/subcall/navigate.wf

# 若当前 wf 在 config/system/workflows/subcall/inner.wf
call "other.wf"              # → config/system/workflows/subcall/other.wf
```

项目中子工作流统一存放在 `subcall/` 子目录下。

### 1.6 契约模式：成功/失败约定

子工作流应遵循**契约模式**——通过 output 中的 `status` 字段告知调用方执行结果：

**子工作流**（`subcall/nav_equip_to_tune.wf`）：

```
# 前置：在装备详情页
# 成功：进入调律页，输出 status = "ok"
# 失败：仍在装备详情页，无输出（直接 return）

click [equip_weapon_detail].[more_func]
wait step_interval

scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4] as $tune_scan
eval $tune_key = find_key($tune_scan, "调律")
if not $tune_key
    log "未找到调律按钮"
    return                          # 失败：直接返回，不 collect
end
click [equip_weapon_detail].$tune_key
wait step_interval

eval $result = "ok"
collect $result as "status"         # 成功：写入 status
```

**父工作流**调用并检测失败：

```
call "subcall/nav_equip_to_tune.wf" read "status" as $tune_status
if not $tune_status
    log "未进入调律页面，终止流程"
    return
end
# 后续逻辑...
```

当子工作流失败时不执行 `collect`，父级 `read` 取不到 `$tune_status`（变量不存在），`if not $tune_status` 判定为 true，从而检测失败。

### 1.7 完整示例

```
# ── 父工作流：装备调律 ──

# 1. 导航到装备页（无需参数和返回值）
call "subcall/nav_main_to_equip.wf"

# 2. 点击具体装备并扫描
click [bag_equip_detail].[slot_main_weapon]
wait page_refresh_wait
scan [equip_weapon_detail] as $scan_result

# 3. 导航到调律页（带返回值检测）
call "subcall/nav_equip_to_tune.wf" read "status" as $tune_status
if not $tune_status
    log "未进入调律页面"
    return
end

# 4. 执行调律操作...
```

```
# ── 子工作流：nav_main_to_equip.wf ──
# 从主界面导航到装备培养页面

click [game_main_page].[menu]
wait step_interval

click [game_menu_page].[bag]
wait step_interval

scan [bag_equip_detail].[sub_equip] as $bag_scan
if not $bag_scan.sub_equip contains "装备"
    click [bag_equip_detail].[training]
    wait step_interval
end
```

## 二、工作流参数声明

工作流可以通过 `workflow.yaml` 声明外部参数，由 UI 参数面板提供配置界面，运行时注入到工作流变量系统中。

### 2.1 参数声明语法

在 `config/system/workflow.yaml` 的 flow 条目下新增 `parameters` 字段：

```yaml
flows:
  - id: single_tuning
    name: 单件装备调律
    wf_file: single_tuning.wf
    required_scenes: [...]
    parameters:
      - name: target_material
        label: 目标材料
        type: select
        default: "金色狗粮"
        options: ["金色狗粮", "紫色狗粮", "彩色狗粮"]
      - name: bag_slot
        label: 背包槽位
        type: select
        default: "bag_1_1"
        options:
          - { value: "bag_1_1", label: "位置 1" }
          - { value: "bag_1_2", label: "位置 2" }
```

### 2.2 参数类型

支持 `select`（下拉枚举）和 `number`（数字输入）两种类型：

| 字段 | 说明 |
|---|---|
| `name` | 参数名，即工作流中的变量名（`$name`） |
| `label` | UI 显示标签 |
| `type` | 参数类型：`select`（下拉框）或 `number`（数字输入框） |
| `default` | 默认值 |
| `options` | （仅 `select`）可选项列表，支持简单字符串或 `{value, label}` 对象 |
| `min` | （仅 `number`）最小值，默认 1 |
| `max` | （仅 `number`）最大值，默认 9999 |

**示例**：

```yaml
parameters:
  - name: execute_times
    label: 执行次数
    type: number
    default: 10
    min: 1
    max: 999
  - name: target_material
    label: 目标材料
    type: select
    default: "金色狗粮"
    options: ["金色狗粮", "紫色狗粮"]
```

### 2.3 工作流中使用

声明参数后，工作流内直接通过 `$name` 引用，无需 `eval` 赋值：

```diff
- eval $target_material = "金色狗粮"   # 硬编码
+ # $target_material 由外部参数注入，直接使用
```

配合 `default` 语句可提供未注入时的默认值：

```
default $execute_times = 10          # 未从 UI 传入时使用默认值 10
loop $execute_times
    # ... 每轮逻辑
end
```

动态 Area 引用：

```diff
- click [bag_equip_detail].[bag_1_1]   # 静态 Area
+ click [bag_equip_detail].$bag_slot  # 动态 Area，由参数注入
```

### 2.4 数据流

```
workflow.yaml (声明 parameters)
       |
       v
UI 动态面板 (workflow_combo 切换时生成对应控件)
       |
       v
_on_run_workflow() (收集面板值 → params dict)
       |
       v
BaseWorkflow.run_file(wf_path, initial_variables=params)
       |
       v
WorkflowEngine.variables = params (注入)
       |
       v
.wf 文件中 $target_material / $bag_slot 引用
```
