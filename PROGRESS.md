# PROGRESS —— 跨对话进度快照

> 用途：开启新对话时先读本文件与 TODO.md。

## 一、当前状态

- 主线功能全部就绪：自动调律端到端流水线（背包遍历 → 潜力判定 →
  实际调律 → 终局判定 → 调律说明文档）可用。
- pytest **777 例全绿**（2026-07-30 本地全量验证）。
- 全部改动已提交并推送至 `origin/master`（最新 `cb734e9`）。
- 严禁自动提交；提交时中文信息须写入 UTF-8 文件后 `git commit -F`。

## 二、最近完成（2026-07-26 ~ 07-30，开发日志待补，见 TODO 4）

- **调律规则开关化重构**（07-29 `6db9b32`）：`keep_pvp` 专用语义废弃，
  改为通用开关机制（tuning_base.yaml `switches` 注册表 + 条件组 `when`
  前提）；评级四档定名 垃圾/一般/优秀/顶级（`Rating.USABLE`→`NORMAL`）；
  条件原语收敛为 4 个（contains_all/not_together/count_max/count_min，
  均支持 include_first）；规则级/部位级 `default_rating` 兜底。
  方案定稿文档：docs/40-development/2026-07/2026-07-26-tuning-switch-refactor.md。
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

## 三、特别注意事项（下一对话必读）

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
