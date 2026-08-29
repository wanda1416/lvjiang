# 开发日志 2026-08-22

> 主题：**图色原语 + `find … by image` 模板定位 + 设备端推住手势 / ESC·HOME + 脚本工作台**
> 涉及：`core/recognizers/`、`workflows/builtins/vision.py`、`workflows/grammar/`、`core/ondevice/`、`android/app/.../A11yService.kt`、`ui/script_*`、`ui/action_palette.py`

---

## 一、为什么

律匠作为"通用视觉 RPA 引擎"，感知通道一直只有 OCR（`scan`/`find`）和参考图分类（`recognize`）。
游戏 UI 不在无障碍树里，"现在是哪个界面 / 按钮亮没亮 / 某个随机位置的图标在哪"这类问题
OCR 又慢又答不了。按键精灵 / AutoJS 系平台的共识是**三件套**：UI 选择器 + 图色 + OCR，
律匠缺"图色"半边；另外设备端 `drag … hold` 的实现语义是错的（见 §四）。本轮把这两块补齐。

## 二、图色原语（`color_ops.py` + `builtins/vision.py`）

纯 numpy 实现 7 个原语：`pixel_rgb / brightness / color_ratio / bright_segments / color_vec /
find_multi_color / find_icons`（后者用 `cv2.connectedComponentsWithStats` 8 连通）。
DSL 以内置函数暴露（`pixel / bright / color_ratio / bright_segs / color_vec / find_icons /
find_multi_color`），入参 CoordRef，距离类参数按画布高比例、偏移按画布宽比例，跨分辨率。

设计取舍：走内置函数而不是新指令——这些是标量/列表求值，进 `eval` 和条件表达式最自然；
`scan/recognize/find` 的语义是"区域 → 结构化结果存变量"，不合适。文法零改动。

**经验**：
- `color_ratio` / `bright` 与分辨率无关（全/半分辨率实测数值一致）；`bright_segs` 数亮暗跳变，
  缩放会合并细缝、段数略降，阈值要留余量。
- `find_icons` 的"主导通道"spec 只在对应界面下有意义：同一绿色 spec 在主界面会把大块绿按钮整个
  命中。状态派发要先判界面再找图标，已写进 `06.4-vision-functions.md`。

## 三、模板定位（`template_locator.py` + `find … by image`）

`cv2.matchTemplate(TM_CCOEFF_NORMED)` + 多尺度：sidecar `recordW` → `当前画布宽 / recordW` 基准
±10%，再加 1.0 兜底。文法加 `match_mode: "image"`，只给 `find`；`scan/recognize` 在 transformer
里拒绝（lark 包成 `VisitError`，与既有 `full by` 校验一致）。`where confidence >= x` 复用为匹配分门槛
（缺省 0.8）。模板落 `config/system/templates/<name>.png + .json`，走 ConfigResolver 双层。

与 `recognize` 的分工：那是"这块区域最像图库哪一条"（分类，ORB），这是"这张小图在画面哪里"（定位）。

**踩坑**：精确贴图的 CCOEFF 分数恰为 1.0，`where >= 0.999` 测不出门槛，先把贴图区域高斯模糊到
(0.8, 0.999) 再验两侧；`make_engine()` 的 `input_sim` 是 MagicMock，点击坐标经
`random.uniform(mock, mock)` → `int(MagicMock)` = 1，要先把 `region_jitter_ratio` 置 0。

## 四、设备端推住手势 + ESC/HOME（`A11yService.kt` / `ondevice/input.py`）

- 原 `drag … hold n` 设备端把 hold 合并进单 stroke 时长：`dispatchGesture` 的 duration 是沿整条 path
  的总时长，手指在整个时间内**匀速滑完全程**，推摇杆成了"慢慢推"而非"推到位停住"。改为
  move stroke(`willContinue`) + `continueStroke` 1px dwell（两段各等回调）。`_GestureInput` 拆出
  `_drag(move_ms, hold_ms)` 原语，ShellInput 仍合并时长（`input swipe` 做不到停住）。
- `press "ESC"` → Android BACK、`press "HOME"` → HOME（`performGlobalAction`）。不引入 "BACK" 键名：
  桌面键名表没有它，且游戏里 Escape 与返回键语义相同，一份 `.wf` 两端通用。
- **Kotlin 验证**：本机 Gradle 链走不通——brew Gradle 9 与 AGP 8.7 不兼容（`org.gradle.util.VersionNumber`
  已删），换 8.14 后又卡在 Chaquopy `buildPython`（`build.gradle.kts` 按 Windows 工具链配置）。改用
  `kotlinc -cp android-34/android.jar` 单文件编译 HEAD 与工作树两版均通过，`javap` 确认
  `holdMove/globalBack/globalHome` 符号在产物中。**真机未跑**。

## 五、脚本编辑对话框（`ui/script_editor_dialog.py`）

原先新建 / 改 `.wf` 只能开文件管理器，或从脚本录制对话框另存。加「工具 → 脚本编辑」（F7；F6 已被燕云插件的「调律配置」占用）：
列表（system ∪ local 合并视图，标层来源）+ 语法高亮编辑区 + 新建 / 保存 / 另存为 / 删除 / 检查。

- 写入走 `ConfigResolver.write_entity`（开发→system，用户→local 影子），删除走 `delete_entity`
  （用户模式下系统脚本落墓碑而非真删）——与场景管理、脚本配置同一套模式判定，不另开写路径。
- 校验分两档：「检查」只 `parse_text`；「保存」落盘后用 `WorkflowEngine(capture=None, …).validate_only`
  跑完整静态校验（import 链 / 命名等待 / 布局引用），判据与真执行共用。硬件后端传 None 是安全的，
  `validate_only` 不碰它们。
- 新建脚本自动追加进 `workflows.yaml` 的 `exposed`（列表非空时只展示已暴露的，否则新脚本在日常页看不到）。
- 高亮关键字集合与 VS Code 扩展的 tmLanguage 同源，另补了 `press/move/scroll/image`（扩展那边还没更新）。
- 脚本 id 拒绝 `_` 前缀：发现层把 `_*.wf` 当临时文件跳过，允许的话用户会创建一个"保存成功但列表里没有"的脚本。

**第二轮：从"文本编辑器"纠正为"可视化工作台"**（用户原意是抓抓式取点器 + 单步调试，我第一轮把需求
收窄成了文本编辑——动手前没把需求形状对一句）。补的三块：

- `ui/pick_canvas.py`：OCRCanvas 子类，加 `hovered / picked / region_changed` 三个信号（按下-抬起位移
  ≤4px 且无选框 = 点击）。截图按活动布局 canvas 裁过再显示，取到的就是 DSL 画布坐标。
- `ui/script_workbench.py::DebugPanel`：画布 + 「插入坐标/颜色/区域」+ 运行/单步/继续/暂停/停止 +
  变量表 + 日志。脚本写到 `_editor_run.wf`（与场景编辑器试运行共用），`WorkflowWorker` 线程跑引擎，
  loguru 临时 sink 把本次运行日志投到面板。
- 引擎 `statement_hook(line_no, variables 快照)` + `step_mode`：每条语句前回调；单步 = 引擎自己 clear
  pause_event 再等，UI set 一次走一条；「暂停」只打开 step_mode（下一条前停，不切断正在执行的动作）；
  「停止」set 事件唤醒引擎——**唤醒后必须再查一次停止标志**，否则会把当前语句执行完才退出（测试抓到的）。
- 文法加 `rect_literal` `(x, y, w, h)` → `RectCoordRef`（画布框出来的区域直接 `click $r` / 喂图色函数）；
  图色内置函数也接受 `(x, y)` / `(x, y, w, h)` 元组。
- 已知小瑕疵：块语句（`loop`/`if`）头节点的 `line_no` 由文法取自首个子 token，单步停在块头时高亮落在
  块内第一行；是既有取法，本轮没动。

**第三轮：快捷指令式「选操作」**（`workflows/action_catalog.py` + `ui/action_palette.py`）

- 目录是纯数据：每条指令 = 模板字符串 + 槽位规格（11 种槽位类型决定控件、取值来源、渲染规则），
  可选槽位留空整段不出现、非空按 `wrap` 包装（`after wait {v}`）。36 条指令覆盖交互 / 感知 / 图色 /
  控制流 / 数据。参数化测试保证**每条指令的默认渲染都能被 DSL 解析**——目录和文法漂移会立刻被抓到。
- 面板按槽位类型生成表单；区域下拉随场景联动；坐标 / 区域 / 颜色槽位从调试画布最近取值预填或
  一键「取画布」；预览随输入刷新，缺必填标红并禁用插入。
- 编辑器新增 `insert_statement`：光标行已有内容先换行，多行模板沿用当前缩进——块模板插进 `if` 体内
  缩进是对的。

## 六、质量门禁

ruff 全绿；mypy 仅剩 `ui/ocr_dialog.py:456` 一条存量（本轮未触碰该文件）；pytest 全绿。

## 七、待办

- 设备端手势真机验证（推住摇杆持续移动；`press "ESC"` 退子界面）；顺带核一次截图尺寸与输入
  坐标系是否一致（不同机型的系统 UI 裁边会让两者差一截）。
