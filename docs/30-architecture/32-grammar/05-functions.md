# DSL 内置函数

DSL 通过 `eval` 调用引擎内置函数，支持基础运算、数据清洗、装备解析、条件判定、背包遍历等能力。eval 语法详见 [01-basics.md](01-basics.md#二变量系统)。

## 目录

- [一、速查表](#一速查表)
  - [基础运算（8）](#基础运算8)
  - [通用工具（6）](#通用工具6)
  - [字典/列表操作（7）](#字典列表操作7)
  - [字符串处理（8）](#字符串处理8)
  - [装备处理（6）](#装备处理6)
  - [背包遍历（3）](#背包遍历3)
  - [系统与用户交互（7）](#系统与用户交互7)
- [二、基础运算](#二基础运算)
  - [算术运算符（推荐）](#算术运算符推荐)
  - [运算函数](#运算函数)
- [三、通用工具](#三通用工具)
- [四、字典/列表与字符串函数](#四字典列表与字符串函数)
  - [字典/列表操作](#字典列表操作)
  - [字符串处理](#字符串处理)
  - [类型不匹配时的行为](#类型不匹配时的行为)
- [五、装备处理](#五装备处理)
- [六、背包遍历](#六背包遍历)
- [七、用户交互函数](#七用户交互函数)
- [八、系统函数](#八系统函数)
- [九、综合示例](#九综合示例)

---

## 一、速查表

共 45 个内置函数，按功能分为 7 类：

### 基础运算（8）

| 函数 | 签名 | 说明 |
|---|---|---|
| `add` | `(a, b) -> number` | 两数相加 |
| `sub` | `(a, b) -> number` | 两数相减 |
| `mul` | `(a, b) -> number` | 两数相乘 |
| `div` | `(a, b) -> number` | 浮点除，除数为 0 返回 0 |
| `mod` | `(a, b) -> number` | 取模，除数为 0 返回 0 |
| `min` | `(a, b, ...) -> number` | 取最小值（支持两个以上参数） |
| `max` | `(a, b, ...) -> number` | 取最大值（支持两个以上参数） |
| `abs` | `(a) -> number` | 取绝对值 |

### 通用工具（6）

| 函数 | 签名 | 说明 |
|---|---|---|
| `concat` | `(*args) -> str` | 拼接所有参数为字符串 |
| `range` | `(end) / (start, end) -> list` | 生成闭区间整数列表 |
| `count_key` | `(dict/list) -> int` | dict 统计非空字段数，list 统计元素数 |
| `contains` | `(dict, str) -> bool` | 检查字典中是否有任意 value 包含指定文本 |
| `find_key` | `(dict, str) -> str` | 查找 value 包含指定文本的 key，找不到返回 `""` |
| `append` | `(list, val) / (dict, key, val) -> ""` | 向列表追加或向字典写入（副作用操作） |

### 字典/列表操作（7）

| 函数 | 签名 | 说明 |
|---|---|---|
| `len` | `(dict\|list\|str) -> int` | 长度：dict 返回 key 数（含空值），list 返回元素数，str 返回字符数 |
| `keys` | `(dict) -> list` | 返回字典所有 key 的列表，可用于 `for k in keys($d)` |
| `values` | `(dict) -> list` | 返回字典所有 value 的列表 |
| `has_key` | `(dict, str) -> bool` | 检查字典是否包含指定 key |
| `del_key` | `(dict, str) -> ""` | 删除字典指定 key（不存在不报错），副作用 |
| `remove` | `(list, val) -> ""` | 删除列表中首个匹配元素，副作用 |
| `slice` | `(list, start, end) -> list` | 列表切片（闭区间，与 range 一致） |

### 字符串处理（8）

| 函数 | 签名 | 说明 |
|---|---|---|
| `substr` | `(str, start, end?) -> str` | 子串，start/end 为索引（闭区间），end 缺省到末尾，支持负数索引 |
| `split` | `(str, sep) -> list` | 按分隔符拆分，返回列表 |
| `replace` | `(str, old, new) -> str` | 替换所有匹配 |
| `match` | `(str, regex) -> bool` | 正则匹配（Python `re.search`），非法正则返回 False |
| `trim` | `(str) -> str` | 去除两端空白 |
| `upper` | `(str) -> str` | 转大写 |
| `lower` | `(str) -> str` | 转小写 |
| `to_num` | `(str) -> float` | 字符串转数字，失败返回 0 |

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

### 系统与用户交互（7）

| 函数 | 签名 | 说明 |
|---|---|---|
| `confirm` | `(str) -> bool` | 弹出确认对话框（是/否），详见[七、用户交互函数](#七用户交互函数) |
| `pause` | `(str?) -> ""` | 暂停执行直到用户点击确定 |
| `notify` | `(str) -> ""` | 非阻塞通知（5 秒自动关闭） |
| `input` | `(str) -> str \| null` | 弹出输入对话框，取消返回 null |
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
- if 条件比较的两侧也支持算术表达式（见 [04-control-flow.md](04-control-flow.md)）

### 运算函数

运算函数统一 number（float）语义：参数宽容转 float，返回 float，类型不匹配时返回 0。适用于需要取模/最值/绝对值或函数式风格的场景：

```
eval $counter = add($counter, 1)          # 自增
eval $remain = sub($total, $used)         # 相减
eval $double = mul($val, 2)              # 翻倍
eval $half = div($total, 2)              # 浮点除
eval $r = mod($index, 3)                 # 取模（判断是否行首等）
eval $clamped = min($val, 100)           # 限制上限
eval $lowest = min($a, $b, $c)           # 多参最小值
eval $at_least = max($val, 1)            # 限制下限
eval $diff = abs($a - $b)                # 绝对值（支持运算符）
```

> **运算符 vs 函数**：`$a + $b` 和 `add($a, $b)` 完全等价，均为 number（float）语义。简单四则运算推荐用运算符，需要取模/最值/绝对值时用函数。DSL 层暂无取整函数，需要向下取整时可用 `sub($x, mod($x, 1))` 组合实现。

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

## 四、字典/列表与字符串函数

### 字典/列表操作

```
# 统计字典 key 数
eval $n = len($config)

# 遍历字典所有 key
for k in keys($scores)
    log concat(k, ": ", $scores.$k)
end

# 检查 key 存在
eval $exists = has_key($data, "target")

# 删除 key
eval del_key($data, "obsolete")

# 列表切片
eval $part = slice($items, 0, 4)
```

> **与 count_key 的区别**：`len($dict)` 返回**全部** key 数（含空值字段），`count_key($dict)` 统计**非空**字段。两者共存，不替换。

### 字符串处理

```
# 子串
eval $name = substr($full_name, 0, 2)

# 拆分 + 遍历
for part in split($csv_line, ",")
    log part
end

# 替换
eval $clean = replace($raw, " ", "_")

# 正则匹配
eval $is_num = match($input, "^\d+$")

# 大小写
eval $tag = upper($category)

# 字符串转数字
eval $price = to_num($price_str)
```

**注意事项**：

- DSL 字符串字面量**不做转义处理**，`"\d"` 就是两个字符 `\` 和 `d`，可直接用于正则。
- `substr` 使用**闭区间** `[start, end]`，与 `slice` / `range` 一致。
- `match` 使用 `re.search`（部分匹配），不是 `re.match`（全匹配）。
- 非字符串参数会先 `str()` 转换，`null` 转为空字符串。

### 类型不匹配时的行为

字典/列表与字符串函数对类型不匹配采用宽容策略：

| 输入 | len | keys | has_key | substr | split |
|---|---|---|---|---|---|
| `dict` | key 数 | key 列表 | 正常 | str() 后截取 | str() 后拆分 |
| `list` | 元素数 | `[]` | `False` | str() 后截取 | str() 后拆分 |
| `str` | 字符数 | `[]` | `False` | 正常 | 正常 |
| `int/float` | `0` | `[]` | `False` | str() 后截取 | str() 后拆分 |
| `null` | `0` | `[]` | `False` | `""` | `[""]` |

---

## 五、装备处理

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

## 六、背包遍历

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

## 七、用户交互函数

运行中与用户交互的内置函数。`confirm`/`pause`/`input` 通过 Qt 主线程回调机制实现，可在工作流子线程中安全调用；`notify` 使用 Win32 超时 API，不阻塞工作流。

> 旧版 `messagebox` 函数已移除，等价功能请使用 `pause`。

### confirm — 确认对话框

弹出"是/否"对话框，返回 `true`（是）或 `false`（否）。

```
eval $ok = confirm("确认开始批量调律？")
if $ok
    log "用户确认，开始执行"
else
    log "用户取消"
end
```

### pause — 暂停执行

阻塞工作流直到用户点击"确定"。用于需要用户手动介入的场景：

```
try
    scan [target_scene] as $result
catch $err
    eval pause(concat("扫描失败: ", $err, "，请手动处理后点击确定"))
end
```

无参数调用使用默认消息：`eval pause()` 显示"工作流已暂停，点击确定继续"。

### notify — 非阻塞通知

弹出通知消息框，5 秒后自动关闭，工作流线程立即继续执行：

```
eval notify("第一批调律完成")
# 继续执行后续步骤
```

### input — 输入对话框

弹出文本输入框，返回用户输入的字符串。用户取消或关闭对话框返回 `null`：

```
eval $name = input("请输入角色名:")
if $name is_empty
    log "用户取消输入"
    return
end
log concat("角色名: ", $name)
```

### 线程安全

`confirm`/`pause`/`input` 通过 `engine._ui_callback` 机制实现。UI 层在创建引擎时注入回调，内部使用常驻主线程的 `QObject` 信号桥 + `threading.Event` 机制：工作流线程发信号携带请求 dict → 主线程槽显示 Qt 对话框并回填结果 → `Event.set()` 唤醒等待中的工作流线程。无竞态、无需事件循环。

按 F10 停止时，UI 层会主动关闭当前活动对话框（`confirm` 返回 `false`、`input` 返回 `null`、`pause` 立即返回），避免工作流阻塞在弹窗上无法响应停止。

`notify` 在后台守护线程中调用 Win32 `MessageBoxTimeoutW`（自带超时自动关闭），工作流线程立即返回，无需 Qt 回调。

无回调时（如测试环境），`confirm`/`pause` 回退到 Win32 MessageBoxW，`input` 返回 `null`。

### 异常处理场景示例

```
# 重试 + 用户介入 + 兜底
eval $attempt = 0
eval $max_retry = 3
eval $success = false

loop while $attempt < $max_retry
    eval $attempt = $attempt + 1
    try
        scan [target] as $result
        eval $success = true
        break
    catch $err
        eval $need_help = confirm(concat("第 ", $attempt, " 次失败: ", $err, "\n是否重试？"))
        if not $need_help
            break
        end
    end
end

if not $success
    eval pause("自动流程失败，请手动完成后点击确定")
end
```

---

## 八、系统函数

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

## 九、综合示例

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
