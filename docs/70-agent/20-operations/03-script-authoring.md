# 写一个可运行的 `.wf` 脚本

面向 Agent（以及第一次写脚本的人）：从零到"能跑、能校验通过"。语法细节不在本篇
复述，第 8 节给出"要做什么 → 读哪一篇"的对照表。

> 本篇描述的**文件布局、元数据、语法、惯用法与校验入口都是现状**。
> 第 9 节（MCP 工具面）是待实现契约：当前 MCP 尚未开放脚本读写，不要按它调用。
>
> 接到"帮用户写一个脚本"的任务、需要按步骤执行而不是查资料时，先读
> [脚本开发任务书](../10-contracts/00-script-task-brief.md)。

## 1. 最短路径

1. **定通道**：这条逻辑靠文字识别、图像匹配、颜色判定还是纯坐标？（第 2 节）
2. **定位置**：写进 `config/local/workflows/<分类>/<名字>.wf`，实验脚本用 `_` 前缀。
3. **写骨架**：元数据头 + `def`/顶层语句；`if`/`for`/`loop`/`def` 一律用 `end` 收尾。
4. **校验**：`parse_file()` 过语法；`WorkflowEngine(...).validate_only(path)` 过 import 链、
   命名等待与布局引用（第 6 节）。
5. **声明可运行**：加 `runnable` / `batchable`，否则脚本不会出现在日常页。

## 2. 先选识别通道

| 要判断的东西 | 用什么 | 为什么 |
|---|---|---|
| 有稳定文字的标题、数量、按钮名、物品名 | `scan`（OCR） | 最便宜最稳；脚本里的主力通道 |
| 纯图标：返回箭头、logo、锁、动作按钮 | `scan`/`find` + `by image` | 没有文字可读，只能靠模板 |
| 图标形状会变（同类不同材料） | `recognize` + `on group` | 形状不变量；模板匹配会直接挂 |
| 数量不定、位置会动的同色图标 | 图色函数（`find_icons` 等） | 一次找出多个连通域，模板只返一个 |
| 位置固定的按钮/空白区 | 直接 `click [scene].[region]` | 不必识别，别为"有库"而加识别 |

**返回值形态是最大的坑**：`scan` / `recognize` 默认返回 **dict**（`{区域key: 文本}`，
未命中是空 dict `{}`）；写了 `by contains…` / `by image` / `by ...` 才返回 **str**
（未命中是 `""`）。想用 `if $x` 做真假判定，就用 `by` 形式。

判断不出来的别猜：先 `screenshot` 看一眼实际画面，再选通道。

## 3. 文件放在哪

```text
config/local/workflows/<分类>/<脚本名>.wf     ← Agent / 用户改这里
config/system/workflows/<分类>/<脚本名>.wf    ← 随包预置，开发模式下才可直接改
```

- **目录不表达语义**：`activity/`、`daily/`、`subcall/` 只是人看的分类，能不能被调度
  取决于元数据里的 `runnable` / `batchable`。
- **`_` 前缀的 `.wf` 会被发现层跳过**（当临时文件），实验脚本用它，正式脚本别用。
- **`config/local` 是整文件影子**：一旦有了同名 local 脚本，系统的更新不再合并进来；
  想收回系统版本就删掉 local 那份。
- 子过程库放 `subcall/`、`lib/`，由 `import "路径"` 引入；库文件不要写 `runnable`。

## 4. 最小可运行脚本

```wf
#% name: 判断是否在主页
#% runnable: true
#% batchable: false

# 是否在主界面。
#
# 前置：客户端已运行。
# 后置：无副作用（只截图识别）。
# 返回：1=在主界面；0=不在。
def is_on_main()
    scan [game_main_page].[menu, huodong, wulinlu] as $found by contains_any ["菜单", "活动", "武林录"]
    if not $found -> return 0
    return 1
end
```

三个可直接复制的骨架：

```wf
# A. OCR 判定 + 点击（最常见的组合）
scan [game_menu_page].[title, sub_title] as $title by contains "背包"

if $title
    click [game_menu_page].[bag_btn] after wait @page_refresh
end
```

```wf
# B. 模板定位后点击（模板在图库管理里截取，按名字引用）
find as $hit by image "common/back_button" where confidence >= 0.85

if $hit != ""
    click $hit
end
```

```wf
# C. 子过程复用：库文件里定义，其他脚本 import 后 call
import "subcall/login.wf"

def ensure_login($fresh)
    if not $fresh -> return 0
    call $state = exit_to_login()
    return $state
end
```

## 5. 元数据头（`#%`）

必须以 `#%` 开头、**从文件第一行开始**，遇到第一个非 `#%` 行就结束元数据。

| 字段 | 必填 | 取值与校验 |
|---|---|---|
| `id` | 否 | 缺省用文件名；必须以 Unicode 字母开头，不含 `-`，新建时不能以 `_` 开头 |
| `name` | 是 | 展示名，中文短句 |
| `note` | 否 | 说明文字，多行用 `\|-` |
| `runnable` | 可运行脚本必填 | `true` 才会出现在日常页 |
| `batchable` | 批量脚本必填 | `true` 才能进批量队列 |
| `batch_check` | 否 | 批量前置判定过程名，必须是**存在的过程** |
| `group` | 否 | 分组展示用字符串 |
| `env` | 否 | 只有声明的环境才会出现；取值必须来自 `app.yaml` 的 `envs` |
| `parameters` | 否 | 外部参数列表，见下 |

参数每项：`name` / `label` / `type` / `default` / `options` / `env`；`type` 五选一：

| type | 用途 | 备注 |
|---|---|---|
| `number` | 次数、阈值 | 默认值写数字 |
| `select` | 枚举（材料、策略） | `options` 支持 `{value, label}` 或纯标量列表 |
| `bool` | 开关 | 默认值写 `true` / `false` |
| `text` | 自由文本（含多行） | 多行用 YAML 块标量 |
| `checkgroup` | 一组勾选项 | 数字型默认值会被判为元数据错误 |

参数要联动时用 `require` 声明依赖，不要留到运行时判断。

## 6. 校验与试跑

| 手段 | 覆盖 | 入口 |
|---|---|---|
| **命令行预检（首选）** | 语法 + import 链 + 命名等待 + 布局引用，输出一行 JSON | `python -m lvjiang --exec --file <脚本> --dry-run --json`：判 `ok` 字段，别判 `code`（静态失败也是 5）；用法错误 2、环境缺失 4 |
| 脚本清单 | 现有脚本 id / name / env / batchable / source | `python -m lvjiang --list --json` |
| 语法 | 能不能解析 | `lvjiang.workflows.grammar.parse_file(path)` / `parse_text(src)` |
| 静态全量（底层） | 同上，但需自己拼 engine | `WorkflowEngine(capture=None, ocr=None, input_ctrl=None, layout=..., delay_params={}).validate_only(path)` |
| 元数据门禁 | 头字段、参数、发现层契约 | `pytest tests/workflows/test_workflow_metadata.py` |
| 脚本工作台 | 「检查」= 只解析；「保存」= 落盘后跑同一套 `validate_only` | 程序内脚本工作台 |
| 实跑 | 真实命中与副作用 | 日常页单脚本试跑；识别可信度先用场景编辑器预览 |

**只说"语法过了"不算过**：`--dry-run` 走的正是 `validate_only`，与真执行共用判据；
只跑 `parse_file` 就交付，等于把爆炸留给用户机器。**但它也只保证"语法与引用一致"**，
识别是否真命中仍需真机或场景编辑器预览——交付时把这点写清楚，别升级成"可用"。

## 7. 惯用法（照着写，评审和门禁都认这套）

```wf
# ─── is_in_bag_page ──────────────────────────────────────
# 是否在背包页（不区分 tab）。
#
# 参数：$fresh（可选，缺省 0）— 1=二次确认，直取当下画面
# 前置：无，任意页面均可调用。
# 后置：无副作用，不切换页面。
# 返回：1=在；0=不在。
def is_in_bag_page($fresh)
```

- **每个 `def` 前都写一段注释块**：包含过程清单（库文件顶部）、参数、前置、后置、返回、
  判据。这是这个仓库的既有风格，也是后续维护者（和 Agent）唯一的上下文来源。
- **纯判定过程返回 `1`/`0`**，不返回布尔字面量；用 `to_flag(...)` 归一。
- **`if not $x -> return 0`** 这种单行守卫比 `if ... end` 块更常用，等价、更短。
- **变量先 `eval` 再读**；字符串拼接用 `+`；日志用 `log info "..."`。
- **不要在一个过程里既判定又导航**：判定归 `subcall/page_detection.wf` 这类库，导航另开
  过程，出问题才定位得到。

## 8. 常见错误与改法

| 症状 | 根因 | 改法 |
|---|---|---|
| 日常页找不到脚本 | 没写 `runnable`/`batchable`，或文件名 `_` 开头 | 补元数据；改掉 `_` 前缀 |
| 元数据完全不生效 | `#%` 块不在第一行（前面有注释或空行） | 把 `#%` 移到第一行 |
| `id` 报"必须 Unicode 字母" | 数字开头、含 `-`、`_` 前缀 | 换合法 id 或干脆不写 `id` |
| 报环境不匹配 | 元数据 `env` 用了 `app.yaml` 没声明的环境 key | 用已声明的 key，或删掉 `env` |
| `batch_check` 报错 | 指向的过程不存在（没定义或没 import） | 定义过程或在同文件补 `def` |
| 语法错、`end` 相关 | 块没闭合或多余 `end` | `if`/`for`/`loop`/`def` 都要 `end` |
| `if $x` 永远为假 | 用了默认形态，拿到的是 dict（`{}` 也是假） | 改 `by ...` 形式拿字符串 |
| `validate_only` 报 Region/场景未绑定 | `[scene].[region]` 拼错，或布局里没这个绑定 | 查布局 JSON 的实际 key，别凭记忆 |
| 运行期 `find: 模板 x 不存在` | 模板名错，或该图只在另一个安装里 | 用图库管理页看到的名字（含分组前缀） |
| 换分辨率后静默不命中 | 模板没登记录制尺寸，只适用同分辨率 | 在图库管理页确认「录制画布」，没有就重截 |
| 改了 local 脚本收不到系统更新 | local 是整文件影子 | 需要系统改动就删掉 local 那份 |

## 9. 细节去哪查

| 要写的东西 | 读哪一篇（都在 [DSL 语法目录](../../40-reference/dsl/README.md)） |
|---|---|
| 词法、字面量、变量与表达式、引用模型 | `01-basics.md` |
| 场景 / 布局 / Area / Action / Panel 是什么 | `02-concepts.md` |
| 点击、拖拽、滚轮、按键、时间线 | `03.3-mouse.md`、`03.4-keyboard.md`、`03.7-timeline.md` |
| OCR 扫描、图像识别、坐标定位、模板匹配 | `04.1-scan.md`、`04.2-recognize.md`、`04.3-find.md`、`04.4-image-template.md` |
| 分支、循环、跳转、条件写法 | `05.1-loops-branches.md`、`05.2-flow-jumps.md`、`05.3-conditions.md` |
| 内置函数（算术、字符串、装备、图色…） | `06-functions.md` 及其子篇 |
| `import` / `def` / `call` 与变量隔离 | `07-subworkflows.md` |
| 元数据与外部参数全部字段 | `07.1-metadata.md` |
| 完整示例 | `08-examples.md` |

## 10. Agent 写入边界（待实现契约，当前不可调用）

下一步 MCP 开放脚本开发时，工具面按这套规则设计，避免"能改脚本"变成"能改一切"：

- **只写 `config/local/workflows/`**：不覆盖 system/remote，不动本次写入以外的路径；
- **写入即影子**：覆盖同名预置脚本前，必须向用户说明"此后不再收系统更新"；
- **必须回校验**：写盘后跑同一 `validate_only`，逐条原样返回错误，不做"看起来没问题"的汇总；
- **引用存在性**：脚本引用的场景/Region 绑定、模板名（含分组）、图库 label 分组逐项核对，
  再报成功；
- **失败不留半成品**：校验不过就撤回本次写入；
- **不代跑**：执行仍走现有任务入口与用户确认，脚本里的点击与副作用由用户承担。
