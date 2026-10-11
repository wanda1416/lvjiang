# 配置升级调整

以下说明对应开发分支的旧配置入口清理；正式版本是否包含这些变更，请看对应发布说明。
没有旧格式时无需处理。调整前备份配置，不要删除整个 local 目录或布局目录。
本次加载不会自动改写旧文件。

| 提示来源 | 如何调整 |
|---|---|
| app.yaml 顶层 android_apps | 将应用条目放到 apps 下，安卓字段放在条目的 android 下，窗口绑定放在 windows 下；核对现有条目后移除旧顶层键。内部同名 Python 模型无需修改。 |
| scenes.yaml v1 | 将 layout_scenes 中每个分组的清单移到 scenes.<分组>.items；group_names 移到对应 name。保留 __added__/__removed__/__order__ 增量，完整文件设 schema_version: 2；增量覆盖可省略版本。 |
| 布局场景 JSON 集中 disabled | 将 region/point/arrow/panel 清单里的停用状态放到对应 regions/points/arrows/panels 实例，写 disabled: true，然后移除集中段。已有坐标必须保留；没有实例的停用项应在原版本保存或依据对应场景定义重新配置，不要随意补零坐标。未调整的场景会被跳过并记录文件路径。 |
| levels_and_seasons.yaml | 备份后在原配置层手工改名为 seasons.yaml。若 seasons.yaml 已存在，核对合并等级与赛季设置后再移除旧文件，不能直接覆盖。官方文件应由配置发布方修正；用户只修改自己的 local 覆盖。 |
| tune_config.yaml 的 tuning_rules/base_rules | 在对应 tuning_rules/base_groups 规则文件设置 disabled/order，核对后移除旧声明。用户停用系统调律规则仍可使用 local 停用桩；不要复制整份系统规则。 |
| batch.json v2 的 selected_* | 将意图作为默认值的字段改为 default_task_ids/default_usernames/default_units；已有当前字段时先核对，不直接覆盖。v1 仍会自动一次性转为 v2，不要为了触发兼容而把 v2 版本号改成 1。 |

也可在清理前版本打开对应编辑器，明确保存所有受影响的配置，再升级并检查文件格式。
只保存部分场景不能保证其他旧场景已更新。

Profile DB、插件会话、界面状态与批量 v1 的必要迁移仍保留，不需要手工清理这些数据。

## 开发分支的在线配置更新

新协议面向尚未正式发布的 0.15.0。官方预置仍随应用提供，首次启动保存原始 r0，
不需要联网才能启动；公开修正和授权会员配置下载成功后立即生效，无须重启。
正在运行的任务继续使用启动快照，后续任务使用新配置。

配置仍只有 local 与 system 两层。你修改过的 local 优先于官方 system，更新器不会
覆盖 local、Session 或用户数据库。官方修正没有改变某项设置时，先检查对应 local 覆盖。

updates 只存原始基底、下载归档和安装状态，不是第三个配置加载层。应用依据在线索引
选择公开累计包，再合并有效会员包；不按本地最大编号选择。发布者撤销更新后，下次
成功检查索引可恢复较早修订或预置；断网或下载失败不会被当成撤销。

正常使用不要手工删除 r0 或修改更新状态。极端恢复时关闭所有实例，将当前版本的
updates/vX.Y.Z/r0.zip 解压到 system，ZIP 根不再嵌套 system 目录；local 不用删除。
已被更新的 system 不能用来重建丢失的原始 r0，需要恢复可信预置。若要持续使用预置，
关闭“接收在线配置更新”。手机本轮仍由 PC 单向同步 system，不主动在线更新。
