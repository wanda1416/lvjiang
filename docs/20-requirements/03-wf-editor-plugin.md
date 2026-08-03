# 工作流 DSL 编辑器插件需求文档

## 1. 概述

律匠的工作流使用自定义 DSL（领域特定语言）编写，文件扩展名为 `.wf`。该 DSL 基于 Lark 解析器，具有关键字、场景引用、字符串插值、过程定义等语法元素。

为了提升 `.wf` 文件的编写效率和正确性，需要开发编辑器插件，提供语法高亮和实时错误诊断能力。插件以 VS Code 扩展形式实现，兼容所有基于 VS Code 的编辑器（VS Code、Qoder、Cursor 等）。

---

## 2. 功能分级

插件功能按三个层级递进实现：

| 层级 | 能力 | 说明 |
|------|------|------|
| **Level 1** | 语法高亮 | TextMate grammar，纯静态正则匹配 |
| **Level 2** | 实时诊断 | Language Server，复用 Lark 解析器做语法校验 |
| **Level 3** | 语义智能 | 场景名/区域 key 存在性、过程调用校验、跳转定义、悬停提示等 |

---

## 3. DSL 语法要素

插件需要覆盖的 DSL 语法元素：

### 3.1 关键字

| 分类 | 关键字 |
|------|--------|
| 流程控制 | `main`, `proc`, `return`, `call`, `try`, `catch`, `as`, `by` |
| 动作指令 | `tap`, `wait`, `drag`, `ocr`, `find`, `screenshot` |
| 条件/循环 | `if`, `elif`, `else`, `while`, `for`, `in`, `break`, `continue` |
| 布尔/空值 | `true`, `false`, `null` |
| 逻辑运算 | `and`, `or`, `not` |
| 时序控制 | `before`, `after`, `around` |
| 匹配模式 | `equals`, `contains`, `equals_any`, `contains_any` |
| 子句关键字 | `where`, `on`, `group`, `hold`, `session`, `context` |
| 特殊变量 | `this`, `error` |

### 3.2 注释

| 类型 | 语法 | 用途 |
|------|------|------|
| 普通注释 | `# ...` | 单行注释 |
| 文档注释 | `#% ...` | 过程/模块级文档说明 |

### 3.3 字面量

| 类型 | 示例 |
|------|------|
| 字符串 | `"hello"`, `"result: {value}"`（支持 `{expr}` 插值） |
| 数字 | `42`, `3.14` |
| 布尔 | `true`, `false` |
| 空值 | `null` |

### 3.4 场景引用

- 格式：`scene_name.key_name`，如 `main_menu.btn_start`
- 需与数值字面量中的 range（`10..20`）区分

### 3.5 运算符

- 算术：`+`, `-`, `*`, `/`, `%`
- 比较：`==`, `!=`, `<`, `<=`, `>`, `>=`
- 逻辑：`and`, `or`, `not`
- 赋值：`=`
- 范围：`..`

---

## 4. Level 1：语法高亮（已实现）

### 4.1 技术方案

- **TextMate grammar**：`syntaxes/wf.tmLanguage.json`
- 正则匹配，无需后端进程
- 通过 `package.json` 的 `contributes.grammars` 注册

### 4.2 高亮规则

| scope 名称 | 匹配目标 | 对应高亮色 |
|------------|----------|-----------|
| `comment.line.number-sign` | `# ...` 注释 | 注释色 |
| `comment.line.documentation` | `#% ...` 文档注释 | 文档注释色 |
| `keyword.control.wf` | 流程控制/条件/循环关键字 | 关键字色 |
| `keyword.control.trycatch.wf` | `try`, `catch` | 关键字色 |
| `keyword.clause.wf` | `as`, `by`, `where`, `on`, `group`, `hold` | 关键字色 |
| `keyword.timing.wf` | `before`, `after`, `around` | 关键字色 |
| `keyword.match.wf` | `equals`, `contains`, `equals_any`, `contains_any` | 关键字色 |
| `keyword.special.wf` | `session`, `context` | 关键字色 |
| `keyword.operator.logical.wf` | `and`, `or`, `not` | 运算符色 |
| `constant.language.wf` | `true`, `false`, `null` | 常量色 |
| `entity.name.function.wf` | `proc` / `main` 后的过程名 | 函数名色 |
| `entity.name.scene-ref.wf` | `scene.key` 场景引用 | 特殊标识色 |
| `string.interpolated.wf` | `"..."` 字符串 | 字符串色 |
| `constant.character.escape.wf` | `{...}` 插值表达式 | 转义色 |
| `constant.numeric.wf` | 数字字面量 | 数字色 |

### 4.3 语言配置

- `language-configuration.json`：定义注释符、括号配对、自动闭合对
- 支持 `[]`, `()`, `{}` 括号配对和 `""` 引号配对

---

## 5. Level 2：实时诊断（已实现）

### 5.1 技术方案

- **Language Server Protocol (LSP)**
- 服务端：Python，基于 `pygls >= 1.3, < 2.0` 框架
- 客户端：TypeScript，基于 `vscode-languageclient`
- 复用项目已有的 `lvjiang.workflows.grammar.parse_text()` 解析器

### 5.2 架构

```
┌─────────────────────────────────┐
│  VS Code / Qoder / Cursor       │
│  ┌───────────────────────────┐  │
│  │ extension.ts (LSP Client) │  │
│  │  - 启动 Python 进程        │  │
│  │  - 自动检测 .venv          │  │
│  └─────────┬─────────────────┘  │
│            │ JSON-RPC (stdio)    │
│  ┌─────────▼─────────────────┐  │
│  │ server.py (LSP Server)    │  │
│  │  - parse_text() 语法解析   │  │
│  │  - 发布 Diagnostic         │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

### 5.3 诊断触发时机

| 事件 | 行为 |
|------|------|
| 文件打开（`didOpen`） | 立即解析并发布诊断 |
| 文件保存（`didSave`） | 重新解析并发布诊断 |
| 内容变更（`didChange`） | 每次编辑后重新解析并发布诊断 |

### 5.4 错误定位

- 利用 Lark 抛出的 `UnexpectedCharacters` / `UnexpectedToken` 异常中的 `line` 和 `column` 信息
- 将 Lark 的 1-based 行列号转换为 LSP 的 0-based `Position`
- 诊断范围：从错误位置起，延伸至 `min(col + 20, 行尾)` 字符

### 5.5 Python 环境检测

扩展按以下优先级查找 Python 解释器：

1. `lvjiangWf.pythonPath` 显式配置
2. VS Code Python 扩展的 `python.defaultInterpreterPath` 设置
3. 自动检测工作区 `.venv`（Windows: `.venv/Scripts/python.exe`，Unix: `.venv/bin/python`）
4. 回退到系统 `python`

### 5.6 三层异常兜底

| 异常类型 | 诊断严重度 | 说明 |
|----------|-----------|------|
| `UnexpectedCharacters` / `UnexpectedToken` | Error | 精确行/列定位 |
| `LarkError`（其他） | Error | 无精确位置时定位到文件首行 |
| `Exception`（兜底） | Warning | 内部解析器错误，提示用户 |

---

## 6. Level 3：语义智能

### 6.1 P0 — 高价值基础校验

这些功能的基础设施（`scene_scan.collect_refs()`、`static_check.check_refs()`、场景注册表）已在项目主体中就绪，只需在 Language Server 中接入。

| 功能 | 说明 | 依赖模块 | 状态 |
|------|------|----------|------|
| **关键字拼写模糊匹配** | 标识符与 DSL 关键字编辑距离 ≤ 2 时发布 Warning | AST NAME 节点遍历 + Levenshtein 距离 | ✅ 已实现 |
| **场景名存在性检查** | `[scene].[key]` 中的 `scene` 不存在于 `scenes.yaml` 时报错 | `SceneRegistry.all_scene_keys()` | ✅ 已实现 |
| **区域 key 检查** | `[scene].[key]` 中的 `key` 在该场景下不存在时报错 | `static_check.check_refs()` + layout | 🔲 待实现（需 layout 上下文） |
| **过程调用存在性检查** | `call proc()` 中 `proc` 未定义时报错 | AST 遍历 `ProcDef` | ✅ 已实现 |
| **import 文件检查** | `import "path.wf"` 的文件不存在时报错 | 文件系统 `Path.exists()` | ✅ 已实现 |

### 6.2 P1 — 中级语义校验

| 功能 | 说明 | 状态 |
|------|------|------|
| **变量作用域检查** | 使用未声明变量时警告（过程参数 / `for` 循环变量 / `this`） | 🔲 待实现 |
| **过程参数数量检查** | `call proc(a, b)` 的参数数量与 `proc` 定义不匹配时报错 | ✅ 已实现 |
| **重复过程名检查** | 同一文件中出现同名 `def` 定义时报错 | 🔲 待实现（解析器已处理覆盖） |

### 6.3 P2 — 编辑器体验增强

| 功能 | 说明 | 状态 |
|------|------|------|
| **代码折叠** | `def`/`if`/`loop`/`try` 块可折叠 | ✅ 已实现 |
| **文档符号大纲** | 在 Outline 面板显示所有 `def` 定义 | ✅ 已实现 |
| **跳转定义** | Ctrl+点击场景引用跳转到对应 `scenes.yaml` 或 `.wf` 过程定义 | 🔲 待实现 |
| **悬停提示** | 鼠标悬停过程名显示参数列表，悬停关键字显示提示 | ✅ 已实现 |
| **代码片段** | 输入 `def`/`if`/`while`/`try`/`click`/`scan` 等自动展开为模板 | ✅ 已实现 |

---

## 7. 扩展目录结构

```
editors/vscode/lvjiang-wf/
├── package.json                 # 扩展清单（语言注册、语法注册、配置项）
├── tsconfig.json                # TypeScript 编译配置
├── language-configuration.json  # 语言配置（注释符、括号配对）
├── syntaxes/
│   └── wf.tmLanguage.json       # TextMate 语法高亮规则
├── src/
│   └── extension.ts             # LSP 客户端入口
├── server/
│   ├── __main__.py              # LSP 服务端入口
│   └── server.py                # Language Server 核心逻辑
├── install.bat                  # 一键安装脚本（支持 vscode/qoder/cursor）
├── .vscodeignore                # 打包排除规则
└── out/                         # TypeScript 编译产物（gitignore）
```

---

## 8. 安装方式

### 8.1 开发模式安装

```batch
cd editors\vscode\lvjiang-wf
install.bat [vscode|qoder|cursor]
```

脚本自动完成：
1. `npm install` 安装 Node.js 依赖
2. `npm run compile` 编译 TypeScript
3. `mklink /J` 创建 Junction 链接到编辑器扩展目录

### 8.2 依赖要求

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| Node.js >= 18 | 编译 TypeScript、运行 LSP 客户端 | 系统安装 |
| Python >= 3.10 | 运行 Language Server | 项目 `.venv` |
| `pygls >= 1.3, < 2.0` | LSP 服务端框架 | `pyproject.toml` dev 依赖 |
| `vscode-languageclient ^8.1` | LSP 客户端库 | `package.json` 依赖 |

---

## 9. 当前实现状态

| 功能 | 状态 | 说明 |
|------|------|------|
| Level 1 语法高亮 | ✅ 已实现 | 全部关键字分组、场景引用、字符串插值、文档注释 |
| Level 2 实时诊断 | ✅ 已实现 | 语法错误实时下划线，三层异常兜底 |
| Level 2 Python 环境检测 | ✅ 已实现 | 四级 fallback 自动检测 |
| Level 2 多编辑器支持 | ✅ 已实现 | install.bat 支持 vscode/qoder/cursor |
| Level 3 关键字拼写模糊匹配 | ✅ 已实现 | 编辑距离 ≤ 2 时发布 Warning 提示 |
| Level 3 场景名存在性检查 | ✅ 已实现 | 接入 SceneRegistry，检查场景是否存在 |
| Level 3 过程调用存在性检查 | ✅ 已实现 | AST 遍历 CallProc，检查过程是否定义 |
| Level 3 import 文件检查 | ✅ 已实现 | 文件系统 Path.exists() 检查 |
| Level 3 过程参数数量检查 | ✅ 已实现 | 比对 CallProc.args 与 ProcDef.params |
| Level 3 代码折叠 | ✅ 已实现 | def/if/loop/try 块可折叠 |
| Level 3 文档符号大纲 | ✅ 已实现 | Outline 面板显示所有 def 定义 |
| Level 3 悬停提示 | ✅ 已实现 | 悬停过程名显示参数列表 |
| Level 3 代码片段 | ✅ 已实现 | def/if/while/try/click/scan 等模板 |
| Level 3 区域 key 检查 | 🔲 待实现 | 需 layout 上下文 |
| Level 3 跳转定义 | 🔲 待实现 | 需实现 textDocument/definition |
| Level 3 变量作用域检查 | 🔲 待实现 | 需符号表与嵌套作用域分析 |
