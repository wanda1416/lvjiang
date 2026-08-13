# TODO

> 已完成/废弃条目定期清出本文件，历史见 git 与 docs/40-development/。
> 开启新对话时先读本文件。

---

## 功能待办

1. **装备分析流程（只扫不调）**：独立工作流，遍历背包 → OCR →
   评级/潜力判定 → 输出结构化报告（值得调律/垃圾胚子/词条已满
   三类清单），不做任何点击调律操作。供用户「先看看再动手」，
   也是后续统计报表的数据源。
2. **统计报表面板**：调律/分析结束后的数据汇总视图（处理装备数、
   评级分布、材料消耗、狗粮投入），与叙事型调律说明文档互补。
   UI 目前仅 user_manager_dialog 有「数据统计」预留卡片（占位文案
   "装备数据展示功能开发中..."），面板本体未做；数据源可复用
   auto_tuning 的 output["tuning_reports"]。
3. **转律 / 装上执行**：当前转律仅用于评级模拟（judge 预测潜力），
   无真实点击转律的工作流；毕业装备替换穿戴（装上）亦未做。

---

## 当前状态

- 主线功能全部就绪：自动调律端到端流水线（背包遍历 → 潜力判定 →
  实际调律 → 终局判定 → 调律说明文档）可用。
- pytest **1120 例全绿**（2026-08-02 本地全量验证）。
- 全部改动已提交；是否推送以 /submit 指令为准。

---

## 最近完成（2026-08-02）

- **场景编辑器增量保存**：per-scene dirty 追踪，修改单个场景只写该场景 JSON，
  Tab 标题绿点指示变更，discard 提示列出变更场景名称。
- **画布保存修复**：保存时从当前激活 Tab 获取 canvas 配置，而非字典第一个 Tab。
- **画布尺寸实时刷新**：调整画布时顶部信息栏同步更新。
- **场景/分组重命名**：右键重命名场景或分组，联动 layouts 文件名、截图文件、
  scenes.yaml 配置。
- **批量 Tab 三页子 Tab 重构**：脚本配置、用户配置、条目概览分离。
- **关于对话框**：版本信息、GitHub Release 检查更新、版权信息。
- **打包版本注入**：package.bat 从 pyproject.toml 读取版本注入 _version.py。

## 最近完成（2026-07-31 ~ 08-01）

- **布局存储目录化**（`2856ce5`）：单文件 `默认布局.json`（2001 行）
  拆为 `layouts.yaml`（名册 + canvas 内联）+ `layouts/{布局名}/{场景}.json`
  （每场景独立实体文件），沿用 ConfigResolver 双层「分离写 / 合并读」语义；
  Android `syncSystemConfig` 整目录同步自动适配。
- **重置调律语义修正**（07-31 `9efc158` → 08-01 `4a26c12`）：冷却期 OCR
  三态检查（硬限单件一次）、已满装备走 scan 规则处理、品阶选项统一、
  非首外功攻击改硬门槛（缺大外/小外能用封顶 `1eb5f3f`）。
- **工作流引擎静态预检**（07-31 `219e7c0`）：新增 `validate_only` 预检 +
  系统工作流引用门禁与上机预检脚本，跑脚本前校验区域是否已在布局绑定。
- **Android 独立执行端**（07-30 ~ 08-01）：三通道 PoC 闭环、系统配置随 APK
  分发、设备端工作流引擎装配、原生调律参数配置页（`5d51117`）、悬浮层
  主题修复游戏不再被 LMK 击杀（`92fcb8e`）、release 实机复验通过。
- **打包分发**（07-31 `d415aa6`）：PyInstaller onedir 一键打包（launcher +
  spec + package.bat），内置 adb 随包分发（`fd45daf`），用户免装 platform-tools。
- **调律规则引擎演进**（07-31）：判定语义下沉到逐条处置规则（`282809f`）、
  顶档/普通条件结构演进 contains_all 组合条件（`934fbab`）、仅首词条逐规则化
  + 首词条方向比较（`54fb43e`）。
- **质量门禁**（07-31 `b5d1de0`）：接入 ruff + mypy，修掉全仓存量告警。
- **平台适配**（07-31 `309bb04`）：抽离 `core/platforms.py` 平台适配层；
  macOS 支持 Phase 0（依赖验证 + 退出崩溃修复 `2e72b30`）。

## 较早完成（2026-07-26 ~ 07-30）

- **调律规则开关化重构**（07-29 `6db9b32`）：`keep_pvp` 专用语义废弃，
  改为通用开关机制（tuning_base.yaml `switches` 注册表 + 条件组 `when`
  前提）；评级四档定名 垃圾/一般/优秀/顶级（`Rating.USABLE`→`NORMAL`）；
  条件原语收敛为 4 个（contains_all/not_together/count_max/count_min，
  均支持 include_first）；规则级/部位级 `default_rating` 兜底。
- **属攻词条双重身份匹配**（07-30 `a2e3467`）：非武器部位具体属攻以
  字面名 + 动态词条（最大/最小本属攻击、最大/最小外属攻击）双重身份
  参与匹配；武器部位仍引用字面无相。
- **狗粮规则引擎重构**（07-30 `276b916`）：有序规则表替代品阶映射；
  配套调律材料策略配置与规则模型扩展（`c422323`）、材料区同名幽灵槽
  修复（`6f8ddee`）。
- **自动调律链路补全**（07-27 `d380546` 等）：背包遍历策略化
  （bag_traversal 包：dedup 滑动窗口去重为默认 / positional 位置对齐）、
  整行列遍历、指纹漂移容错、狗粮返还二次弹窗兼容、F10 停止输出部分
  结果、「跳过实际调律」测试开关。
- **调律说明文档**（07-28 `ca9bbbe` → 07-30 `cb734e9`）：叙事型 Markdown
  输出至 logs/tuning/，结尾含成品清单。
- **UI 插件化注入架构**（07-29 `30534d2`）：插件通过注入点挂载 UI；
  配套 loguru 落盘与崩溃防护安装位置修正（`3c1f4b5`）。
- **崩溃根治**（07-30）：pynput 退出竞态（`b754132`）、
  UnhandledExceptionFilter 回调防 GC（`48e9ab4`）。
- **三层术语模型重命名**（07-28 `ad37a0e`）：流派/玩法/调律规则全面统一；
  四个主体大文件拆为分层/Mixin 包（`5b5e3d3`）、DSL 引擎拆 engine/ 包
  （`29c2121`）。
- **CI**（07-28 `a75ecdd`）：GitHub Actions windows-latest + 离屏 pytest。
- **品阶门槛按部位锁死 + 规则级覆盖**（07-29 `92d5505`）。

---

## 特别注意事项（下一对话必读）

1. **严禁自动提交**；提交时中文信息须写入 UTF-8 文件后 `git commit -F`。
2. 运行环境：用**全局 python**（.venv 缺 yaml）；pytest 加 `-p no:cacheprovider`
   可避开沙箱对 .pytest_cache 的权限报错；PowerShell 写中文用 `python -X utf8`。
   注意：pytest -q 在本机不打印 "N passed" 汇总行，以进度 100% 且无 F/E 为准。
3. **SearchReplace 陷阱**：任何含 `───` U+2500 制表线的行做锚点必失败，
   用纯代码锚点或（文件 <1000 行时）Write 整文件重写。
4. **Qt 样式级联坑**：容器 QFrame 的 setStyleSheet 必须用 `QFrame#objectName` 选择器，
   否则级联到 QListWidget/QTableWidget（均继承 QFrame）抹掉其边框背景，看似"布局错乱"。
   排查布局问题先离屏 `widget.grab().save()` 截图确认实际渲染。
5. 三面板（base_attr/affix_caps/school）各自模块级 `_ATTRS_PATH`（相对路径
   `config/system/yysls/attributes.yaml`），全量加载→改动→yaml.dump 全量写盘
   （allow_unicode、sort_keys=False）；UI 测试用 tmp 副本 + monkeypatch 三处 `_ATTRS_PATH`。
6. school_panel 信号重入：`_refresh_list` 中 `clear()` 会嵌套触发 `_on_school_changed`，
   后者用 `prev_loading` 保存/恢复 `_loading` 标志，改动时勿破坏此约定。
7. 用户约定：开发期配置重构**不写迁移兼容代码**（旧 schema 直接废弃）。
8. 提交推送遵循 /submit 一次性语义：完成后不自动 commit/push，等用户指令。
