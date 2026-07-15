# 工作流 DSL 语法规范（v2）

`.wf` 文件为纯文本工作流描述，存放于 `config/system/workflows/`。支持顺序执行、条件分支、循环、跳转等控制流。

## 一、基础约定

- **注释**：`#` 开头整行为注释
- **空行**：忽略
- **缩进**：自由（解析器不依赖缩进），但建议 4 空格以提升可读性
- **块闭合**：靠 `end` 关键字，不靠缩进
- **字符串**：双引号 `"..."`，内部不支持转义
- **标识符**：`[a-zA-Z_][a-zA-Z0-9_]*`
- **变量引用**：**仅 `[]` 包裹的标识符被识别为变量引用**，未包裹的标识符一律视为字面量。例如 `[slot]` 是变量，`slot` 是字面量字符串
- **内置变量**：
  - `[last_result]`：最近一次 `scan` 的结果（dict），字段 key 为 `field_key`
  - `for` 循环变量：在循环体内通过 `[var]` 引用当前迭代值

## 二、指令集

### 2.1 基础指令（v1 沿用）

| 指令 | 语法 | 说明 |
|---|---|---|
| click | `click [scene].[field]` | 点击区域（含抖动） |
| wait | `wait <delay_name>` 或 `wait <秒数>` | 命名延迟或固定秒数 |
| scan | `scan [scene]` 或 `scan [scene].[f1, f2, ...]` | OCR 整个场景或指定字段，结果写入 `[last_result]` |
| scan as | `scan [scene] as [var_name]` | 同上，同时将结果存入自定义变量 `[var_name]` |
| click_match | `click_match "文本" [error "错误信息"]` | 在 `[last_result]` 中匹配文本并点击 |
| collect | `collect` | `[last_result]` 作为工作流输出 |
| collect_as | `collect_as [key]` | `[last_result]` 存入输出字典的 `[key]` 字段 |
| log | `log "消息"` | 输出日志 |

### 2.2 控制流指令（v2 新增）

| 指令 | 语法 | 说明 |
|---|---|---|
| if | `if <cond> ... [else ...] end` | 条件分支，可嵌套 |
| for | `for <var> in [a, b, c] ... end` | 枚举循环，迭代时通过 `[var]` 提供当前值 |
| loop | `loop <N> ... end` | 计数循环，N 为正整数 |
| break | `break` | 跳出最内层 for/loop |
| label | `@label_name` | 标签，goto 的目标 |
| goto | `goto label_name` | 同文件内无条件跳转 |

## 三、条件表达式

### 3.1 基础条件

| 形式 | 说明 |
|---|---|
| `[last_result].field_key contains "文本"` | 字段值包含子串 |
| `[last_result].field_key equals "文本"` | 字段值完全相等 |
| `[last_result].field_key in ["文本1", "文本2", ...]` | 字段值等于列表中任一项（等价于多个 equals 的 or 组合） |
| `[last_result].field_key is_empty` | 字段不存在或为空字符串 |
| `not <基础条件>` | 取反任意一种 |

示例：

```
if [last_result].equip_type in ["万仞山披膊", "天罡战袍"]
    log "命中目标装备类型"
end
```

### 3.2 组合条件

- `and`：逻辑与，优先级高于 `or`
- `or`：逻辑或
- `(...)`：显式分组，覆盖默认优先级
- **短路求值**：`and` 遇 false 停、`or` 遇 true 停

示例：

```
if [last_result].affix_gong contains "会心" and not [last_result].affix_shang is_empty
    log "命中目标词条"
end

if [last_result].x is_empty or ([last_result].y equals "A" and [last_result].z contains "B")
    click [scene].[field]
end
```

### 3.3 变量引用

`for` 循环变量在循环体内通过 `[var]` 引用：

```
for slot in [slot_head, slot_chest]
    click [equip_bag_detail].[slot]   # [slot] 引用循环变量
    if [slot] equals "slot_head"      # 条件表达式里同样用 [slot]
        log "当前是头部槽位"
    end
end
```

自定义变量通过 `scan ... as [var]` 声明，后续通过 `[var]` 引用：

```
scan [equip_weapon_detail] as [weapon_data]
if [weapon_data].affix_gong contains "会心"
    log "武器命中会心"
end
```

## 四、完整示例

### 4.1 装备分析（for + if/else）

```
# 装备分析工作流

for slot in [slot_main_weapon, slot_sub_weapon,
             slot_ring, slot_pendant,
             slot_head, slot_chest, slot_leg, slot_wrist]
    click [equip_bag_detail].[slot]
    wait page_refresh_wait

    if [slot] equals "slot_main_weapon" or [slot] equals "slot_sub_weapon"
        scan [equip_weapon_detail]
    else
        scan [equip_armor_detail]
    end

    collect_as [slot]
end
```

### 4.2 调律决策树骨架（loop + if + goto + break）

```
@tune_start
click [equip_tune_detail].[one_click_add]
wait step_interval
click [equip_tune_detail].[tune_btn]
wait after_tune_wait
scan [equip_tune_result]

if [last_result].result equals "成功"
    log "调律成功"
    goto tune_done
end

if [last_result].result contains "失败"
    loop 3
        click [equip_tune_detail].[retry]
        wait step_interval
    end
    goto tune_start
end

log "未知结果，停止"
break

@tune_done
click [equip_tune_result].[close_btn]
```

## 五、保留与兼容性

- v1 的 `.wf` 文件（无控制流）在 v2 解析器下 100% 可执行
- 旧 `click_match "..." error "..."` 语法保持原样
