# 配置升级调整（0.13.15 → 0.15.0，已归档）

> 状态止于 2026-11-18。本文是 0.15.0 交付时的一次性升级指引，**当前版本不再需要**：
> 0.15.x 已正式交付多轮，面向 0.13.14 及更早格式的迁移不再提供。
> 原文保留作追溯用，其中的路径、版本号与调整步骤只对当时有效。
>
> 现役入口：[用户指南 · 在线配置、会员内容与功能激活](../../60-guide/01-accounts/01-appendix-a-online-config.md)
> （升级前的备份要求、便携包与测试版口径在该文第六节）；发布索引口径见
> [发布说明](../../50-releases/README.md)。

---

0.15.0 推荐从 0.13.15 升级，以该版本正常保存的配置为基线；不再提供面向 0.13.14
及更早格式的配置迁移。更早版本请先升级并正常运行 0.13.15，在对应编辑器保存旧自定义配置。
没有旧格式时无需处理。调整前备份配置，不要删除整个 local 目录或布局目录。
本次加载不会自动改写旧文件。

| 提示来源 | 如何调整 |
|---|---|
| app.yaml 顶层 android_apps | 将应用条目放到 apps 下，安卓字段放在条目的 android 下，窗口绑定放在 windows 下；核对现有条目后移除旧顶层键。内部同名 Python 模型无需修改。 |
| scenes.yaml / layouts.yaml（聚合索引） | 启动时自动拆分为目录化结构（分组/布局定义文件 + 同名子目录 + `_meta.json` 顺序），无需手工调整；迁移日志为「[迁移] 检测到旧聚合配置，开始拆分」。自动迁移失败时按日志提示处理后重试，不要手工半改。 |
| scenes.yaml v1 | 将 layout_scenes 中每个分组的清单移到 scenes.<分组>.items；group_names 移到对应 name。保留 __added__/__removed__/__order__ 增量，完整文件设 schema_version: 2；增量覆盖可省略版本。 |
| 布局场景 JSON 集中 disabled | 将 region/point/arrow/panel 清单里的停用状态放到对应 regions/points/arrows/panels 实例，写 disabled: true，然后移除集中段。已有坐标必须保留；没有实例的停用项应在原版本保存或依据对应场景定义重新配置，不要随意补零坐标。未调整的场景会被跳过并记录文件路径。 |
| levels_and_seasons.yaml | 备份后在原配置层手工改名为 seasons.yaml。若 seasons.yaml 已存在，核对合并等级与赛季设置后再移除旧文件，不能直接覆盖。官方文件应由配置发布方修正；用户只修改自己的 local 覆盖。 |
| tune_config.yaml 的 tuning_rules/base_rules | 在对应 tuning_rules/base_groups 规则文件设置 disabled/order，核对后移除旧声明。用户停用系统调律规则仍可使用 local 停用桩；不要复制整份系统规则。 |
| batch.json v2 的 selected_* | 将意图作为默认值的字段改为 default_task_ids/default_usernames/default_units；已有当前字段时先核对，不直接覆盖。v1 不再自动转换，请先在 0.13.15 保存为 v2。 |
| OCR 旧文件或扁平规则 | 自定义规则合入 `ocr.yaml` 的 `normalization.default/groups`；`ocr_rules.yaml` 不再读取或下发。 |
| 调律规则 max_pct/max_rating | 改用 pct_op/pct 和 ratings；原 max_rating 表示该档及以下，请完整列出档位。 |
| 英文部位值、纯字符串武器类型 | 部位使用界面保存的中文领域值，武器类型使用含 name 的对象；可在 0.13.15 编辑器重新保存。 |
| 等级能力缺失 | 在等级配置中明确填写 allow_chengyin、allow_retransfer、allow_retransfer_after_chengyin、reset_no_refund 布尔值，不再按等级猜测。 |

也可在清理前版本打开对应编辑器，明确保存所有受影响的配置，再升级并检查文件格式。
只保存部分场景不能保证其他旧场景已更新。

Profile、任务历史和调律历史数据库保留现有建表与版本管理，不需要手工清理。
燕云会话使用 0.13.15 已有的 `config/session/yysls` 独立文件，不再搬运旧 `session.json.yysls`。
普通词条承音上限缺省按游戏固定 94% 比例计算，显式值优先；定音词条仍使用完整上限。

## 测试与预发布版本

测试版以 bN 结尾（如 0.15.0b1），预发布候选以 rcN 结尾（如 0.15.0rc1）。
普通“检查更新”只提供正式版；需要测试时使用明确的测试发布链接手动下载。
正式版 0.15.0 高于同版本测试与候选包，因此手动安装测试版后仍可收到正式升级提示。
便携包仍需解压到新目录；手机升级使用相同官方签名。
