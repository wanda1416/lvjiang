# 开发日志 2026-08-13（十一）

> 接续本日《装备解析器与材料/背包识别多处修复》。
> 本轮主题：**江湖工作流新增、通知告警系统、国际化框架、关于对话框重构**。

---

## 一、江湖工作流

- 东方第一枝（看报）落地；心得场景改名 `xinde_exchange→training_xinde`（`fe78be5`）；
- 心法购买工作流重构与场景重命名（`88a2046`）；
- 自动购买心法工作流（`4f37b1c`）；
- 新增 `purchase_bugan` 自动购买不肝工作流——首页→菜单→不肝→不肝商店视图，依次处理战斗养成（两个 2×4 商品面板整面板 OCR 扫描）等多个类目（`e23c7e8`）；
- `purchase_bugan` 简化赛季/外观购买逻辑（`7b301a7`）；
- 登录流程增加主页检测与活动弹窗关闭逻辑（`b38aeea`）；
- 江湖号令工作流重构与场景配置更新（`1fcfafd`）；
- 新增每日自动签到工作流 `daily_checkin.wf`（`5c35346`）；
- 重命名 `activity_jianghu.wf` → `daily_jianghu.wf` 并修复测试引用（`196be80`）；
- `daily_jianghu.wf` 轮询替换 `wait stable 10`（`a9a5143`）；
- 江湖号令看报流程优化（`76bb8d7`，同一提交另含场景编辑器视图下拉框同步修复，见场景编辑器篇）；
- 江湖号令情境保存前衣柜页面检测——`goto_qingjing` 后检测是否在衣柜页面（识别"穿搭"），若是则导航回情境编辑页面再保存（`1c7a85f`，同一提交另含材料数量解析修复，见识别修复篇）；
- 补充 `purchase_xinfa.wf` 流程结构注释（`966f7f6`）；
- 江湖工作流 goto 优化——`activity_jianghu.wf` 找到看报后 goto 跳出双重循环（`380ec76`，同一提交另含批处理模块 lint 修复，见批处理框架篇）。

## 二、通知告警系统

- 公共告警通知系统 + DSL notify 双重通知——新增 `AlertPanel` 组件在主页面展示告警，`session.py` 新增告警存储函数（`add_alert`/`dismiss_alert`/`get_alerts`，`mutate_node` 原子操作），DSL `notify` 实现 `native_notify` 弹窗 + 告警面板双重通知（`84dd0c5`）；
- 告警持久化下沉引擎侧——`ProfileEngine` 直接调用 `add_alert` 持久化，信号仅通知 UI 刷新；`AlertPanel` 新增 `refresh()`；导航按钮启用状态修复（QTimer 延迟初始化绕开 `blockSignals` + `user_changed` 信号兜底）（`f10879c`）；
- 双阈值告警系统（橙色/红色）——`RegenKeyDef.alert_above → alert_orange + alert_red`，`profile_engine` 双阈值独立告警 + 滞回清理，新增 `unmark_alert`，`settings_dialog` 单阈值改双阈值 spinbox，着色按 red > orange 优先级判断（`28c7d70`，场景编辑器分组删除修复部分见场景编辑器篇）。

## 三、国际化框架

- 实现国际化（i18n）框架，支持中英文切换——新增 `i18n` 模块提供 `tr()` 翻译函数和语言切换 API，创建翻译文件 `zh_CN.yaml`/`en_US.yaml`（各 720 条），`UserConfig` 新增 `language` 字段支持持久化（`fbe52ae`）；
- 国际化 P0-P3 阶段完成——P0：454 条 UI 标签/tooltip/消息框 `tr()` 包裹 + 287 条翻译；P1：55 条错误消息 + 43 条翻译；P2：636 条其他用户可见文本 + 378 条翻译；P3：77 条剩余字符串 + 110 条翻译（`fbfb788`）。

## 四、关于对话框与更新检查

- 关于对话框重构 + 打包版本注入机制——新增 `about_dialog.py`（版本信息、检查更新 GitHub Release、版权信息），新增 `inject_version.py`（打包时从 `pyproject.toml` 读取版本写入 `_version.py`），修复 PyInstaller spec 路径问题与 `package.bat` 中文编码问题（`fe4fd59`）；
- 关于对话框 QUrl 类型错误修复 + 主窗口标题跟随版本号（`07c7643`）；
- 窗口标题版本号改为动态读取 `_version.py`（`3c39bf1`）；
- 更新对话框「退出」按钮改为「继续使用」，不再退出应用（`b11587e`）；
- 帮助菜单新增检查更新、文档、反馈功能（`b0da329`）；
- 启动时自动检查更新 + 跳过版本功能（`ac6403f`）。

---

## 结果

- 新增/重构江湖类工作流约 10 个，涵盖看报、心法购买、签到、不肝购买等；
- 告警系统从无到有并升级为双阈值；
- 国际化框架首次落地，累计约 1500 条文本完成 P0-P3 阶段翻译包裹；
- 本篇 commit 约 20 个。

---

## 关键设计决策（用户确认）

1. **告警持久化下沉到引擎侧**：由 `ProfileEngine` 直接调用 `add_alert`，UI 仅负责刷新，避免信号链路与持久化耦合。
2. **双阈值告警（橙色/红色）**：单一阈值不足以区分提醒紧急程度，改为橙/红两级 + 滞回清理。
3. **i18n 采用 YAML 词表 + `tr()` 包裹**：全量 UI 文本分 P0-P3 阶段逐步包裹，翻译文件与代码解耦。
