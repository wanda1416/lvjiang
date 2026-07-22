# DSL 内置函数

DSL 通过 `eval` 调用引擎内置函数，支持基础运算、数据清洗、装备解析、条件判定、背包遍历等能力。eval 语法详见 [01-basics.md](01-basics.md#二变量系统)。

## 一、速查表

共 27 个内置函数，按功能分为 5 类：

### 基础运算（8）

| 函数 | 签名 | 说明 |
|---|---|---|
| `add` | `(a, b) -> int` | 两数相加 |
| `sub` | `(a, b) -> int` | 两数相减 |
| `mul` | `(a, b) -> int` | 两数相乘 |
| `div` | `(a, b) -> int` | 整除，除数为 0 返回 0 |
| `mod` | `(a, b) -> int` | 取模，除数为 0 返回 0 |
| `min` | `(a, b) -> int` | 取较小值 |
| `max` | `(a, b) -> int` | 取较大值 |
| `abs` | `(a) -> int` | 取绝对值 |

### 通用工具（6）

| 函数 | 签名 | 说明 |
|---|---|---|
| `concat` | `(*args) -> str` | 拼接所有参数为字符串 |
| `range` | `(end) / (start, end) -> list` | 生成闭区间整数列表 |
| `count_key` | `(dict/list) -> int` | dict 统计非空字段数，list 统计元素数 |
| `contains` | `(dict, str) -> bool` | 检查字典中是否有任意 value 包含指定文本 |
| `find_key` | `(dict, str) -> str` | 查找 value 包含指定文本的 key，找不到返回 `""` |
| `append` | `(list, val) / (dict, key, val) -> ""` | 向列表追加或向字典写入（副作用操作） |

### 装备处理（6）

| 函数 | 签名 | 说明 |
|---|---|---|
| `to_equipment` | `(dict) -> dict` | 解析 OCR 原始数据为标准装备字典，支持链式字段访问 |
| `make_fingerprint` | `(dict) -> str` | 基于装备数据生成 MD5 去重指纹（8 位 hex） |
| `affix_cap` | `(name, level) -> float` | 查询词条数值上限 |
| `chengyin_cap` | `(name, level) -> float` | 查询承音词条数值上限（上限的 94%） |
| `is_good_equip` | `(dict) -> bool` | 判定装备是否值得保留（高价值词条 ≥ 2） |
| `evaluate` | `(dict) -> dict` | 使用流派规则评估装备，返回评级结果字典 |

### 背包遍历（3）

| 函数 | 签名 | 说明 |
|---|---|---|
| `check_scroll` | `(fingerprint) -> str` | 滚动校验，返回偏移量 `"0"` / `"1"` / `"-1"` |
| `notify_scroll` | `(col, row, fingerprint) -> ""` | 记录已处理装备指纹到滚动管理器 |
| `scroll_advance` | `() -> ""` | 校验通过后推进状态，移除已滚出的行指纹 |

### 系统交互（4）

| 函数 | 签名 | 说明 |
|---|---|---|
| `messagebox` | `(str) -> str` | 弹出 Windows 消息框，阻塞直到点击确定 |
| `save` | `() -> ""` | 强制保存 session 到磁盘 |
| `panel_rows` | `(scene, panel) -> int` | 返回 panel 实际检测到的行数 |
| `panel_cols` | `(scene, panel) -> int` | 返回 panel 实际检测到的列数 |

---

## 二、基础运算

### 算术运算符（推荐）

eval 赋值右侧支持 `+` `-` `*` `/` 四则运算符，优先级 `*` `/` > `+` `-`，支持 `()` 改变优先级：

```
eval $x = $a + $b                   # 加法
eval $x = $a - 1                    # 减法
eval $x = $a * $b / 2              # 乘除混合
eval $x = ($a + $b) * ($c - 1)     # 括号改变优先级
```

- 除法为浮点除（`10 / 3 = 3.333...`），除零返回 `0`
- if 条件比较的两侧也支持算术表达式（见 [03-control-flow.md](03-control-flow.md)）

### 运算函数

运算函数内部做 `int()` 转换，参数类型不匹配时返回 0。适用于需要整数运算或函数式风格的场景：

```
eval $counter = add($counter, 1)          # 自增
eval $remain = sub($total, $used)         # 相减
eval $double = mul($val, 2)              # 翻倍
eval $half = div($total, 2)              # 整除
eval $r = mod($index, 3)                 # 取模（判断是否行首等）
eval $clamped = min($val, 100)           # 限制上限
eval $at_least = max($val, 1)            # 限制下限
eval $diff = abs($a - $b)                # 绝对值（支持运算符）
```

> **运算符 vs 函数**：`$a + $b` 和 `add($a, $b)` 等价，但运算符版本为浮点除，函数版本为整数除。简单运算推荐用运算符，需要取模/最值/绝对值时用函数。

---

## 三、通用工具

### concat — 字符串拼接

将多个参数依次拼接为字符串，非字符串参数自动转 `str()`。

```
log concat("当前数据: ", $dict.key)
eval $msg = concat("结果: ", $var, " 完成")
```

### range — 生成整数列表

生成闭区间整数列表，常用于 `for` 循环迭代。

```
eval $list = range(1, 100)     # [1, 2, ..., 100]
for i in range(1, 5)           # 迭代 1, 2, 3, 4, 5
    ...
end
```

- `range(end)` → `[1, 2, ..., end]`
- `range(start, end)` → `[start, start+1, ..., end]`

### count_key — 统计数量

根据输入类型不同：
- **dict**：统计非空字段数量（值为空或空白字符串的字段不计）
- **list**：统计元素数量
- **其他**：返回 0

```
eval n = count_key($result)
eval n = count_key($list)
```

### contains — 文本包含检查

检查字典中是否有任意 string 类型的 value 包含指定文本。

```
if contains($scan, "调律")
    log "找到调律相关字段"
end
```

### find_key — 按键查找

在字典的 values 中查找包含目标文本的项，返回其 key 名。找不到返回空字符串 `""`，配合 `if` 判断使用。

```
scan [scene].[f1, f2, f3] as $scan
eval $key = find_key($scan, "调律")
if $key
    click [scene].$key
end
```

### append — 追加元素

向列表追加元素，或向字典写入键值对。返回空字符串（副作用操作）。

```
eval append($candidates, $equip_data)           # 列表追加
eval append($fingerprints, $slot, $fp)          # 字典写入
```

---

## 四、装备处理

### to_equipment — 装备解析

将 OCR 原始扫描结果解析为标准装备字典，支持链式字段访问。纯基于 OCR 文字分析，不依赖场景信息。

```
scan [equip_weapon_detail] as $scan_result
eval $main_weapon = to_equipment($scan_result)
collect $main_weapon
```

返回字典字段：`type`、`level`、`quality`、`base_attr`（嵌套 dict）、`affix_1`~`affix_5`（嵌套 dict）等。支持链式访问：

```
if $main_weapon.affix_1.value > 100
    log "首词条数值超过 100"
end
```

### make_fingerprint — 装备指纹

基于 type + level + quality + chengyin + 全部词条(name:value) 生成 MD5 前 8 位 hex 指纹。空数据返回空字符串。

```
eval $equip = to_equipment($scan)
eval $fp = make_fingerprint($equip)
```

### affix_cap / chengyin_cap — 词条上限查询

根据词条名和装备等级查询数值上限。`affix_cap` 返回上限值，`chengyin_cap` 返回承音值（上限的 94%）。自动映射词条名（如 "最大外功攻击" → "外功攻击"），找不到返回 0。

```
eval $equip = to_equipment($result)
eval $cap = affix_cap($equip.affix_1.name, $equip.level)
if $equip.affix_1.value > $cap
    log concat($equip.affix_1.name, " 超标")
end
```

### is_good_equip — 装备初筛

基于 OCR 扫描结果中的词条文本，检查是否包含 ≥ 2 条高价值词条（大外攻、会心、会意、三率、劲、敏、势、武学增效、首领增伤）。

```
scan [equip_weapon_detail] as $result
if is_good_equip($result)
    collect $result
end
```

### evaluate — 装备评估

使用当前流派规则（`config/system/rules/` 下第一个 .yaml）进行完整评估，返回评级结果字典。

```
eval $equip = to_equipment($scan)
eval $result = evaluate($equip)
if $result.rating equals "heirloom"
    log "传家宝！"
end
```

返回字段：`rating`（评级）、`disqualified`（是否取消资格）、`details`（详情列表）等。

---

## 五、背包遍历

用于 grid 滚动遍历场景的指纹管理三件套，配合 `check_scroll` → 处理 → `notify_scroll` → `scroll_advance` 流程使用。

### check_scroll — 滚动校验

比对 grid[1][1] 指纹与滚动管理器预期，返回偏移量：
- `"0"` — 正常
- `"1"` — 没有滚动
- `"-1"` — 滚动过头

```
eval $offset = check_scroll($fp)
```

### notify_scroll — 记录指纹

记录已处理装备的指纹。每行第一列（col=1）的指纹自动记录为行指纹。

```
eval notify_scroll($col, $row, $fp)
```

### scroll_advance — 推进状态

校验通过后调用，移除已滚出的行指纹。

```
eval scroll_advance()
```

---

## 六、系统交互

### messagebox — 消息弹窗

弹出 Windows 消息框，阻塞直到用户点击确定。可在工作流子线程中安全调用。常用于流程异常时提示用户。

```
if not $found
    eval messagebox(concat("未找到: ", $target_name))
    return
end
```

### save — 手动保存 session

通过 engine 回调触发 SessionManager.save()，将当前 session 持久化到磁盘。

```
eval save()
```

### panel_rows / panel_cols — Panel 尺寸查询

返回 panel 经网格校准后检测到的实际行数/列数。

```
eval $rows = panel_rows("bag_equip_detail", "bag_grid")
eval $cols = panel_cols("bag_equip_detail", "bag_grid")
```

---

## 七、综合示例

### 装备扫描 → 解析 → 评估 → 收集

```
scan [equip_weapon_detail] as $scan
eval $equip = to_equipment($scan)
eval $fp = make_fingerprint($equip)

# 指纹去重
if $fingerprints.$slot equals $fp
    log "重复装备，跳过"
else
    eval append($fingerprints, $slot, $fp)
end

# 评估
eval $result = evaluate($equip)
eval $total = count_key($result)
log concat("评级: ", $result.rating, " 详情数: ", $total)
```

### 字典变量聚合

```
eval $summary = {}
eval $summary.total_affixes = count_key($scan_result)
eval $summary.status = "done"
collect $summary
```
