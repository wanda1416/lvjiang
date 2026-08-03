# 02-static-check — 跑脚本前的静态检查

引擎在 `.wf` 解析完成、执行第一条指令**之前**做两遍静态检查，不通过就直接
抛 `WorkflowUserError`，不进入执行阶段。

为什么必须前置：脚本里一个 key 拼错，运行时要等执行到那一行才暴露；而在那
之前的几十步已经把游戏点到别的页面去了，报错行号只告诉你"哪一步崩了"，不
告诉你"从哪一步开始就错了"。静态检查把这类配置错误一次性全列出来。

## 一、检查什么

| 检查 | 实现 | 依据 |
|------|------|------|
| 命名等待参数已定义 | `_validate_named_waits` | `delay_params`（配置管理 → 等待参数，app.yaml delay_params 节） |
| 引用的坐标已绑定 | `_validate_refs_bound` | 当前激活布局的 regions / points / arrows / panels |

第二项按引用**类别**分别查表，与运行时的查找路径一一对应：

| DSL 写法 | 类别 | 查布局的 |
|----------|------|----------|
| `click [scene].[key]` | click | regions，再 points（`click_any` 的顺序） |
| `drag [scene].[key]` | arrow | arrows；命中后再查方向两端的 point |
| `align [scene].[panel]`、`[scene].[panel][r][c]`、`drag [scene].[panel] up` | panel | panels |
| `scan/recognize [scene].[f1, f2]` | region | regions |

DSL 只认 key，不认场景定义里的中文名 —— `[返回]` 写成字段名而 key 是 `back`
是最常见的一类错，静态检查专治这个。

## 二、搜集范围

`scene_scan.collect_refs` 遍历：

- 顶层语句
- 所有 `def` 过程体，**包括未被 `call` 的**（宁可多报，不可漏）
- `import` 平铺进来的过程体（报错时报它自己的文件名与行号）
- 全部嵌套体：`if/else`、`for`、`loop`、`loop while`、`loop until`、`try/catch`

搜集不到的（静态无从判断，只校验到场景一级）：

- key 是变量：`click [scene].$key`、`scan [scene].$fields`
- 场景是变量：`scan $detail_scene.[...]` —— 整条引用丢弃
- 无字段的整场景识别：`scan [scene] as $x`

## 三、报错长什么样

```
静态检查未通过，脚本有 2 处引用在当前布局中找不到：
  activity_jianghu.wf:187  [activity_jianghu].[返回] — 区域/坐标点未绑定
  nav_main_to_equip.wf:12  [game_menu_page].[bag] — 区域/坐标点未绑定
请核对脚本里的 key 拼写（DSL 只认 key，不认中文名），或在场景布局编辑器中绑定
```

行号是源文件真实行号：解析前的续行预处理（`[]` `{}` `()` 内换行、行尾 `\`）
会把吐掉的换行补回成空行，保住总行数，否则多行语句之后的行号会整体偏移。

## 四、与运行时硬报错的关系

静态检查是**预检**，不是替代 —— 变量取到空值、panel 未对齐这类只有运行时
才知道的失败仍由执行层抛错（见 `32-grammar/03-1-interaction.md` 的失败语义）。
两者互补：静态检查拦配置错误，运行时硬报错拦状态错误。

静态检查抛在执行之前，**不受脚本里的 `try` / `catch` 保护**，无法被吞掉。
