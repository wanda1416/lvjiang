# DSL 内置函数

DSL 通过 `eval` 调用引擎内置函数，支持基础运算、数据清洗、装备解析、条件判定、背包遍历、玩家档案访问等能力。eval 语法详见 [01-basics.md](01-basics.md#三变量)。

## 子文档

| 文件 | 内容 |
|------|------|
| [06-1-basic-functions.md](06-1-basic-functions.md) | 基础函数：算术运算、字典/列表操作、字符串处理 |
| [06-2-system-interaction.md](06-2-system-interaction.md) | 系统与交互函数：用户交互（confirm/pause/notify/input）、系统函数（save/panel）、玩家档案（profile） |
| [06-3-game-functions.md](06-3-game-functions.md) | 游戏相关函数：装备处理、背包遍历、玩家档案、综合示例 |

---

## 速查表

共 50 个内置函数，按功能分为 7 类：

### 基础运算（8）

| 函数 | 签名 | 说明 |
|---|---|---|
| `add` | `(a, b) -> int \| float` | 两数相加，保持类型（int+int→int，int+float→float） |
| `sub` | `(a, b) -> int \| float` | 两数相减，保持类型 |
| `mul` | `(a, b) -> int \| float` | 两数相乘，保持类型 |
| `div` | `(a, b) -> float` | 浮点除，除数为 0 返回 0.0 |
| `mod` | `(a, b) -> int \| float` | 取模，除数为 0 返回 0 |
| `min` | `(a, b, ...) -> int \| float` | 取最小值（支持两个以上参数），跳过无效值 |
| `max` | `(a, b, ...) -> int \| float` | 取最大值（支持两个以上参数），跳过无效值 |
| `abs` | `(a) -> int \| float` | 取绝对值，保持类型 |

### 字典/列表操作（12）

| 函数 | 签名 | 说明 |
|---|---|---|
| `len` | `(dict\|list\|str) -> int` | 长度：dict 返回 key 数（含空值），list 返回元素数，str 返回字符数 |
| `keys` | `(dict) -> list` | 返回字典所有 key 的列表，可用于 `for k in keys($d)` |
| `values` | `(dict) -> list` | 返回字典所有 value 的列表 |
| `has_key` | `(dict, str) -> bool` | 检查字典是否包含指定 key |
| `del_key` | `(dict, str) -> ""` | 删除字典指定 key（不存在不报错），副作用 |
| `remove` | `(list, val) -> ""` | 删除列表中首个匹配元素，副作用 |
| `slice` | `(list, start, end) -> list` | 列表切片（闭区间，与 range 一致） |
| `range` | `(end) / (start, end) -> list` | 生成闭区间整数列表 |
| `count_nonempty` | `(dict/list) -> int` | dict 统计非空字段数，list 统计元素数 |
| `contains` | `(dict, str) -> bool` | 检查字典中是否有任意 value 包含指定文本 |
| `find_key` | `(dict, str) -> str` | 查找 value 包含指定文本的 key，找不到返回 `""` |
| `append` | `(list, val) / (dict, key, val) -> ""` | 向列表追加或向字典写入（副作用操作） |

### 字符串处理（9）

| 函数 | 签名 | 说明 |
|---|---|---|
| `concat` | `(*args) -> str` | 拼接所有参数为字符串 |
| `substr` | `(str, start, end?) -> str` | 子串，start/end 为索引（闭区间），end 缺省到末尾，支持负数索引 |
| `split` | `(str, sep) -> list` | 按分隔符拆分，返回列表 |
| `replace` | `(str, old, new) -> str` | 替换所有匹配 |
| `match` | `(str, regex) -> bool` | 正则匹配（Python `re.search`），非法正则返回 False |
| `trim` | `(str) -> str` | 去除两端空白 |
| `upper` | `(str) -> str` | 转大写 |
| `lower` | `(str) -> str` | 转小写 |
| `to_num` | `(str) -> int \| float` | 字符串转数字，含小数点→float，否则→int，失败返回 0 |

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
| `confirm` | `(str) -> bool` | 弹出确认对话框（是/否） |
| `pause` | `(str?) -> ""` | 暂停执行直到用户点击确定 |
| `notify` | `(str) -> ""` | 非阻塞通知（5 秒自动关闭） |
| `input` | `(str) -> str \| null` | 弹出输入对话框，取消返回 null |
| `save` | `() -> ""` | 强制保存 session 到磁盘 |
| `panel_rows` | `(scene, panel) -> int` | 返回 panel 实际检测到的行数 |
| `panel_cols` | `(scene, panel) -> int` | 返回 panel 实际检测到的列数 |

### 玩家档案（5）

| 函数 | 签名 | 说明 |
|---|---|---|
| `profile_get` | `(key) -> float \| null` | 读取 profile 值，自动识别模型；regen key 返回实时计算值 |
| `profile_set` | `(key, value) -> float` | 写入 profile 值；realtime regen 自动规范化时间锚点 |
| `profile_inc` | `(key, delta?) -> float` | 增减 profile 值（delta 默认 1），返回新值 |
| `profile_model` | `(key) -> str` | 查询 key 所属模型：`"quota"` / `"regen"` / `"stock"` |
| `profile_all` | `() -> dict` | 获取全部 profile 数据，regen 条目返回计算后的当前值 |
