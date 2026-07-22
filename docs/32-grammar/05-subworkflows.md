# DSL 模块化：import / def / call

## 一、概述

DSL 通过三个正交指令实现代码复用和模块化：

| 指令 | 语义 | 说明 |
|---|---|---|
| `import "file.wf"` | 引入定义 | 解析目标文件的 `def` 块，注册到当前命名空间 |
| `def name($p)` ... `end` | 定义过程 | 在当前文件内定义可复用过程 |
| `call name($args)` | 执行调用 | 调用本地或导入的过程 |

**命名空间**：平铺。`import "a.wf"` 后，a.wf 中的 `def foo()` 直接以 `call foo()` 调用。名字冲突时后 import 覆盖先 import。

## 二、import — 引入外部 def 定义

```
import "subcall/bag_process_slot.wf"
import "subcall/nav.wf"
```

- 路径基于**当前 wf 所在目录**解析（相对路径）
- import 仅引入目标文件中的 `def` 定义，不执行任何过程体
- 支持链式 import（A import B，B import C → A 可使用 B 和 C 的所有 def）
- **循环检测**：解析期检测循环 import，发现后抛出错误

```
# 循环 import 示例（会报错）
# a.wf: import "b.wf"
# b.wf: import "a.wf"
# → 循环 import 检测: a.wf -> b.wf -> a.wf
```

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
- 过程体内可使用 `return` 退出过程（不退出整个工作流）
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

### 返回值传递

过程间通过 `context` 传递返回值（因为 context 是共享引用）：

```
# 被调用方
def find_material($name)
    recognize [equip_tune_detail].[material_1] as $found
    eval context.slot_name = $found
end

# 调用方
call find_material("紫色狗粮")
eval $slot = context.slot_name
if $slot
    click [equip_tune_detail].$slot
end
```

## 五、完整示例

```
# ── 主工作流：single_tuning.wf ──

# 导入依赖
import "subcall/nav_main_to_equip.wf"
import "subcall/nav_equip_to_tune.wf"
import "subcall/find_tune_material.wf"

# 调用导航过程
call nav_main_to_equip()

# 点击装备并扫描
click [bag_equip_detail].$equip_slot
wait step_interval
scan [equip_weapon_detail] as $scan_result

# 导航到调律页（带返回值检测）
call nav_equip_to_tune()
eval $tune_status = context.status
if not $tune_status
    log "未进入调律页面"
    return
end

# 查找材料
call find_tune_material($target_material)
eval $slot = context.slot_name
if $slot
    click [equip_tune_detail].$slot
end
```

```
# ── 子过程文件：subcall/nav_equip_to_tune.wf ──

def nav_equip_to_tune()
    click [equip_weapon_detail].[more_func]
    wait step_interval

    scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4] as $tune_key by contains "调律"
    if not $tune_key
        log "未找到调律按钮"
        return
    end

    click [equip_weapon_detail].$tune_key
    wait step_interval
    eval context.status = "ok"
end
```

## 六、迁移指南

### 旧语法 → 新语法

| 旧语法 | 新语法 |
|---|---|
| `call "sub.wf"` | `import "sub.wf"` + `call proc_name()` |
| `call "sub.wf" with $x as "p"` | `def proc_name($p)` + `call proc_name($x)` |
| `call "sub.wf" read "k" as $v` | `eval context.k = value` + `call proc()` + `eval $v = context.k` |

### 子工作流文件改造

将原有顶层代码包入 `def` ... `end`：

```diff
- # sub.wf（旧：顶层代码）
- click [scene].[area]
- scan [scene] as $result
- collect $result as "output"

+ # sub.wf（新：def 包裹）
+ def my_proc()
+     click [scene].[area]
+     scan [scene] as $result
+     eval context.output = $result
+ end
```

### 调用方改造

```diff
- call "subcall/sub.wf" with $x as "input" read "output" as $result

+ import "subcall/sub.wf"
+ call my_proc($x)
+ eval $result = context.output
```

## 七、工作流参数声明

工作流可以通过 `workflows.yaml` 声明外部参数，由 UI 参数面板提供配置界面，运行时注入到工作流变量系统中。

### 7.1 参数声明语法

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

### 7.2 参数类型

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

### 7.3 工作流中使用

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

### 7.4 数据流

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
