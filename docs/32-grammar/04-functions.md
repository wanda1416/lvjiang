# DSL 内置函数

DSL 通过 `eval` 调用引擎内置函数，支持数据清洗、条件判定等能力。eval 语法详见 [01-basics.md](01-basics.md#二变量系统)。

## 一、内置函数列表

### 字符串处理

| 函数 | 签名 | 说明 |
|---|---|---|
| `concat` | `(*args) -> str` | 拼接所有参数为字符串，用于 `log concat("文本", $var.field)` |

### 字典处理

| 函数 | 签名 | 说明 |
|---|---|---|
| `find_key` | `(dict, str) -> str` | 在字典中查找 value 包含指定文本的 key，找不到返回 `""` |
| `contains` | `(dict, str) -> bool` | 检查字典中是否有任意 value 包含指定文本 |
| `count` | `(dict) -> int` | 统计字典中非空字段数量 |

### 装备处理

| 函数 | 签名 | 说明 |
|---|---|---|
| `to_equipment` | `(dict) -> dict` | 解析装备 OCR 原始数据为标准装备字典，支持链式字段访问 |
| `is_good_equip` | `(dict) -> bool` | 判定装备是否值得保留（基于高价值词条数量） |

### UI 交互

| 函数 | 签名 | 说明 |
|---|---|---|
| `messagebox` | `(str) -> str` | 弹出 Windows 消息框，阻塞直到用户点击确定，返回消息文本。可在工作流子线程中安全调用 |

## 二、装备解析示例

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

## 三、字典变量用法

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

## 四、列表变量与 for 遍历

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

## 五、动态场景名用法

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

## 六、范围字面量与随机等待

通过 `(min, max)` 元组字面量存储随机范围，配合 `wait $var` 实现随机等待：

```
# 直接赋值
eval $step_interval = (1, 2)
wait $step_interval              # 在 1~2 秒之间随机等待

# 配合 default 使用（支持外部覆盖）
default $step_interval = (1.5, 2.5)
wait $step_interval

# 直接内联
wait (0.5, 1.5)
```

## 七、messagebox 用法

`messagebox` 弹出 Windows 消息框，阻塞直到用户点击确定。常用于流程异常时提示用户：

```
scan [scene].[area] as $result
if not $result.area contains "目标文本"
    eval messagebox("请在初始界面开始执行")
    return
end

# 配合 concat 拼接变量
if not $found
    eval messagebox(concat("未找到: ", $target_name))
    return
end
```
