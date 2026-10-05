# 2026-08 开发日志

本月共 35 篇。日期为**日志日**（记这篇日志的日期），发布日以[发布说明](../../50-releases/README.md)为准。

| 日期（日志日） | 文件 | 主题 |
|------|------|------|
| 2026-08-30 | [2026-08-30-subscene-references-config-version-v090.md](2026-08-30-subscene-references-config-version-v090.md) | 可复用引用场景与三段式访问 + DSL 单行环境分支 + 配置版本显式治理 + 出厂图库按平台命名 + PC 自动调律页面生命周期 + v0.9.0 发布 |
| 2026-08-29 | [2026-08-29-import-sandbox-background-focus-file-tree.md](2026-08-29-import-sandbox-background-focus-file-tree.md) | import 根相对与沙盒封闭 + 后台模式不再抢焦点 + 工作流文件树合并视图 + Profile 升格为主引擎共享模块 + 江湖号令 PC 端可用 |
| 2026-08-28 | [2026-08-28-write-discipline-batch-order-v080.md](2026-08-28-write-discipline-batch-order-v080.md) | 写盘纪律（画布拖拽死区 + OCR 清洗规则显式保存）+ 批量「可见顺序即执行顺序」+ 调律规则安全缺陷 + note 模型上限生效 + v0.8.0 发布 |
| 2026-08-27 | [2026-08-27-remote-config-core-decoupling-i18n-gate.md](2026-08-27-remote-config-core-decoupling-i18n-gate.md) | config/remote 在线下发 + content_version 仲裁 + core 与插件解耦 + i18n 遮蔽事故与一致性门禁 + 静态校验只查可达过程 + session.json 结构梳理 |
| 2026-08-26 | [2026-08-26-ui-domain-refactor-telemetry-per-equip-stats-client-v070.md](2026-08-26-ui-domain-refactor-telemetry-per-equip-stats-client-v070.md) | ui/ 按业务域重构 + main_window 拆分 + 遥测事件粒度改为「一件装备一条」+ stats-client 本地统计控制台 + 最小化到系统托盘 + v0.7.0/v0.7.1 发布 |
| 2026-08-25 | [2026-08-25-telemetry-macro-recorder-gallery-v060.md](2026-08-25-telemetry-macro-recorder-gallery-v060.md) | 匿名使用统计与调律数据回收（从 0 到 1）+ 宏录制补完 + 图库空间目录化 + 技能轴查看器 + Qt 生命周期崩溃根治 + v0.6.0 发布 |
| 2026-08-24 | [2026-08-24-macros-workflows-announcement-hotkeys-v052-v053.md](2026-08-24-macros-workflows-announcement-hotkeys-v052-v053.md) | 宏录制系统完善 + workflows 语义统一 + 远程公告中心 + F7-F12 可配置热键 + core 原子写入收拢 + v0.5.2/v0.5.3 发布 |
| 2026-08-23 | [2026-08-23-screen-calibration.md](2026-08-23-screen-calibration.md) | 屏幕标定：app 内用参照图 + 几组地标对齐本机布局画布；设备端 ScreenMap；macOS 出包；代理通道实机验证 |
| 2026-08-22 | [2026-08-22-device-agent-channel.md](2026-08-22-device-agent-channel.md) | 设备端代理通道：PC 经律匠 app 用无障碍截图 + 手势，替代 adb shell input |
| 2026-08-22 | [2026-08-22-vision-primitives-hold-gesture.md](2026-08-22-vision-primitives-hold-gesture.md) | 图色原语 + find by image 模板定位 + 设备端推住手势/ESC·HOME + 脚本工作台（选指令/取点取色/单步调试） |
| 2026-08-21 | [2026-08-21-target-env-dsl-instructions-v043.md](2026-08-21-target-env-dsl-instructions-v043.md) | 目标环境(env)配置 + 桌面窗口模式后台投递回归标记 + v0.4.3 发布 + DSL press/scroll/else-if 指令补全 + 导航策略拆分与武库拦截 + 桌面小屏布局 |
| 2026-08-20 | [2026-08-20-layout-strategy-workflow-pause-resume.md](2026-08-20-layout-strategy-workflow-pause-resume.md) | 战斗属性布局策略模式重构 + DSL/Python 工作流暂停/恢复机制 + 页面检测去重 + 用户指南全面重组 + 日常江湖看报防误操作 + F8/F12 热键修复 |
| 2026-08-19 | [2026-08-19-loadout-refactor-ui-split-v040.md](2026-08-19-loadout-refactor-ui-split-v040.md) | 多备战方案架构重构 + UI 模块拆分（loadout/tuning）+ 满配假设选项 + 最优组合搜索性能优化（向量化）+ ADB 断连恢复 + v0.4.0/0.4.1/0.4.2 三连发布 |
| 2026-08-18 | [2026-08-18-optimal-combo-search-test-suite-slimdown.md](2026-08-18-optimal-combo-search-test-suite-slimdown.md) | 最优毕业率组合搜索从 0 到 1 + 测试套件大精简（172s→92s）+ 导航工作流加固 + auto_tuning 进度可视化与评级修复 |
| 2026-08-17 | [2026-08-17-mock-equip-module-reorg-dsl-panel-range.md](2026-08-17-mock-equip-module-reorg-dsl-panel-range.md) | 模拟装备功能 + 毕业率模型持续打磨 + yysls 模块重组 + DSL panel 范围索引 + 装备解析器严格化 |
| 2026-08-16 | [2026-08-16-graduation-calculator-combat-attrs.md](2026-08-16-graduation-calculator-combat-attrs.md) | 战斗属性系统 + 全流派毕业率计算器从 0 到 1 + 流派配置 UI 重构 + v0.3.1 发布 |
| 2026-08-15 | [2026-08-15-coordref-entityref-equip-scan-v030.md](2026-08-15-coordref-entityref-equip-scan-v030.md) | DSL CoordRef/EntityRef 坐标与表达式体系 + 装备扫描/背包筛选增强 + 调律进度 Tab 整合 + v0.3.0 发布 |
| 2026-08-14 | [2026-08-14-dsl-int-float-equip-bag-merge.md](2026-08-14-dsl-int-float-equip-bag-merge.md) | DSL int/float 双类型改造 + 装备背包视图合并 + 感知指令文档拆分 |
| 2026-08-13 | [2026-08-13-macos-android-bootstrap.md](2026-08-13-macos-android-bootstrap.md) | macOS 首次支持 + Android 首次落地（引导流程、正式签名、FloatService 输入注入、原生调律配置页） |
| 2026-08-13 | [2026-08-13-tuning-engine-behavior-refactor.md](2026-08-13-tuning-engine-behavior-refactor.md) | 调律规则引擎核心语义重构——行为状态机替代 recycle 段，判定语义下沉到逐条处置规则 |
| 2026-08-13 | [2026-08-13-tuning-config-tools.md](2026-08-13-tuning-config-tools.md) | 调律配置页与调律工具增强——rules_editor 改版、玩法绑定开关、进度对话框 |
| 2026-08-13 | [2026-08-13-dsl-language-extensions.md](2026-08-13-dsl-language-extensions.md) | DSL 语言能力扩展——整面板扫描、返回值绑定、静态预检、编辑器工具链 |
| 2026-08-13 | [2026-08-13-batch-framework-mvp.md](2026-08-13-batch-framework-mvp.md) | 批处理框架从 0 到 1——账号/角色批量编排，四阶段生命周期 |
| 2026-08-13 | [2026-08-13-config-layering-packaging.md](2026-08-13-config-layering-packaging.md) | 配置三层架构落地 + 打包体系从 0 到 1 |
| 2026-08-13 | [2026-08-13-profile-model-foundation.md](2026-08-13-profile-model-foundation.md) | 玩家数据模型重构（上）——四模型体系、后台引擎、SQLite 迁移 |
| 2026-08-13 | [2026-08-13-profile-model-refinement.md](2026-08-13-profile-model-refinement.md) | 玩家数据模型重构（下）——三模型重命名、CAS 写入、来源词表、DSL 集成 |
| 2026-08-13 | [2026-08-13-scene-editor-ocr-cleaning.md](2026-08-13-scene-editor-ocr-cleaning.md) | 场景编辑器增量保存 + OCR 通用清洗架构重构 |
| 2026-08-13 | [2026-08-13-equip-material-recognition-fixes.md](2026-08-13-equip-material-recognition-fixes.md) | 装备解析器与材料/背包识别多处修复，识别置信度阈值调整 |
| 2026-08-13 | [2026-08-13-jianghu-notify-i18n-about.md](2026-08-13-jianghu-notify-i18n-about.md) | 江湖工作流新增、通知告警系统、国际化框架、关于对话框重构 |
| 2026-08-13 | [2026-08-13-docs-mypy-release-housekeeping.md](2026-08-13-docs-mypy-release-housekeeping.md) | 项目整理、mypy/ruff 清零、v0.1.1~v0.2.5 版本发布记录 |
| 2026-08-05 | [2026-08-05-config-refactor-tuning-engine-v015.md](2026-08-05-config-refactor-tuning-engine-v015.md) | 配置架构重构 + 调律引擎修复 + 文档体系重组 + mypy 清零 + v0.1.4/v0.1.5 发布 |
| 2026-08-04 | [2026-08-04-material-recognition-scene-editor-v013.md](2026-08-04-material-recognition-scene-editor-v013.md) | 材料识别重构 + 场景编辑器增强 + DSL 语法扩展 + VS Code 插件 + v0.1.2/v0.1.3 发布 |
| 2026-08-03 | [2026-08-03-screenshot-ocr-canvas-v011.md](2026-08-03-screenshot-ocr-canvas-v011.md) | screenshot 指令 + OCR 画布可视化 + v0.1.1 发布 |
| 2026-08-02 | [2026-08-02-scene-editor-batch-dsl-ocr.md](2026-08-02-scene-editor-batch-dsl-ocr.md) | 场景编辑器增强 + 批处理完善 + DSL 语法扩展 + OCR 清洗架构 |
| 2026-08-01 | [2026-08-01-layout-split-batch-mvp-v010.md](2026-08-01-layout-split-batch-mvp-v010.md) | 布局拆分 + 批处理 MVP + 调律冷却期 + v0.1.0 发布 |

[← 返回开发日志总览](../README.md)
