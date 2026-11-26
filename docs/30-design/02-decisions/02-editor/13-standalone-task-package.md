# 独立任务包（Standalone Task Package）与场景管理双模式设计

## 背景与目标

部分专项玩法任务（如「风沙酒肆」）的场景与布局高度独立，不应污染全局 `scenes/` 与 `layouts/`，但仍需要精确的场景/区域/坐标定义（不能退化为全图扫描），同时满足两项核心诉求：

1. **可独立发布交给他人**：一个文件夹即完整交付单元，接收方放入 `config/local/workflows/` 下即可直接运行，删除目录即完整卸载，不修改任何全局配置文件。
2. **保留场景管理加载查看与调试能力**：在「场景管理」顶栏第一行最右侧提供「加载任务场景 / 退出任务场景」双模式切换，复用现有画布、布局与场景编辑体验。

## 目录结构与 `manifest.yaml`

独立任务包直接复用现有 `scenes/`、`layouts/` 与可选 `templates/` 目录结构，不造新格式、不改 `.wf` 语法：

```text
fengshajiusi/
├── manifest.yaml         # 标识独立任务包与入口元信息
├── fengshajiusi.wf       # 入口脚本（与普通 .wf 语法完全一致）
├── scenes/               # 与全局 config/system/scenes/ 结构一致（支持多分组）
├── layouts/              # 与全局 config/system/layouts/ 结构一致（支持 desktop/android 及 extends）
└── templates/            # 可选：任务私有模板图
```

`manifest.yaml` 最小字段：

```yaml
schema_version: 1
id: fengshajiusi
name: 风沙酒肆
entry: fengshajiusi.wf
version: "1.0.0"
author: ""
min_app_version: ""
```

路径安全约束：`entry` 必须为包目录内的相对 `.wf` 路径，禁止 `..` 与绝对路径。
包内的 `import` 仍按 workflows 根解析（见下节「首个真实包」），因此包目录位置是脚本
的一部分，交付时必须保持同一层级。

## 运行态与编辑态隔离机制

### 1. 普通任务零影响原则
未命中同级 `manifest.yaml` 的普通 `.wf` 任务，运行期与静态分析均直接短路返回原有对象，完全走现有代码路径，零额外开销、零行为变化。

### 2. 任务运行态（启动合并加载，退出释放）
`WorkflowEngine._execute_dsl` 检测到 `manifest.yaml` 时进入 `task_package_runtime_scope`：
- **冲突检测**：任务包的**场景分组 key** 或 **场景 key** 与全局主场景重名时，直接抛出 `WorkflowUserError` 中止启动，要求开发者使用独特前缀（如 `fsjs_`）。
- **同名布局合并**：`layouts/` 下的根布局 key（如 `desktop`、`android`）允许且必然与全局同名合并；当主环境激活 `desktop` 或其别名（如 `desktop_fullscreen`）时，自动合并任务包内对应根布局的场景绑定到当前布局副本，不修改原始 `Layout` 对象。
- **模板回退**：通过 `OverlayConfigResolver` 优先查找任务包 `templates/`，未命中则回退全局 `templates/`。
- **退出释放**：基于线程级 `ContextVar` 与 `try ... finally` 释放作用域，绝不修改进程全局 `_registry` 单例或污染下一个任务。

### 3. 场景管理编辑态（纯切根，零合并）
- 点击「加载任务场景」后，先做主场景脏检查，再通过 `ContextVar` 将编辑器作用域切换到指向任务包目录的独立 `ConfigResolver`、独立 `SceneRegistry` 与独立截图目录（`config/session/screenshots/_packages/{pkg_id}/`）。
- 顶栏右侧按钮切换为「退出任务场景」，窗口标题显示 `[任务场景：{name}]`；左侧布局下拉框、增删改、画布标定、截图与识别测试全部原样复用。
- 点击「退出任务场景」或关闭窗口时，恢复原 `ContextVar` 作用域与主场景布局/Tab 状态，确保不泄漏到主界面。

### 4. 静态预检同样进作用域
`WorkflowEngine.validate_only` 与执行期共用同一套包作用域：先把包内场景与同名根布局
合并，再跑布局引用校验。否则 `task --dry-run`、编辑器「检查」与 CI 门禁会拿全局布局去
比对包私有场景，把能跑的包判成「场景未绑定任何坐标」。普通任务的作用域是空操作，
判据不变。

## 首个真实包：风沙酒肆（`fengshajiusi`）

`config/system/workflows/standalone/fengshajiusi/` 是当前唯一的参照实现（多文件、
双平台、带私有场景与两套布局）：

```text
fengshajiusi/
├── manifest.yaml                 # id fengshajiusi / entry fengsha_jiushi_night.wf
├── fengsha_jiushi_night.wf       # 入口：参数、词表、主流程、两端共用过程
├── desktop.wf  /  android.wf     # 平台过程（*_desktop / *_android）
├── scenes/                       # 分组 fsjs + 场景 fsjs_night（四个阶段视图）
├── layouts/{desktop,android}/    # 两套实机标定的区域坐标
└── README.md                     # 交付说明（安装位置、重新标定步骤）
```

实现这个包时确认的几条约定：

- **import 路径恒相对 workflows 根**（`get_resolver().resolve_read("workflows/<rel>")`，
  运行期 overlay 只代理 `templates/`），所以包内子文件要写
  `import "standalone/fengshajiusi/desktop.wf"`。代价是**包必须固定在这个目录**：
  交付时整目录放到 `config/local/workflows/standalone/fengshajiusi/`，不能改名或挪层级。
- **包内场景/布局文件不带 `content_version`**：它们不在全局 content 版本索引里
  （`track_content=False`），`scripts/ci/add_content_version.py` 也不会扫到
  `workflows/…` 下的这些文件。
- **分组与场景 key 用独特前缀**（`fsjs` / `fsjs_night`），避免与全局主场景重名——
  重名会在启动时直接报错中止。
- **确认不了坐标的实体标 `disabled`**：桌面端没有「听风辨位」耳朵按钮、线索弹窗的
  「确认」只是 `Space` 键提示，因此 `listen_btn` / `clue_confirm` 在桌面布局里标
  `disabled`（静态检查视为已处理），被引用时运行期显式报「区域未绑定布局坐标」，
  不会静默退回整屏扫描。
- **交付形态就是文件夹**：用户自行解压到 local 层即可，不做额外打包脚本。

