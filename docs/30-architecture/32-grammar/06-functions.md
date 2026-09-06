# DSL 内置函数

DSL 通过 `eval` 调用引擎内置函数，支持基础运算、数据清洗、装备解析、条件判定、背包遍历、玩家档案访问等能力。eval 语法详见 [01-basics.md](01-basics.md#三变量)。

## 子文档

| 文件 | 内容 |
|------|------|
| [06.1-basic-functions.md](06.1-basic-functions.md) | 基础函数：算术运算、字典/列表操作、字符串处理 |
| [06.2-system-interaction.md](06.2-system-interaction.md) | 系统与交互函数：用户交互（confirm/pause/notify/input）、系统函数（save/panel）、玩家档案（profile） |
| [06.3-game-functions.md](06.3-game-functions.md) | 游戏相关函数：装备处理、背包遍历、玩家档案、综合示例 |
| [06.4-vision-functions.md](06.4-vision-functions.md) | 图色函数：取色、色占比、亮段、色心方位、同色图标、多点找色 |

---

## 速查表

共 72 个内置函数（含 yysls 插件注册的），下表按功能分为 10 类。

> 表中当前收录 63 个。以下 9 个已注册但尚未收录，待补：
> `check_env`、`yysls_rich_parse`、`to_role_base_attrs`、`open_base_attr_form`、
> `write_bag_item`、`write_equipped`、`bag_cursor_init`、`bag_cursor_visit`、
> `bag_cursor_finish_window`。
> 核对方式：`list_functions()`（需先导入 `apps.yysls.workflows.builtins`
> 各模块，否则只看得到 core 的 50 个）。

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

### 时间（2）

| 函数 | 签名 | 说明 |
|---|---|---|
| `clock` | `() -> float` | 返回 Unix 时间戳（秒精度 float），可用于计时、超时判断 |
| `datetime` | `(fmt?) -> str` / `(ts, fmt?) -> str` | 格式化时间：无参默认当前时间，首参为数值则作为时间戳，字符串则作为 strftime 格式 |

### 系统与用户交互（11）

| 函数 | 签名 | 说明 |
|---|---|---|
| `confirm` | `(str) -> bool` | 弹出确认对话框（是/否） |
| `pause` | `(str?) -> ""` | 暂停执行直到用户点击确定 |
| `notify` | `(str) -> ""` | 非阻塞通知（5 秒自动关闭） |
| `input` | `(str) -> str \| null` | 弹出输入对话框，取消返回 null |
| `save` | `() -> ""` | 强制保存 session 到磁盘 |
| `panel_rows` | `(scene, panel) -> int` | 返回 panel 实际检测到的行数 |
| `panel_cols` | `(scene, panel) -> int` | 返回 panel 实际检测到的列数 |
| `android_app_running` | `(name) -> bool` | 查询已注册安卓应用的进程是否存在 |
| `android_app_stop` | `(name, timeout=15) -> bool` | 强制停止应用并等待进程消失 |
| `android_app_start` | `(name, timeout=30) -> bool` | 启动应用并等待进程出现 |
| `android_wait_stable_frame` | `(name, timeout=60, duration=1) -> bool` | 等待期望方向下的连续稳定帧 |

### 运行环境与后端（4）

三者回答的是**不同层面**的问题，不能互相替代：

| 函数 | 签名 | 说明 |
|---|---|---|
| `env` | `() -> str` / `(str) -> bool` | **配置的工作环境**（`desktop` / `android`），由 UI 下拉框决定；回答「该按哪套导航策略走」 |
| `is_send` | `() -> bool` | 窗口模式且用 SendInput 注入（移动真实光标，需前台） |
| `is_post` | `() -> bool` | 窗口模式且用 PostMessage 注入（不移动光标，不抢焦点） |
| `is_device` | `() -> bool` | **指令实际打给设备端**（ADB / Agent / 无障碍 / Shell） |

- `is_send` / `is_post` / `is_device` **互斥**，且设备端后端一律归 `is_device`；后端未知时三者都为假。
- `env()` 与 `is_device()` 正交：桌面环境下也可能挂着 ADB 后端（PC 连手机跑），此时 `env("desktop")` 与 `is_device()` 同时为真。
- 需要「只有窗口模式才成立」的前提时用 `is_device()` 取反——按键、光标位置、前台焦点这些概念在设备端不存在。

```
if is_device()
    log info "设备端执行，跳过键盘快捷键分支"
end
```

### 图色（7）

| 函数 | 签名 | 说明 |
|---|---|---|
| `pixel` | `(ref) -> [r, g, b]` | 取坐标中心点颜色 |
| `bright` | `(ref) -> int` | 中心点亮度 r+g+b（0–765） |
| `color_ratio` | `(rect, "#rrggbb", tol) -> float` / `(rect, "#lo", "#hi") -> float` | 区域内目标色像素占比 |
| `bright_segs` | `(rect, on_min, off_max) -> int` | 沿区域中线数亮→暗跳变次数 |
| `color_vec` | `(rect, center, c_lo, c_hi, margin, channel?, min_r?, max_r?, step?) -> {deg, count} \| null` | 主导通道像素相对中心的合成方位角 |
| `find_icons` | `(rect, channel, c_min, margin1, margin2?, o_max?, min_area?, min_bbox?, c_max?) -> [FoundRegion]` | 同色连通块（可 click），按面积降序 |
| `find_multi_color` | `(rect, "#anchor", [[dx, dy, "#c"], …], tol?) -> FoundRegion \| ""` | 多点找色 |

> 坐标入参是 `$ref = [scene].[region]` 的求值结果或 find 产出；距离类参数按画布高比例。详见 [06.4-vision-functions.md](06.4-vision-functions.md)。

### 玩家档案（5）

| 函数 | 签名 | 说明 |
|---|---|---|
| `profile_get` | `(key) -> float \| null` | 读取 profile 值，自动识别模型；regen key 返回实时计算值 |
| `profile_set` | `(key, value) -> float` | 写入 profile 值；realtime regen 自动规范化时间锚点 |
| `profile_inc` | `(key, delta?) -> float` | 增减 profile 值（delta 默认 1），返回新值 |
| `profile_model` | `(key) -> str` | 查询 key 所属模型：`"quota"` / `"regen"` / `"stock"` |
| `profile_all` | `() -> dict` | 获取全部 profile 数据，regen 条目返回计算后的当前值 |
