# 开发日志 2026-08-13（一）

> 接续 08-05 配置架构重构与调律引擎修复、文档体系重组、mypy 清零、v0.1.4/v0.1.5 发布。
> 本轮主题：**macOS 首次支持 + Android 首次落地（引导流程、正式签名、FloatService 输入注入、原生调律配置页）**。
>
> 说明：本篇及后续同日各篇归并的是一次性集中提交的大批改动（307 个 commit 落在同一个 committer 时间戳），实际开发时间跨度可能远不止一天，按 git 历史如实分主题记录。

---

## 一、macOS 支持首次落地

### 1.1 依赖版本约束（`de43a52`）

- `pyproject.toml` 新增 macOS 平台依赖版本约束：`PyQt6<6.8`（6.8+ 需 macOS 13+）、`onnxruntime>=1.19,<1.21`（1.20+ 需 macOS 13+）；
- 依据实际运行环境锁定兼容版本区间，避免打包后在旧版 macOS 上无法启动。

### 1.2 退出崩溃修复

macOS 下应用退出阶段暴露出多个跨平台通用的生命周期问题，集中修复：

- 退出时 use-after-free + pynput libffi 递归崩溃（`9f5ccb3`）；
- 退出时先停 loguru 异步写入线程，避免退出挂起（`c117d93`）；
- `_wait_for_threads` 排除 `_DummyThread`（PyAV/onnxruntime 的 native 线程包装不支持 `join()`），避免 `AssertionError`（`a563cc2`）；
- 修复 desktop capture 的关闭生命周期（`0693055`）；
- 显式清理 Qt 对象（`app.exec()` 返回后立即 close+del window/app），修复退出时 SIP 析构 `EXC_BAD_ACCESS` 崩溃（`e0fbe06`）；
- 进一步将 QApplication/MainWindow/hooks 提升为模块级全局变量 + `atexit`，避免函数返回后 SIP 以随机顺序析构访问已释放指针（`28a45ce`）。

---

## 二、Android 首次落地

### 2.1 首次启动引导流程（`a3b71fc`）

- `activity_main.xml` 引导区（权限清单 + 主按钮 + 每步提示）置顶，开发者自检按钮收进默认折叠的「高级」面板；
- MainActivity 引导状态机：主按钮永远指向第一个未完成项（无障碍 → 悬浮窗 → 启动悬浮图标 → 就绪），通知权限顺带申请；
- e2e 回归测试补齐 R8 混淆下的反射面覆盖。

### 2.2 正式签名落地（`bdbac2f`）

- 本机生成 `android/lvjiang.jks`（RSA 2048 / 10000 天 / 别名 lvjiang），密钥材料与 `keystore.properties` 均入 `.gitignore`，不入库；
- 新增 `README-signing.md`：属性格式、重建命令、备份提醒、签名变更需卸载重装等说明；
- `assembleRelease` 复验走正式签名链路，`apksigner` 确认 Signer CN=lvjiang。

### 2.3 FloatService 输入注入（`385028a`）

- FloatService 适配 Shizuku shell 级权限注入 tap；
- 构建配置（`build.gradle.kts`）同步调整。

### 2.4 设备端插件加载修复（`52d6128`）

- 新增 `core/ondevice/plugins.py` 幂等 `ensure_loaded()`（DEVICE_APPS 登记），`list_tasks()`/`_resolve_task()`/`create_engine()` 三入口统一调用；
- 修复 class 型脚本退化成同名旧 `.wf`、DSL 游戏内置函数未注册两类静默故障；
- `workflows.yaml` 的 `exposed` 增加 `auto_tuning`，自动调律在设备端暴露。

### 2.5 原生调律参数配置页（`5362325`）

- 新增 `TuningConfigActivity` 全屏配置页（规则/部位/开关三区块），条目全部由 Python 侧枚举返回，Kotlin 不写死清单，写入插件会话 tuning 节；
- MainActivity 主页化（权限卡 + 功能区），悬浮面板空闲态加配置入口；
- 修复部位会话回退问题。

### 2.6 透明悬浮层主题（`991f7a8`）

- 病灶：`TuningConfigActivity` 是不透明全屏 Activity，从悬浮面板打开时游戏被挤到后台，低内存设备上被 low-memory-killer 回收；
- 修复：`windowIsTranslucent` + 80% 黑底 `windowBackground`，配置页叠加显示，游戏进程不再被系统判定为后台可回收。

### 2.7 其他

- versionCode 9→10，todo 记录插件加载修复（`11a07b2`）；
- ADB 无线扫描集成——局域网设备发现与自动连接，带进度回调的自定义扫描对话框（`fd55b20`）；局域网扫描支持取消，修复对话框销毁后的过期回调竞态（`0332694`）；
- 文档同步：更新 todo-android 进度笔记（`193f519`）、同步 todo-android 至 08-01 实际上机状态（`cd6b01e`）、新增面向普通用户的用户指南（连接手机、配置与运行自动调律）（`ae0d4eb`）。

---

## 结果

- 本篇归并 macOS/Android 平台相关 commit 约 20 个；
- macOS：首次跑通依赖验证与退出流程，无版本号变化；
- Android：首次完成引导流程、正式签名、原生输入注入与调律配置页，具备独立分发条件。

---

## 关键设计决策（用户确认）

1. **macOS 依赖锁版本**：按运行环境实测锁定 PyQt6/onnxruntime 版本区间，而非追新，规避 macOS 13+ 门槛问题。
2. **Android 签名不入库**：keystore 与签名属性均排除在版本控制之外，仅文档化重建流程。
3. **配置页悬浮层化**：Android 原生配置页改为透明悬浮层而非独立全屏 Activity，避免游戏进程被系统判定后台回收。
4. **设备端能力由 Python 侧枚举**：Kotlin 原生 UI 不写死规则/部位清单，全部通过插件会话动态下发。
