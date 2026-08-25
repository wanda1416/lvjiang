# 开发日志 2026-08-24

> 接续 08-23 屏幕标定（app 内参照图 + 地标对齐本机布局画布）、设备端 ScreenMap、macOS 出包、代理通道实机验证。
> 本轮主题：**宏录制系统完善 + workflows 语义统一 + 远程公告中心 + F7-F12 可配置热键 + core 原子写入收拢 + v0.5.2/v0.5.3 发布**。

---

## 一、宏录制系统完善

### 1.1 按键、滚轮录制与对话框热键（`80d37ba`）

- `MacroRecorder` 新增 `pynput.keyboard.Listener`：按住 <0.5s 判定为一次完整按键（`press "X"`），否则判定为长按（`press "X" hold N`）；键名转换复用 `win32_keyboard.KEY_NAME_TO_VK`，与播放引擎 `normalize_key` 同源，无法识别的键直接丢弃；
- 鼠标 Listener 新增 `on_scroll`，按 dy 正负判定 up/down，带坐标目标；
- F8/F9/F10/F12 全局热键录制时直接丢弃，不误录成 `press` 语句；
- `press`/`scroll` 接入原有 `_maybe_emit_wait` 机制，`stop()` 补收尾 wait，修复"等了半天按 F12 直接接上下一条、没有 wait"的问题；
- 新增 20 个测试；顺手修正用户手册热键表（F8 是暂停/恢复，脚本录制实际是 F12，文档此前未提及）。

### 1.2 滚轮语法与语义修复（`efcaae0`）

- `send_input`/`post_message` 原来把 `amount` 折算进单条滚轮消息的 delta 一次性发出，很多游戏 UI 只按"是否收到过消息"响应一格、不看 delta 数值，导致 `down 2` 与 `down 1` 效果一致；改为逐格发送 `amount` 次独立标准 delta=120 事件，格间加随机延迟；
- `scroll` 语法统一为 `scroll [target] up|down [n]`，目标紧跟指令关键字、方向紧随其后，与 click/move/drag 风格对齐（此前方向在目标前，是唯一的风格例外）；同步更新 grammar/parser/文档/测试。

### 1.3 高精度 Raw Input 轨迹录制（`4ccfbbc`）

- 新增 `core/desktop/raw_input.py`（Raw Input 注册与消息解析）、`core/input_trace.py`（轨迹落盘/回放数据结构）；
- `send_input.py` 新增轨迹回放能力；`workflows/engine/core.py` 接入轨迹相关执行逻辑；
- 录制侧 `recorder.py`/`script_record_dialog.py` 打通轨迹录制入口；grammar/AST/parser 新增对应语句；
- 新增 `test_input_trace.py`（core 与 workflows 两侧）、`test_raw_input.py`、`test_send_input_move.py` 等多组测试。

---

## 二、workflows 语义统一

### 2.1 鼠标移动语义重定义（`a4b28cc`）

- 重写 `move` 指令行为，涉及 `core/input_base.py`、`core/desktop/send_input.py`/`post_message.py`、`core/android/agent.py`/`input.py`、`core/ondevice/input.py` 六个后端实现，以及 grammar/AST/parser 全链路；文档 `03.3-mouse.md` 同步重写。

### 2.2 click 支持指定鼠标按键（`081e31d`）

- grammar/AST/parser：click 目标之后可选跟鼠标键，省略默认 left；`back`/`forward` 是 x1/x2 的别名，解析后规范化统一；
- `engine._exec_click` 把 button 透传给 `click_screen`，6 个调用分支共用一份 kw dict；
- 6 个 `InputBackend` 实现全部更新：`SendInputInput`（桌面前台）完整支持全部五种键；`PostMessageInput`（已知严重回归、暂不可用）非左键降级为左键并记警告；`AdbInput`/`AgentInput`/`_GestureInput`（触屏）非左键忽略、按普通点击处理并记警告。

### 2.3 导航流程与结果处理统一（`ea60ced`）

- `navigation.wf`：统一 android/desktop 导航逻辑，改为检测式返回；
- `page_detection.wf`：更新背包页检测关键词（制造/整理）；
- `system.py`：修复 `WorkflowUserError` 相对导入路径；
- `daily_jianghu.wf`/`gather_zhayu.wf`/`purchase_bugan.wf`/`purchase_xinfa.wf`/`scan_equipped.wf`/`scan_unequipped.wf`/`scan_wallet.wf` 随之调整调用方式。

### 2.4 新增单点工作流（`d3cf491`）

- 新增 `standalone/fengshajiusi.wf`；`discovery.py`/`metadata.py` 支持单点触发型工作流的发现与元信息；批处理面板与运行控制随之打通；文档补充 `07-subworkflows.md` 与用户指南工作流章节。

### 2.5 其余 workflow 修复

- 安卓扫描装备时跳过桌面端关闭动作（`31f0805`）；
- `scan_wallet.wf`：桌面端 `money_2` 面板实为 4 行×6 列（安卓 3 行×6 列），取值子过程原硬编码 2 行导致第 4 行读不到，改为 `panel_rows()` 动态取行数；安卓端 3×6=18 格装不下全部约 21 种货币，补上滚动补扫逻辑（么玉/宝钱/长鸣玉缺失时上拉 1 行重新识别合并）；么玉点击坐标改为只信任补扫后最后一行重新定位，找不到就放弃点击；新增 `scan_targets()` 合并三次 `scan_get` 调用（`7548518`）；
- `purchase_xinfa.wf` 导航修正 + `training_xinfa` 布局坐标校准（`8d52f3c`）。

---

## 三、远程公告中心（新功能）

`af83437`：从 0 到 1 新增公告能力，新增 `core/announcement.py`（获取、校验、版本筛选、Session 状态管理）与 `ui/announcement_dialog.py`（展示对话框）。

- 公告经 GitHub Pages 静态 JSON（`https://wanda1416.github.io/lvjiang/notices.json`）下发，不依赖 GitHub API；
- 远端 `notice_version` 单调递增，客户端只在版本推进且存在适用于当前客户端版本（`min_app_version`/`max_app_version_exclusive`）的公告时自动弹出；
- 公告缓存与最后已处理版本存放在 `session.json/server_config.announcement`；
- `main_window.py` 接入启动检查；新增 `test_announcement.py`（200 行）、`test_announcement_dialog.py`、`test_startup_announcements.py` 三组测试。

---

## 四、F7-F12 可配置全局热键

`bd645df`：

- `core/config/models.py` 新增 `HotkeyConfig`（`start`/`pause`/`stop`/`record` 四个动作，值限定 F7~F12，非法值回退默认，出现重复键整组回退默认组合），`UserConfig` 挂载 `hotkeys` 字段；
- `core/platforms.py` 新增 `hotkey_pynput_token()`，把展示用按键名（`"F9"`）转换为 pynput `GlobalHotKeys` 需要的 token（`"<f9>"`）；
- `main_window.py`/`run_control.py`/`batch_tab.py`/`script_record_dialog.py`/`macros/recorder.py` 等随用户配置动态重建监听；`settings_dialog.py` 新增热键设置页；
- 配套：移除 OCR 对话框冗余快捷键绑定（`8c919f8`）。

---

## 五、core 原子文件写入统一（`ed3630d`）

- 新增 `core/fs_util.py`，提供 `atomic_write_bytes`/`atomic_write_text`：目标同目录写临时文件、成功后 `os.replace` 覆盖，避免进程崩溃或写入中途失败留下半截文件；支持可选 `fsync`（用于 input_trace 等对崩溃更敏感的双文件保存事务）；
- 此前该套骨架在 `session.py`/`users.py`/`input_trace.py` 三处各自独立实现，收敛为唯一实现，调用方只保留自己的 prefix/编码差异。

---

## 六、UI 交互完善

### 6.1 任务空闲时允许切换后台模式（`406f144`）

- 原逻辑：Windows 定位/ADB 连接后统一锁定"后台模式"复选框，防止误切换；
- 改为：ADB 侧仍锁定，Windows 定位后允许再次切换前台/后台输入模式，无需断连重新定位，仅在任务运行（含暂停）期间锁定，任务结束后自动恢复（新增 `_refresh_bg_mode_lock`）。

### 6.2 新增红框标定开关（`c36d8f1`）

- 主窗口新增"红框标定"复选框，随后台模式一起显示；纯运行期状态，不持久化，默认勾选；
- 控制定位窗口后是否显示红色边框标记：取消勾选立即隐藏，重新勾选且仍处于定位状态则立即按当前窗口位置重画。

### 6.3 分离截图与输入方式配置（`850d797`，安卓）

- `settings_dialog.py`/`window_ops.py` 把安卓端截图方式与输入方式拆成两组独立配置项，不再耦合为单一"连接方式"；`core/config/models.py` 扩展对应字段；文档同步更新设备代理协议与 FAQ。

---

## 七、yysls 模块

### 7.1 角色基础属性扫描（`9162cca`）

- 新增 `scan_role_base_attr.wf`：导航到角色详情页，反复向上拖拽滚动 `detail_1` 并 OCR 识别，途中遇到「属性攻击/外功穿透/属攻穿透」就展开 `detail_2` 一并识别，滚动完毕交给 `role_attr_parser` 解析，触发"创建基础属性"面板预填数值；
- `navigation.wf` 新增 `nav_main_to_role()`，沿用 `back_to_haoling` 的三级兜底范式（find → 翻页再找 → pause 人工介入 → 仍失败 return -1）；
- 新增 `role_attr_parser`：跨屏 OCR 文本去重合并（重叠窗口搜索，容忍滚动边界表头裁切噪声）+ 已知字段提取；
- UI 打通：`MainWindow` 新增 `open_play_style_form` 信号，复用 `equipment_changed` 广播模式；`_CreatePlayStyleDialog` 支持 `initial_values` 预填。

### 7.2 修炼回收场景与布局（`b73e3a9`）

- 新增修炼回收相关场景定义 `training_xinfa.yaml` 与对应布局。

### 7.3 装备与修炼页面布局更新（`86146f4`）

- `equip_detail` 新增 `main_func`/`more_func`/`sub_func` 区域；`game_main_page` 新增 `huanma`/`more_func`/`tingfeng` 区域；`training_main` 5 个区域坐标校准。

### 7.4 战斗属性卡片布局修复（`407639b`）

- `attrs_tab.py`/`combat/layout.py`/`combat/layout_strategies.py` 修复布局问题；后续 `bd585d0` 补测试放宽 Qt 网格像素取整容差。

### 7.5 模拟装备词条硬约束 + 实时红字提示（`f8bd74e`）

给"模拟装备创建/编辑"（`MockEquipDialog`）补上词条 2-5（不含首词条）的三条游戏内硬性规则：

1. 属攻类词条（四大属性攻击 + 自动适配武学的无相攻击）最多 2 条且不能重复；
2. 神力词条（增效类 + 武器类）最多 1 条；
3. 神力词条不能是转律产出的（转律不会产出神力词条）。

- 新增 `_validate_affix_rules()`，词条归属查 `GameConfigManager` 已有的 `get_affix_category()`，不需要新配置；
- 填写阶段不约束，只在两处生效并共用同一份校验逻辑：按钮行左侧新增红字提示标签、词条名变化实时刷新；点击确认时同一份校验做最终拦截；
- 新增 `test_mock_equip_dialog.py`（11 条用例，用仓库真实 `game_config.yaml`，不 mock）。

### 7.6 角色档案空值可编辑（`e43fd3b`）

- `profile_db.py` 的 CAS 更新方法 `expected_entry_exists` 参数化：预期条目不存在时走原子插入，若已被其他进程先行创建则仍返回冲突；此前空值条目无法通过 CAS 路径编辑。

---

## 八、反馈对话框与规范完善

`91786df`：

- 新增用户指南 `08-feedback-and-issues.md`（206 行反馈规范）；`06-faq.md`/`07-workflows.md` 随文档编号调整重命名；
- `feedback_dialog.py` 扩充反馈入口与文案；新增 `test_feedback_dialog.py`。

`ad33d0c`：更新反馈二维码图片（`data/image/feedback-qrcode.jpg`）。此处仍是静态图片，后续另有改动把它做成点击刷新、从 github.io 拉取最新版二维码，本次未涉及。

---

## 九、其他修复与整理

- VS Code 插件虚拟环境探测修复（`603dbf0`）：根因是 `package.json` 里 `lvjiangWf.pythonPath` 的 `default` 被写成非空字符串 `"python"`，导致 `resolvePythonPath()` 的 `if (configured)` 永远为真，短路掉后续 VS Code Python 设置读取和 `.venv` 自动探测，实际上永远用系统 PATH 的 python 启动 LSP 进程，而 pygls/lsprotocol 只在项目 dev 依赖组里，进程握手前因 `ModuleNotFoundError` 退出、被判定反复崩溃；修复：默认值改空字符串、`.venv` 探测改为向上遍历最多 6 层父目录、系统 python 兜底按平台探测真实可执行文件、失败提示改为可操作指引；
- mypy 类型错误修复（`9fb58b0`）：`play_style_dialog.py`/`main_window.py`/`data_ops.py`；
- 文档结构整理与脱敏（`59b3d1c`）：修复 7 处坏链；调律文档目录改用 ASCII 命名（`10-tuning-rules/`）并消除与实现漂移的重复规格；待办与平台计划移入 `00-meta/`；删除一份外部计算引擎逆向分析文档；
- 修正 PC 端与后台投递可用性说明（`ec967a8`）：README/TODO/`v0.4.3.md`/用户指南/`post_message.py` 注释同步。

---

## 十、v0.5.2 / v0.5.3 发布

### 10.1 v0.5.2（`07bf687`）

- 版本号升级 0.5.1 → 0.5.2；发布说明汇总设备端代理、屏幕标定、宏录制、F7-F12 热键、远程公告中心、红框标定、后台模式切换等本轮及 08-23 屏幕标定相关全部改动；34 commits（自 v0.5.1）。

### 10.2 v0.5.3（`d74f4e5`）

- 版本号升级 0.5.2 → 0.5.3；仅修复角色档案空值无法编辑的问题（`e43fd3b`）；4 commits（自 v0.5.2）。

---

## 结果

- 本轮（含 08-23 尾声 7 commits）共 39 commits；
- v0.5.2、v0.5.3 两个版本先后发布；
- 全量测试通过（`f8bd74e` 提交信息记录 2428 passed，ruff 全绿）。

---

## 关键设计决策（用户确认）

1. **滚轮事件语义**：逐格发送独立标准 delta=120 事件而非折算单条 delta，兼容只认"是否收到消息"的游戏 UI。
2. **鼠标操作 DSL 风格统一**：`scroll`/`move`/`click` 均为「目标紧跟指令关键字」范式，`click` 可选按键、省略默认 left。
3. **原子写入唯一实现**：`core/fs_util.py` 收拢 session/users/input_trace 三处独立实现，`fsync` 仅用于崩溃敏感场景。
4. **后台模式切换粒度**：Windows 定位后允许运行期外随时切换前台/后台输入模式，不要求断连重新定位；仅在任务运行期间锁定。
5. **公告下发方式**：走 GitHub Pages 静态 JSON + 单调递增 `notice_version`，不依赖 GitHub API。
6. **模拟装备词条硬约束**：仅作用于词条 2-5，首词条不受限；填写阶段不拦截、仅确认时终局校验 + 实时红字提示。
