# 开发日志（过程层）

按时间记录开发过程中的重要决策、问题排查、实验记录。

## 目录结构

- `YYYY-MM/` — 按月分组的开发日志
- `references/` — 拿来参考的开发信息（API 文档、技术调研等）

## 参考信息

| 文件 | 主题 |
|------|------|
| [references/game-api.md](references/game-api.md) | 燕云十六声官方 API 接口参考（装备/体力/心力/不肝数据） |

## 已有日志

### 2026-08

| 日期 | 文件 | 主题 |
|------|------|------|
| 2026-08-24 | [2026-08-24-macros-workflows-announcement-hotkeys-v052-v053.md](2026-08/2026-08-24-macros-workflows-announcement-hotkeys-v052-v053.md) | 宏录制系统完善 + workflows 语义统一 + 远程公告中心 + F7-F12 可配置热键 + core 原子写入收拢 + v0.5.2/v0.5.3 发布 |
| 2026-08-23 | [2026-08-23-screen-calibration.md](2026-08/2026-08-23-screen-calibration.md) | 屏幕标定：app 内用参照图 + 几组地标对齐本机布局画布；设备端 ScreenMap；macOS 出包；代理通道实机验证 |
| 2026-08-22 | [2026-08-22-device-agent-channel.md](2026-08/2026-08-22-device-agent-channel.md) | 设备端代理通道：PC 经律匠 app 用无障碍截图 + 手势，替代 adb shell input |
| 2026-08-22 | [2026-08-22-vision-primitives-hold-gesture.md](2026-08/2026-08-22-vision-primitives-hold-gesture.md) | 图色原语 + find by image 模板定位 + 设备端推住手势/ESC·HOME + 脚本工作台（选指令/取点取色/单步调试） |
| 2026-08-21 | [2026-08-21-target-env-dsl-instructions-v043.md](2026-08/2026-08-21-target-env-dsl-instructions-v043.md) | 目标环境(env)配置 + 桌面窗口模式后台投递回归标记 + v0.4.3 发布 + DSL press/scroll/else-if 指令补全 + 导航策略拆分与武库拦截 + 桌面小屏布局 |
| 2026-08-20 | [2026-08-20-layout-strategy-workflow-pause-resume.md](2026-08/2026-08-20-layout-strategy-workflow-pause-resume.md) | 战斗属性布局策略模式重构 + DSL/Python 工作流暂停/恢复机制 + 页面检测去重 + 用户指南全面重组 + 日常江湖看报防误操作 + F8/F12 热键修复 |
| 2026-08-19 | [2026-08-19-loadout-refactor-ui-split-v040.md](2026-08/2026-08-19-loadout-refactor-ui-split-v040.md) | 多备战方案架构重构 + UI 模块拆分（loadout/tuning）+ 满配假设选项 + 最优组合搜索性能优化（向量化）+ ADB 断连恢复 + v0.4.0/0.4.1/0.4.2 三连发布 |
| 2026-08-18 | [2026-08-18-optimal-combo-search-test-suite-slimdown.md](2026-08/2026-08-18-optimal-combo-search-test-suite-slimdown.md) | 最优毕业率组合搜索从 0 到 1 + 测试套件大精简（172s→92s）+ 导航工作流加固 + auto_tuning 进度可视化与评级修复 |
| 2026-08-17 | [2026-08-17-mock-equip-module-reorg-dsl-panel-range.md](2026-08/2026-08-17-mock-equip-module-reorg-dsl-panel-range.md) | 模拟装备功能 + 毕业率模型持续打磨 + yysls 模块重组 + DSL panel 范围索引 + 装备解析器严格化 |
| 2026-08-16 | [2026-08-16-graduation-calculator-combat-attrs.md](2026-08/2026-08-16-graduation-calculator-combat-attrs.md) | 战斗属性系统 + 全流派毕业率计算器从 0 到 1 + 流派配置 UI 重构 + v0.3.1 发布 |
| 2026-08-15 | [2026-08-15-coordref-entityref-equip-scan-v030.md](2026-08/2026-08-15-coordref-entityref-equip-scan-v030.md) | DSL CoordRef/EntityRef 坐标与表达式体系 + 装备扫描/背包筛选增强 + 调律进度 Tab 整合 + v0.3.0 发布 |
| 2026-08-14 | [2026-08-14-dsl-int-float-equip-bag-merge.md](2026-08/2026-08-14-dsl-int-float-equip-bag-merge.md) | DSL int/float 双类型改造 + 装备背包视图合并 + 感知指令文档拆分 |
| 2026-08-13 | [2026-08-13-macos-android-bootstrap.md](2026-08/2026-08-13-macos-android-bootstrap.md) | macOS 首次支持 + Android 首次落地（引导流程、正式签名、FloatService 输入注入、原生调律配置页） |
| 2026-08-13 | [2026-08-13-tuning-engine-behavior-refactor.md](2026-08/2026-08-13-tuning-engine-behavior-refactor.md) | 调律规则引擎核心语义重构——行为状态机替代 recycle 段，判定语义下沉到逐条处置规则 |
| 2026-08-13 | [2026-08-13-tuning-config-tools.md](2026-08/2026-08-13-tuning-config-tools.md) | 调律配置页与调律工具增强——rules_editor 改版、玩法绑定开关、进度对话框 |
| 2026-08-13 | [2026-08-13-dsl-language-extensions.md](2026-08/2026-08-13-dsl-language-extensions.md) | DSL 语言能力扩展——整面板扫描、返回值绑定、静态预检、编辑器工具链 |
| 2026-08-13 | [2026-08-13-batch-framework-mvp.md](2026-08/2026-08-13-batch-framework-mvp.md) | 批处理框架从 0 到 1——账号/角色批量编排，四阶段生命周期 |
| 2026-08-13 | [2026-08-13-config-layering-packaging.md](2026-08/2026-08-13-config-layering-packaging.md) | 配置三层架构落地 + 打包体系从 0 到 1 |
| 2026-08-13 | [2026-08-13-profile-model-foundation.md](2026-08/2026-08-13-profile-model-foundation.md) | 玩家数据模型重构（上）——四模型体系、后台引擎、SQLite 迁移 |
| 2026-08-13 | [2026-08-13-profile-model-refinement.md](2026-08/2026-08-13-profile-model-refinement.md) | 玩家数据模型重构（下）——三模型重命名、CAS 写入、来源词表、DSL 集成 |
| 2026-08-13 | [2026-08-13-scene-editor-ocr-cleaning.md](2026-08/2026-08-13-scene-editor-ocr-cleaning.md) | 场景编辑器增量保存 + OCR 通用清洗架构重构 |
| 2026-08-13 | [2026-08-13-equip-material-recognition-fixes.md](2026-08/2026-08-13-equip-material-recognition-fixes.md) | 装备解析器与材料/背包识别多处修复，识别置信度阈值调整 |
| 2026-08-13 | [2026-08-13-jianghu-notify-i18n-about.md](2026-08/2026-08-13-jianghu-notify-i18n-about.md) | 江湖工作流新增、通知告警系统、国际化框架、关于对话框重构 |
| 2026-08-13 | [2026-08-13-docs-mypy-release-housekeeping.md](2026-08/2026-08-13-docs-mypy-release-housekeeping.md) | 项目整理、mypy/ruff 清零、v0.1.1~v0.2.5 版本发布记录 |
| 2026-08-05 | [2026-08-05-config-refactor-tuning-engine-v015.md](2026-08/2026-08-05-config-refactor-tuning-engine-v015.md) | 配置架构重构 + 调律引擎修复 + 文档体系重组 + mypy 清零 + v0.1.4/v0.1.5 发布 |
| 2026-08-04 | [2026-08-04-material-recognition-scene-editor-v013.md](2026-08/2026-08-04-material-recognition-scene-editor-v013.md) | 材料识别重构 + 场景编辑器增强 + DSL 语法扩展 + VS Code 插件 + v0.1.2/v0.1.3 发布 |
| 2026-08-03 | [2026-08-03-screenshot-ocr-canvas-v011.md](2026-08/2026-08-03-screenshot-ocr-canvas-v011.md) | screenshot 指令 + OCR 画布可视化 + v0.1.1 发布 |
| 2026-08-02 | [2026-08-02-scene-editor-batch-dsl-ocr.md](2026-08/2026-08-02-scene-editor-batch-dsl-ocr.md) | 场景编辑器增强 + 批处理完善 + DSL 语法扩展 + OCR 清洗架构 |
| 2026-08-01 | [2026-08-01-layout-split-batch-mvp-v010.md](2026-08/2026-08-01-layout-split-batch-mvp-v010.md) | 布局拆分 + 批处理 MVP + 调律冷却期 + v0.1.0 发布 |

### 2026-07

| 日期 | 文件 | 主题 |
|------|------|------|
| 2026-07-31 | [2026-07-31-dsl-docs-reorg-tuning-rules-platform.md](2026-07/2026-07-31-dsl-docs-reorg-tuning-rules-platform.md) | DSL 文档重组 + 调律规则模型大重构 + 平台适配层 + macOS 支持 |
| 2026-07-30 | [2026-07-30-tuning-materials-and-crash-fix.md](2026-07/2026-07-30-tuning-materials-and-crash-fix.md) | 属攻词条双重身份 + 调律材料策略 + 狗粮规则引擎 + 退出崩溃根治 |
| 2026-07-26 | [2026-07-26-config-engine-and-attrs.md](2026-07/2026-07-26-config-engine-and-attrs.md) | 配置引擎 + 属性管理 |
| 2026-07-26 | [2026-07-26-tuning-switch-refactor.md](2026-07/2026-07-26-tuning-switch-refactor.md) | 调律切换重构 |
| 2026-07-25 | [2026-07-25-tuning-school-exhaustive-match.md](2026-07/2026-07-25-tuning-school-exhaustive-match.md) | 调律流派判定体系重构（穷举匹配制） |
| 2026-07-24 | [2026-07-24-plugin-arch-references-docs.md](2026-07/2026-07-24-plugin-arch-references-docs.md) | 单包插件化架构重构 + 参考图管理 + 文档结构重构 |
| 2026-07-23 | [2026-07-23-equip-parser-ui-layout.md](2026-07/2026-07-23-equip-parser-ui-layout.md) | 装备解析与 UI 布局优化 |
| 2026-07-22 | [2026-07-22-tuning-rules-standardization.md](2026-07/2026-07-22-tuning-rules-standardization.md) | 调律规则标准化 |
| 2026-07-21 | [2026-07-21-project-evolution-summary.md](2026-07/2026-07-21-project-evolution-summary.md) | 项目演进总结（Phase 0~8 + 用户指令索引） |
| 2026-07-16 | [2026-07-16-workflow-stability-and-evaluation.md](2026-07/2026-07-16-workflow-stability-and-evaluation.md) | 工作流稳定性与评估体系闭环 |
| 2026-07-16 | [2026-07-16-scene-grouping.md](2026-07/2026-07-16-scene-grouping.md) | 场景分组管理功能 |
| 2026-07-16 | [2026-07-16-dsl-refactor-and-crash-protection.md](2026-07/2026-07-16-dsl-refactor-and-crash-protection.md) | DSL 去隐式化重构与崩溃防护体系 |
| 2026-07-16 | [2026-07-16-dsl-points-arrows-next-step.md](2026-07/2026-07-16-dsl-points-arrows-next-step.md) | DSL 消费 points/arrows 改造方案 |
| 2026-07-15 | [2026-07-15-scene-yaml-externalization.md](2026-07/2026-07-15-scene-yaml-externalization.md) | 场景定义 YAML 外部化、SceneRegistry、加载顺序控制 |
| 2026-07-15 | [2026-07-15-scene-class-refactor.md](2026-07/2026-07-15-scene-class-refactor.md) | 装备场景类化重构、功能按钮标记、区域复制修复 |
| 2026-07-15 | [2026-07-15-architecture-refactor.md](2026-07/2026-07-15-architecture-refactor.md) | 架构文档重构、场景标识符规范化、布局顺序管理 |
| 2026-07-15 | [2026-07-15-canvas-middle-layer.md](2026-07/2026-07-15-canvas-middle-layer.md) | 画布中间层坐标解耦 |
| 2026-07-16 | [2026-07-16-equip-model-dedup-slot.md](2026-07/2026-07-16-equip-model-dedup-slot.md) | 装备模型去 slot 依赖全链路重构 |
| 2026-07-14 | [2026-07-14-overlay-dpi.md](2026-07/2026-07-14-overlay-dpi.md) | BorderOverlay 多 DPI 跨屏定位问题 |
| 2026-07-14 | [2026-07-14-layout-hierarchy-refactor.md](2026-07/2026-07-14-layout-hierarchy-refactor.md) | 区域配置布局层级重构 |

## 日志格式

```markdown
# Dev Log: <标题>
> 日期：YYYY-MM-DD
> 涉及模块：<路径>
> 关键词：<关键词>
## 问题描述
## 排查过程
## 根因分析
## 解决方案
## 教训
```
