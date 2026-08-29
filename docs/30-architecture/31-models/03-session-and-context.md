# Session 与 Context 数据模型

本文档定义工作流引擎的两个核心数据容器：`session`（持久状态）和 `context`（运行时上下文）。

> 架构背景：采用 HTTP 类比 — `session` 类比 localStorage（跨运行持久），`context` 类比 Request Context（单次运行，结束销毁）。

---

## 一、核心概念

| | `session` | `context` |
|---|---|---|
| 生命周期 | 跨运行持久，写回磁盘 | 单次运行，结束销毁 |
| 创建方 | UI 层从 `SessionManager` 加载后注入 `engine.session` | 引擎构造时自动初始化为空 dict |
| 子过程调用 | 同一引擎实例的同一个 dict，本就是同一份引用 | 同上 |
| 持久化 | 运行结束自动写回 + `save()` 手动写回 | 不持久化 |
| DSL 关键字 | `session`（裸关键字，非 `$` 变量） | `context`（裸关键字，非 `$` 变量） |

两者在 DSL 中均为**普通 dict**，通过字段链访问，不引入方法调用语法。

`call proc_name(...)` 不会像早期设计设想的那样创建一个新的子引擎实例——
`session`/`context` 也就不存在"透传给子引擎"这个动作。`_exec_call_proc` /
`_run_proc` 在**同一个** `WorkflowEngine` 实例内保存并恢复
`self.variables`/`self.output`（作用域隔离），`self.session`/`self.context`
/`self._coord_meta` 全程共享同一对象，细节见第六节。

---

## 二、Session：机制已就位，尚无业务 schema

`session` 由 UI 层经 `SessionManager.load(username)` 从
`config/session/users/{username}.json` 读入，赋给 `engine.session`
（见第五节）。未初始化的用户只有一个默认字段：

```json
{"current_user": "测试用户B"}
```

**目前没有任何系统 `.wf` 脚本读写 `session.*` 字段**——`config/system/workflows/`
下全文搜索 `session\.` 没有命中。DSL 层面的裸关键字机制（`KeywordRef`、
第四节的字段访问语法）是真实可用的，但早期设计设想的具体业务 schema
（装备槽位摘要、调律材料库存、流派标识等）从未在这份文件上落地，也没有
被后续实现沿用。

真正承载这类持久业务数据的是另外两套机制，写脚本时应该用它们，而不是
`session` 关键字：

| 数据类型 | 现在存哪 | 读写方式 |
|---------|---------|---------|
| 调律材料库存、承音/定音石数量、装备评级历史等玩家数据 | `config/session/profile.db`（SQLite） | `profile_action()` / `profile_read()` 内置函数，见 [02-player-profile.md](../../20-requirements/02-player-profile.md) |
| App/UI 层持久状态（当前用户、当前布局、日常脚本参数、窗口位置…） | `config/session/session.json`（`SessionStore` 节点） | 不经 DSL；Python 层经 `core.config.session.get_session_store()` 读写，见 [05-config-layering.md §四](../05-config-layering.md#四用户偏好不进配置层) |

`session` DSL 关键字与上面两套都**不是**同一个存储——它读写的是
`users/{username}.json` 这一份独立文件，目前处于"机制通了、没人用"的状态。
若确实需要"跨次运行持久、按用户隔离、DSL 里直接读写"的数据，且不适合归入
`profile_action()` 的 quota/regen/stock/note 四模型，这里仍是可用的落点；
新增前建议确认 `core.profile` 的模型确实覆盖不了这个场景。

---

## 三、Context：同样是空壳机制

`context` 由引擎在 `__init__` 里自动初始化为空 dict
（`self.context: dict = {}`），每次 `execute()` 重新清空。同 `session`，
**没有系统脚本读写 `context.*`**，早期设想的 `context.bag`（背包格子映射）、
`context.progress`（执行进度）等字段没有被任何实现采用。

工作流内真正的"单次运行临时数据"走法是普通 `$var` 变量
（`eval $x = ...`），作用域由 `call` 的 `self.variables` 保存/恢复机制
天然隔离（见第一节）——这是目前所有 `.wf` 脚本实际在用的方式，比经
`context` 关键字共享一个全局可变 dict 更不容易产生跨过程的隐式耦合。

`context` 关键字机制被保留是为了"确有需要跨子过程共享、又不想持久化"
的场景预留扩展点，不代表现在有任何脚本依赖它。

---

## 四、DSL 访问规范

### 4.1 语法前提：裸 NAME 字段访问

`.name`（裸 NAME）等价于 `."name"`（字符串字面量），四种字段访问写法统一：

```
$result.$var      — 动态（唯一不同，key 来自变量值）
$result."name"    — 静态（字符串字面量）
$result.[name]    — 静态（括号字面量）
$result.name      — 静态（裸 NAME）
```

这使得 `session.current_user` 这样的写法成为可能——即便当前没有脚本真的
这样用，语法本身对任意 dict 变量同样成立，是通用能力，不是专为
`session`/`context` 设计的特例。

### 4.2 session / context 关键字

`session` 和 `context` 为 DSL 裸关键字，不走 `$var` 变量体系。语法上通过
`KeywordRef` AST 节点实现，作为字段访问链的根：

```dsl
# 读取 — 字段链访问
eval $x = session.some_field
eval $y = context.some_field

# 写入 — eval 字段赋值
eval session.some_field = "value"
eval context.some_field = $scan_result
```

### 4.3 安全约束

- `session` 和 `context` 不可被整体赋值（`eval session = 123` 语法不合法）
- 只能读写其内部字段
- 引擎 `_resolve()` 遇到 `KeywordRef("session")` 或 `KeywordRef("context")` 时，直接返回 `self.session` / `self.context` 引用，不走 `self.variables` 查找
- `session` 和 `context` 为保留字，不可用作用户变量名

### 4.4 手动保存

```dsl
# 关键操作后强制写回磁盘
eval save()
```

`save()` 为内置函数，经 `engine._save_callback`（UI 层注入，绑定到
`SessionManager.save()`）把当前 `engine.session` 立即写回
`users/{username}.json`。没有注入回调时（如脚本工作台单独测试）静默跳过
并记 warning，不报错中断。

---

## 五、存储路径

```
config/session/
├── session.json                       ← SessionStore：core/插件的 App/UI 层持久状态
│                                          （active_user、daily 脚本配置、settings…，
│                                          经 core.config.session 读写，不经 DSL）
├── profile.db                          ← SQLite：quota/regen/stock/note 四模型的
│                                          玩家数据（见 02-player-profile.md）
└── users/
    ├── 测试用户A.json                 ← SessionManager：DSL `session` 关键字的落点
    ├── 测试用户B.json                    （见第二节，目前无业务 schema）
    └── ...
```

`session.json` 与 `users/{username}.json` 是两套完全独立的机制，仅仅
恰好挨着放：前者是 App 运行态（`SessionStore`，不经 DSL），后者是 DSL
`session` 关键字读写的用户级 dict（`SessionManager`）。命名相近容易
误认为同一套，写代码或读代码时按用途区分，不要按文件名猜测归属。

---

## 六、引擎集成

```python
class WorkflowEngine:
    def __init__(self, workflow, ...):
        self._wf = workflow
        self.variables: dict = {}
        self._coord_meta: dict = {}
        self.session: dict = {}          # UI 层执行前注入（engine.session = ...）
        self.context: dict = {}          # 每次 execute() 自动重置为空 dict
        self._procs: dict = {}           # def 定义索引，供 call 查找
        ...

    def _resolve(self, node):
        if isinstance(node, KeywordRef):
            if node.name == "session":
                return self.session
            if node.name == "context":
                return self.context
        ...

    def _exec_call_proc(self, node: CallProc):
        """call 在同一引擎实例内执行，不创建子引擎。"""
        proc_def = self._procs[node.name]
        resolved_args = [self._resolve(a) for a in node.args[:len(proc_def.params)]]
        return_value, callee_output = self._run_proc(proc_def, resolved_args)
        ...

    def _run_proc(self, proc_def, resolved_args):
        # 只隔离 variables / output，session / context / _coord_meta 共享引用
        saved_vars, saved_output = dict(self.variables), dict(self.output)
        self.variables, self.output = {}, {}
        try:
            self._exec_body(proc_def.body)
        except _ReturnSignal as e:
            return_value = e.value
        finally:
            self.variables, self.output = saved_vars, saved_output
        return return_value, callee_output
```

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [01-equipment-models.md](01-equipment-models.md) | 装备领域模型完整定义 |
| [02-scene-implementations.md](02-scene-implementations.md) | 场景实现与区域定义 |
| [../05-config-layering.md](../05-config-layering.md) | `session.json`（SessionStore）的节点划分与合并规则 |
| [../../20-requirements/02-player-profile.md](../../20-requirements/02-player-profile.md) | quota/regen/stock/note 四模型（玩家持久数据现在的落点） |
