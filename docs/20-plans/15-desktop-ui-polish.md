# 桌面端高密度 UI 微规范收口与可读性改进计划

> 状态：未开始（待排期）。

## 1. 背景与当前已落档基线

本计划承接桌面端三主题系统与语义色收敛的后续打磨。截至本计划建立时，已完成并落档/落地的基础如下：

### 1.1 `design/` 与 `guide/` 已落档内容

- **设计决策（`30-design`）**：[31-ui-theme-system.md](../30-design/02-decisions/04-platform/31-ui-theme-system.md) 已记录桌面端三主题（`light` 纯白 / `paper` 宣纸 / `dark` 玄墨）取舍、固定 `Fusion` 风格、与 Web v2 令牌同源微调、通用/插件两层单一来源边界，以及 Windows DWM 原生标题栏/边框同步策略。
- **用户指南（`60-guide`）**：[02-main-interface.md](../60-guide/04-desktop/02-main-interface.md) 已更新右上角三主题下拉切换与 Windows 原生标题栏同步说明。

### 1.2 `reference/` 状态与代码已落地能力

- **接口契约（`40-reference`）**：当前 `40-reference/` 仅收录配置、DSL、Profile 与同步四类数据/协议契约，**暂无**桌面 UI 主题与控件契约（当前由 `src/lvjiang/ui/widgets/theme.py`、`layout_helpers.py` 与离屏测试 `tests/ui/widgets/test_theme.py` 作为权威实现）。
- **代码已落地（尚未单独升格为 `reference/`，部分细节待后续并入 `31-ui-theme-system.md`）**：
  - 全站硬编码 `#c62828` / `#2e7d32` / `#d32f2f` 已统一收口到 `current_tokens()` 或 `tone` 属性；
  - 主执行按钮 7 档已按 `is_dark` 适配玄墨色板；
  - 调律总览卡片已支持 `refresh_theme()` 原地热刷新；
  - `layout_helpers.py` 已提供 `set_status_tone()`（`muted` / `warning` / `danger` / `success`）与 `add_dialog_footer()`，并已迁移游戏设置三面板、袅袅之音、脚本编辑器、账户管理、调律规则弹窗、小表单弹窗与 `scene_editor`。

---

## 2. 剩余待办范围

### 2.1 真机桌面视觉巡检（人工验收）

在真实 Windows 桌面环境下切遍「纯白 ↔ 宣纸 ↔ 玄墨」，确认以下 5 项视觉表现无回退：

1. 顶部主题下拉与多语言 tooltip、Windows 原生标题栏/边框同步变色与重启记忆。
2. 主执行按钮 7 档在玄墨下的对比度与质感，以及宣纸下强调色/警示色区分。
3. 调律进度与调律装备总览弹窗在打开状态下切主题时的原地热刷新（不闪烁、不丢滚动位置）。
4. 常驻编辑框（游戏设置、调律配置、袅袅之音、脚本编辑）的未保存/报错/成功状态字在玄墨下的对比度与实时刷新。
5. 账户管理、场景管理与通用小表单弹窗的左对齐错误提示与右下角按钮对齐。

### 2.2 高密度界面“文字减压”与高 DPI / 窄屏弹性

1. **长说明收纳**：梳理高密度表单与设置页中直接铺在正文里的长段说明（如方案锁定、智能调律规则、脚本只读提示等），正文仅保留简短操作标签，补充解释收敛到 `?` 悬浮提示或可折叠区域。
2. **高 DPI 与最小尺寸弹性**：针对 1080p 125% / 150% 系统缩放及较小窗口宽度，抽查配装计算器、规则编辑器、调律总览与主窗口左右分栏的换行、滚动区与 `AutoWidthComboBox` 空间弹性，避免横向挤压或截断。

### 2.3 剩余横幅与存量模态弹窗收敛

1. **提示横幅统一到 `status` 属性**：盘点仍手动拼接带背景色 QSS 的区块提示，统一改用全局 QSS 的 `status="info"|"warning"|"danger"|"success"` 动态属性。
2. **存量弹窗底部栏统一**：其余未迁移的模态弹窗在后续修改触及时，顺手改用 `add_dialog_footer()` 对齐按钮顺序、间距与左侧状态位；若后续需要跨模块稳定约定，再将 `tone`/`status` 与底部栏规则补充进 `31-ui-theme-system.md`（或视需要沉淀至 `40-reference/`）。

### 2.4 （可选）既有 i18n 反馈文案补齐

- 补齐 `src/lvjiang/ui/main/menu_ops.py` 中既有与主题无关的 2 条缺失译文（`'待受理反馈 ({count})'` 与 `'控制台有 {count} 条待受理反馈，点击打开网页管理控制台'`），使 `tests/core/test_i18n_literal_coverage.py` 在全部 12 种语言下通过。

---

## 3. 验收标准

- 视觉巡检覆盖三套主题且在玄墨下所有状态文字对比度满足可读性要求（无暗底深红/深绿）；
- 修改涉及的模块通过 `.venv` 下 `ruff check`、`mypy` 与对应离屏 UI `pytest` 用例；
- 若文档随实现演进，按规范用 `git mv` 迁入 `docs/30-design/` 并同步 `scripts/docs_check.py` 零告警。
