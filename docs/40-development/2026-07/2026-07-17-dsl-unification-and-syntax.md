# Dev Log: 2026-07-17 DSL 四大指令统一与语法体系成型

> 日期：2026-07-17
> 涉及模块：`lvjiang/workflows/`（grammar.lark / parser.py / ast_nodes.py / engine.py）、`lvjiang/ui/region_editor/`、`lvjiang/core/input.py`、`lvjiang/core/capture.py`、`config/system/workflows/`、`docs/32-grammar/`、`tests/`
> 关键词：四大指令统一、数值比较运算符、grammar 子包解耦、PostMessage 后台鼠标、屏幕捕获内存泄漏、Point 属性体系、文档分层

> 说明：本篇为事后补写（原 07-17~20 无每日日志，仅 07-21 演进总结覆盖），依据当日 git 提交还原。

---

## 一、本日主线

当日 13 个提交，三条主线：**DSL 语法体系成型**（四大指令统一 + 比较运算 + grammar 子包解耦）、**输入后台化**（PostMessage 模式）、**稳定性修复**（屏幕捕获内存泄漏）。

---

## 二、DSL 语法体系成型

### 2.1 数值比较运算符与字面量赋值（`3b96b8d`，16 files +864/-131）

- 新增数值比较运算符（`>`/`<`/`>=`/`<=`/`==`/`!=`）；
- eval 支持字面量赋值、字段链式访问（`$var.field.sub`）；
- 子工作流拆分重构，配套测试覆盖。

### 2.2 recognize 字段列表与字典变量（`bd02386`，12 files +591/-50）

- `recognize` 支持 `field_list`（按字段列表识别）；
- `find` 支持变量引用；字典变量语法增强。

### 2.3 统一四大指令语法（`8ca7d67`，23 files +2296/-1146）

**背景：** click / scan / recognize / drag 四个核心指令语法不统一，参数传递方式各异。

**统一方案：**

- 所有指令统一为 `指令 目标 [参数]` 形式；
- 目标统一支持 `[scene].region_key` 或 `$var`（动态引用）；
- 参数统一支持常量、变量、字段访问；
- 测试覆盖全部指令变体。

### 2.4 single_tuning 装备预检 + return 结构化（`6446942`）

- single_tuning 工作流增加装备预检；
- 消除 return 结构化改造，输出语义更清晰。

### 2.5 语法模块解耦至 grammar 子包（`203c862`，12 files +83/-24）

**背景：** `grammar.lark`、`parser.py`、`ast_nodes.py` 散落在 `workflows/` 根目录，引擎与语法定义耦合。

```
workflows/
├── engine.py              # 引擎（通过 grammar 模块感知语法）
└── grammar/               # 语法子包（关注点分离）
    ├── grammar.lark       # EBNF 语法定义
    ├── parser.py          # Lark 解析器
    └── ast_nodes.py       # AST 节点定义
```

- `_coord_meta` 父子引擎共享，支持子工作流访问父工作流扫描坐标。

### 2.6 其余语法增强

- `85d8bd2`：goto 信号向上传播、数值比较支持裸变量、`_to_number` 失败返回 None；
- `631b049`：default 语句、loop 变量引用、范围字面量、messagebox 内置函数、参数面板 number 类型；
- `d619c5b`：DSL 隐式 eval 与 list 变量展开语法支持。

---

## 三、编辑器解耦与 Point 属性体系

### 3.1 region_editor 模块系统性解耦（`c3891f7`，9 files +1605/-1470）

按 Mixin 职责划分拆分大文件（约束：单文件 ≤400 行）：

```
dialog.py (653→275)
├── scene_ops.py (416)        # 分组/场景 CRUD + 右键菜单
├── recognition_ops.py (130)  # OCR/材料识别
└── script_ops.py (134)       # DSL 脚本测试器

scene_tab.py (600→255)
├── scene_region_panel.py (255)  # 区域列表/CRUD
└── scene_poi_panel.py (389)     # 坐标/方向列表/CRUD
```

### 3.2 Point 属性扩展与文档分层（`d619c5b`，22 files +825/-461）

- Point 增加 `type` / `is_text` / `is_clickable` 属性体系；
- 场景文档拆分为定义/编辑/实现三文件；
- `docs/32-grammar/` 术语统一为 Area / Action；
- OCR 日志格式显示输入字段列表。

---

## 四、输入后台化与稳定性

### 4.1 PostMessage 后台鼠标模式（`31ebbe8`，6 files +489/-13）

新增后台鼠标模式：光标不动，直接向目标窗口投递鼠标事件（`PostMessage`），为多开/后台运行打基础。

### 4.2 屏幕捕获内存泄漏修复（`e7ff7cd`，8 files +170/-56）

修复屏幕捕获内存泄漏与 GUI 启停/退出问题（mss 多线程 GDI 资源回收）。

### 4.3 MaterialRecognizer 类级别缓存（`f5aceba`）

参考图缓存从实例级提升为类级别，避免每次工作流运行重复加载参考图。

---

## 五、工作流参数化（`5e4123d`，12 files +273/-71）

工作流参数化改造与 UI 优化，支持运行时传参。
