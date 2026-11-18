# 脚本开发任务书（交给 AI Agent）

> **这份是给"用律匠写游戏脚本"的 Agent 看的任务契约**，不是给律匠本体做开发的。
> 仓库根的 `AGENTS.md` 服务于后者（提交、发布、打包），与你的任务无关，可以跳过。
> 逐字照本篇做即可完成一轮；要查细节再进 `20-operations/03-script-authoring.md` 与
> `30-architecture/32-grammar/`。

## 1. 你要交付什么

1. 一个或多个 `.wf` 脚本文件，写在 `config/local/workflows/<分类>/<名字>.wf`；
2. 一份给人看的说明：做了什么、怎么验证的、哪几件事需要人确认（格式见第 7 节）。

## 2. 三条不可破的边界

| # | 边界 | 为什么 |
|---|---|---|
| 1 | **只写 `config/local/workflows/`** | 不碰 `system`/`remote`（那是随包内容），不碰 `config/session` 里任何用户数据 |
| 2 | **覆盖同名预置脚本前必须明确告知用户**「此后不再收系统更新」 | `local` 是整文件影子：一旦存在同名文件，系统更新不会合并进来。这是不可逆的选择 |
| 3 | **不执行脚本、不自己造识别资产** | 需要新模板、新 Region、新参考图时，报告"需要什么"，由用户在图库/场景编辑器里做；执行由用户点 |

## 3. 工作循环（每条命令都真实可跑）

```bash
# ① 看现有脚本清单（id / name / env / batchable / source）
python -m lvjiang --list --json

# ② 抄同类脚本的结构与风格（别自己发明写法）

# ③ 写完先做静态预检：不连设备、不需要游戏在跑
python -m lvjiang --exec --file config/local/workflows/xxx.wf --dry-run --json

# ④ 按返回的 ok / error 修，直到 exit 0
```

- **判 `ok` 字段，不要判 `code`**：静态检查不通过返回 `code: 5`（与运行期错误同码），
  用法错误是 `2`、环境缺失是 `4`。`--json` 的输出就是一行 JSON，可直接解析。
- `--dry-run` 覆盖：语法 + import 链 + 命名等待 + 布局引用，报错带 `文件:行号` 与原因。
- 预检前提：本机要有可用执行用户（没有则退出码 2），并按**目标布局**校验引用——
  默认 `android`，投屏/端游用 `--layout desktop`。选错布局会把正常脚本报成"引用找不到"。
- **它不验证识别会不会真命中**——那需要真机或人在场景编辑器里预览。别把预检通过说成"能用"。

## 4. 动手前先查这四类事实

| 你要知道的 | 去读 |
|---|---|
| 有哪些场景；Region 的中文名、是否文本、是否可点 | `config/system/scenes/<分组>/<场景key>.yaml` |
| 这个 Region 在目标布局里的坐标，是否绑定了模板 | `config/system/layouts/<布局>/<分组>/<场景key>.json` |
| `on group "…"` 该写哪个分组名 | `config/system/references/<空间>.yaml` |
| `by image "…"` 有哪些模板、录制尺寸多少 | `config/system/templates/templates.yaml` 与布局里的 `template` 字段 |

读文件比猜便宜：**DSL 只认 key，不认中文名**。预检会告诉你哪个 key 不存在，但那时已经
写错了——先查，一次写对。

## 5. 语法只记这些，其余查语法目录

```wf
#% name: 领帮会奖励
#% runnable: true

scan [game_menu_page].[title, sub_title] as $title by contains "帮会"
if not $title
    click [game_main_page].[menu] after wait @page_refresh
end
find as $hit by image "common/back_button" where confidence >= 0.85
if $hit != "" -> click $hit
```

八条硬规则（违反就会报错或静默失效）：

1. `#%` 元数据**必须从第一行开始**，第一个非 `#%` 行结束；`runnable`/`batchable` 决定脚本
   能不能被发现（不写就不出现在列表里）。
2. 块用 `end` 收尾：`if` / `for` / `loop` / `def` 都要。
3. **`scan`/`recognize` 默认返回 dict**（`{区域key: 文本}`，未命中 `{}`）；要拿字符串做真假
   判断，必须写 `by contains…` / `by image` 形式（未命中 `""`）。
4. 引用写 key：`[场景key].[区域key]`。
5. 变量 `$x`；赋值 `eval $x = …`；取子过程结果 `call $r = 过程名(...)`。
6. 每个 `def` 前写注释块（参数 / 前置 / 后置 / 返回）；纯判定过程返回 `1`/`0`。
7. 子过程库放在 `subcall/`、`lib/`，**不要**写 `runnable`。
8. 文件名不要以 `_` 开头（发现层当临时文件跳过）。

## 6. 参考资料（按需读，不必通读）

| 需要什么 | 读哪篇 |
|---|---|
| 元数据全字段、参数类型、骨架、错误表、惯用法 | [写一个可运行的 .wf 脚本](../20-operations/03-script-authoring.md) |
| 语法细节索引 | [DSL 语法目录](../../30-architecture/32-grammar/README.md) |
| 现网写法（42 个预置脚本） | `config/system/workflows/`：先看 `subcall/page_detection.wf`（判定库），再看 `daily/` 下的完整流程 |

## 7. 交付说明的固定格式

```markdown
## 做了什么
<一句话 + 文件路径>

## 验证结果
- `python -m lvjiang --exec --file … --dry-run --json` → `ok: true`（贴原始输出）

## 需要你确认 / 我做不到
- 识别是否真的命中（需要真机，或在场景编辑器里预览）
- 是否需要新截模板 / 新绑 Region / 新增参考图
```

**禁止表述**："已验证可用"、"测试通过"、"跑过了"——除非真的连过设备执行过。静态预检
通过只等于"语法与引用一致"。

## 8. 边界摘要（给未来的 MCP 工具面，当前未实现）

接入 MCP 后，第 4 节的读文件会变成工具调用，命名草案：
`list_scripts`、`get_script`、`validate_script`、`list_scenes`、`get_layout`、
`list_reference_groups`、`list_templates`、`get_dsl_catalog`、`write_script`。

它们受第 2 节同样的约束：写入只落 `config/local/workflows/`，不执行脚本，不改识别资产。
**当前未实现，不要假设存在**——按第 3、4 节的命令行与文件读取来做。
