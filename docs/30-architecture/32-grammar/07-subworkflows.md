# DSL 模块化：import / def / call

## 目录

- [一、概述](#一概述)
- [二、import — 引入外部 def 定义](#二import--引入外部-def-定义)
- [三、def — 定义子过程](#三def--定义子过程)
- [四、call — 执行调用](#四call--执行调用)
  - [变量隔离](#变量隔离)
  - [返回值传递](#返回值传递)
- [五、完整示例](#五完整示例)
- [六、工作流参数声明](#六工作流参数声明)
  - [6.1 参数声明语法](#61-参数声明语法)
  - [6.2 参数类型](#62-参数类型)
  - [6.3 工作流中使用](#63-工作流中使用)
  - [6.4 数据流](#64-数据流)

## 一、概述

DSL 通过三个正交指令实现代码复用和模块化：

| 指令 | 语义 | 说明 |
|---|---|---|
| `import "file.wf"` | 引入定义 | 解析目标文件的 `def` 块，注册到当前命名空间 |
| `def name($p)` ... `end` | 定义过程 | 在当前文件内定义可复用过程 |
| `call name($args)` | 执行调用 | 调用本地或导入的过程 |
| `call $var = name($args)` | 返回值绑定 | 调用过程并接收返回值 |

**命名空间**：平铺。`import "a.wf"` 后，a.wf 中的 `def foo()` 直接以 `call foo()` 调用。不同文件定义同名过程时会在解析期报冲突，不允许依赖 import 顺序隐式覆盖。

## 二、import — 引入外部 def 定义

```
import "subcall/bag_process_slot.wf"
import "subcall/navigation.wf"
```

- 路径基于**当前 wf 所在目录**解析（相对路径）
- import 仅引入目标文件中的 `def` 定义，不执行任何过程体
- 支持链式 import（A import B，B import C → A 可使用 B 和 C 的所有 def）
- 每个文件应显式 import 它直接调用过程所在的文件，不把传递导入当作本文件的隐式依赖
- **重复去重**：同一次根工作流加载中，解析到同一规范绝对路径时只加载一次；直接重复和菱形依赖均安全
- **循环检测**：解析期检测循环 import，发现后抛出错误
- **冲突检测**：不同文件的同名过程，以及根工作流与导入文件的同名过程，均直接报错并列出两个来源

```
# 循环 import 示例（会报错）
# a.wf: import "b.wf"
# b.wf: import "a.wf"
# → 循环 import 检测: a.wf -> b.wf -> a.wf
```

`load_subcalls()` 的去重范围仅限于单次调用的 import 图。下一次显式调用仍会重新解析并覆盖引擎中之前加载的同名过程，用于支持热更新；这不属于 import 冲突。

## 三、def — 定义子过程

```
def process_slot($row, $col)
    eval $slot_key = concat("r", $row, "c", $col)
    click [bag_equip_detail].[bag_grid][$row][$col]
    scan $detail_scene.[equip_type, equip_level] as $equip_scan
    # ...
end
```

- 过程定义在文件顶层（不在其他 def 体内）
- 参数列表可选：`def navigate()` 无参数也合法
- 参数以 `$` 前缀声明，调用时按位置绑定
- 过程体内可使用 `return` 退出过程，或 `return <value>` 返回值给调用方
- 过程体内可使用 `goto` 跳转到同过程内的标签

## 四、call — 执行调用

```
call process_slot(1, 1)           # 传两个参数
call nav_main_to_equip()          # 无参数
call find_tune_material($target)  # 传变量值
```

- 调用已定义的过程（本地 def 或 import 引入的 def）
- 参数支持字面量（字符串、数字）和变量引用
- 参数按位置绑定到 def 的形参

### 变量隔离

调用过程时 **save/restore caller variables**：

- 过程内的局部变量不影响调用方
- 参数作为局部变量注入，过程返回后自动恢复
- `session` / `context` / `_coord_meta` 共享引用（不隔离）

```
# 调用方
eval $x = "hello"
call my_proc($x)
# $x 仍然是 "hello"（过程内的修改不影响调用方）
# 但 context 的修改会保留
```

### 返回值绑定

使用 `call $var = proc()` 语法接收子过程的返回值：

```
def get_count()
    return 42
end

call $n = get_count()
# $n == 42
```

子过程内通过 `return <value>` 返回值，调用方通过 `$var` 接收：

```
# 被调用方
def find_material($name)
    recognize [equip_tune_detail].[material_1, material_2, material_3] as $found by contains $name
    if $found
        return $found
    end
    return null
end

# 调用方
call $slot = find_material("紫色狗粮")
if $slot
    click [equip_tune_detail].$slot
end
```

支持的返回值类型：数字、字符串、布尔值、null、变量、算术表达式、列表、**字典**。详见 [05-control-flow.md](05-control-flow.md#八return--结束工作流或返回子过程)。

**返回字典用于结构化数据传递**：子过程可通过 `return {"field": value}` 返回字典，调用方通过字段访问（`.`）提取多个结果字段。这比用多个全局变量传递更清晰、更模块化：

```
# 子过程返回字典，一次性传递多个结果
def scan_equip_info()
    scan [equip].[name_area] as $name by exact
    scan [equip].[score_area] as $score by exact
    return {"name": $name, "score": $score}
end

# 调用方通过字段访问提取各字段
call $info = scan_equip_info()
log concat("装备: ", $info.name, " 分数: ", $info.score)
```

### 异常返回值约定

子过程使用负数（推荐 `-1`）表示异常或失败返回，调用方可通过检查返回值判断是否成功：

```
def find_target($name)
    scan [scene].[areas] as $found by contains $name
    if not $found
        log concat("未找到: ", $name)
        return -1
    end
    return $found
end

call $result = find_target("目标")
if $result < 0
    log "查找失败，执行备用逻辑"
end
```

**约定**：
- `return -1`（或任意 `< 0` 的数值）：异常/失败返回
- `return 0` 或正数：正常返回（也可用于传递状态码）
- `return null` 或 bare `return`：无返回值或空结果（语义上不同于错误）

**默认返回值**：当子过程执行完毕但没有显式 `return` 值时，`call $var = proc()` 会将 `$var` 绑定为 `null`。这包括以下情况：
- 子过程没有 `return` 语句，自然执行完毕
- 子过程使用 bare `return`（不带值）退出

调用方可以通过检查返回值判断执行结果：

```
call $result = some_proc()
if $result < 0
    log "执行失败"
else if $result is null
    log "无返回值"
else
    log concat("成功: ", $result)
end
```

不绑定返回值时，`call proc()` 正常工作，返回值被丢弃：

```
call do_something()  # 不关心返回值
```

> **与旧版 context 传递的区别**：旧版通过 `context.slot = $value` 共享引用传递返回值，需要调用方额外读取 `context`。新版 `call $var = proc()` 直接绑定到局部变量，语义更清晰。旧方式仍然可用，特别是在需要传递多个返回值时。

## 五、完整示例

```
# ── 主工作流：single_tuning.wf ──

# 导入依赖
import "subcall/navigation.wf"
import "subcall/find_tune_material.wf"

# 调用导航过程
call nav_main_to_equip()

# 点击装备并扫描
click [bag_equip_detail].$equip_slot
wait @step_interval
scan [equip_weapon_detail] as $scan_result

# 导航到调律页（带返回值检测）
call $tune_status = nav_equip_to_tune()
if not $tune_status
    log "未进入调律页面"
    return
end

# 查找材料
call $slot = find_tune_material($target_material)
if $slot
    click [equip_tune_detail].$slot
end
```

```
# ── 子过程文件：subcall/navigation.wf 中的 nav_equip_to_tune ──

def nav_equip_to_tune()
    click [equip_detail].[more_func]
    wait @step_interval

    scan [equip_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4] as $tune_key by contains "调律"
    if not $tune_key
        log "未找到调律按钮"
        return false
    end

    click [equip_weapon_detail].$tune_key
    wait @step_interval
    return true
end
```

## 六、工作流参数声明

工作流可以通过 `workflows.yaml` 声明外部参数，由 UI 参数面板提供配置界面，运行时注入到工作流变量系统中。

### 6.1 参数声明语法

在 `config/system/workflows.yaml` 的 flow 条目下新增 `parameters` 字段：

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

### 6.2 参数类型

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

### 6.3 工作流中使用

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

### 6.4 数据流

```
workflows.yaml (声明 parameters)
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
