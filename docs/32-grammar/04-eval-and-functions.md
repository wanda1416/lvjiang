# DSL 内置函数与 eval

DSL 通过 `eval` 调用引擎内置函数，支持数据清洗、条件判定等能力。

## 一、eval 语法

```
# 变量赋值
eval $var = func_name(arg1, arg2, ...)   # 调用函数，结果存入变量
eval $var = "字符串"                      # 字面量赋值
eval $var = 42                            # 数字字面量赋值（支持负数）
eval $var = {}                            # 初始化空字典
eval $var = ["a", "b", $c]               # 列表赋值（支持字符串、数字、变量引用）

# 字段赋值（统一使用 field_access 语法）
eval $var.field = "value"                # 单层字段赋值
eval $var.f1.f2.f3 = value               # 链式字段赋值（自动创建中间层）
eval $var."key" = value                  # 字符串 key 赋值
eval $var.[key] = value                  # 括号 key 赋值（等价于 "key"）
eval $var.$key = value                   # 动态 key 赋值

# 丢弃返回值
eval func_name(arg1, arg2, ...)          # 调用函数，丢弃返回值
```

- 赋值目标 `$var =` 可选，省略则丢弃返回值
- 右侧可以是函数调用、字符串字面量、数字字面量、空字典 `{}`、列表 `[...]`、变量引用 `$var` 或字段访问 `$var.field`
- 函数参数可以是 `$var`（变量引用）、`$var.field`（字段访问）或 `"literal"`（字面量字符串）
- 列表元素支持字符串字面量、数字字面量和 `$var` 变量引用，运行时求值

### 字段赋值语义

字段赋值统一使用 `field_access` 语法，支持任意深度的链式访问：

```
eval $dict.key = value              # 单层：$dict["key"] = value
eval $dict.a.b.c = value            # 链式：自动创建中间层空字典
eval $dict."key" = value            # 字符串 key（等价于 .key）
eval $dict.[key] = value            # 括号 key（等价于 .key）
eval $dict.$key = value             # 动态 key：key 名由变量值决定
```

**链式赋值自动创建中间层**：

```
eval $d = {}
eval $d.a.b.c = "deep"
# $d = {"a": {"b": {"c": "deep"}}}
```

## 二、内置函数列表

| 函数 | 签名 | 说明 |
|---|---|---|
| `to_equipment` | `(raw_data: dict) -> dict` | 解析装备 OCR 原始数据为标准装备字典，支持链式字段访问 |
| `concat` | `(*args) -> str` | 拼接所有参数为字符串，用于 `log concat("文本", $var.field)` |
| `contains` | `(scan_result: dict, text: str) -> bool` | 检查 scan 结果中是否有任意字段包含指定文本 |
| `count` | `(scan_result: dict) -> int` | 统计 scan 结果中非空字段数量 |
| `is_good_equip` | `(scan_result: dict) -> bool` | 判定装备是否值得保留（基于高价值词条） |
| `find_key` | `(dict, text: str) -> str` | 在字典中查找 value 包含指定文本的 key，找不到返回 `""` |

## 三、装备解析示例

```
# scan → eval 解析 → collect 标准三步模式
scan [equip_weapon_detail] as $scan_result
eval $main_weapon = to_equipment($scan_result)
collect $main_weapon
```

`to_equipment` 纯基于 OCR 文字分析装备类型，不依赖场景信息。
返回字典支持链式字段访问：

```
if $main_weapon.affix_1.value > 100
    log "首词条数值超过 100"
end
```

## 四、字典变量用法

通过 `eval $var = {}` 初始化空字典，再用字段赋值逐字段填充：

```
# 单层字段赋值
eval $data = {}
eval $data.name = "紫狗粮"
eval $data.count = 3
eval $data.rarity = concat("稀有度: ", $data.name)

log concat($data.name, " x", $data.count)

# 链式字段赋值（自动创建中间层）
eval $config = {}
eval $config.ui.theme = "dark"
eval $config.ui.font_size = 14
# $config = {"ui": {"theme": "dark", "font_size": 14}}

# 动态 key 赋值
eval $key = "dynamic_field"
eval $data.$key = "value"
# $data["dynamic_field"] = "value"
```

字典变量常用于聚合多步操作结果后统一输出：

```
eval $summary = {}
eval $summary.total_affixes = count($scan_result)
eval $summary.status = "done"
collect $summary
```

## 五、列表变量与 for 遍历

通过 `eval $var = [...]` 创建列表，再用 `for $x in $var` 遍历。列表元素支持字符串、数字和变量引用：

```
# 字符串列表
eval $slots = ["bag_1_1", "bag_1_2", "bag_1_3"]

# 混合变量引用
eval $x = "bag_2_1"
eval $slots = [$x, "bag_2_2", "bag_2_3"]

# 数字列表
eval $nums = [1, 2, 3]
```

**典型场景：3x6 背包全槽位批量调律**：

```
eval $all_slots = [
    "bag_1_1", "bag_1_2", "bag_1_3", "bag_1_4", "bag_1_5", "bag_1_6",
    "bag_2_1", "bag_2_2", "bag_2_3", "bag_2_4", "bag_2_5", "bag_2_6",
    "bag_3_1", "bag_3_2", "bag_3_3", "bag_3_4", "bag_3_5", "bag_3_6"
]

for $slot in $all_slots
    eval $bag_slot = $slot
    call "subcall/single_tuning.wf"
end
```

## 六、动态场景名用法

`scan` 和 `recognize` 支持动态场景名，可以先将场景名存入变量再使用：

```
# 动态场景名
eval $scene = "equip_weapon_detail"
scan $scene.[affix_1, affix_2] as $result

# 结合字段访问
eval $config = {}
eval $config.equip_scene = "equip_weapon_detail"
scan $config.equip_scene.[affix_1] as $result
```

这在需要根据条件选择不同场景时非常有用：

```
if $is_weapon
    eval $scene = "equip_weapon_detail"
else
    eval $scene = "equip_armor_detail"
end
scan $scene as $result
```
