# 公共服务与桌面适配

- `core.update.fetch_latest_release` 提供更新结果与错误，Qt 检查线程位于 `ui/background/update.py`。
- `core.config.remote.run_sync` 提供同步服务，Qt 包装位于 `ui/background/remote_config_sync.py`。
- `core.telemetry.reporter.run_report` 返回上报结果，Qt 包装位于 `ui/background/telemetry.py`，
  桌面仍在主线程构造 job 并应用记账结果。
- `core.profile.engine.ProfileProcessor.tick` 保留周期处理、同步脚本与提醒持久化；
  `ui/profile/engine.py` 管理桌面线程、信号和生命周期。key 重命名通过无 GUI 更新通知
  发布已提交数据的变化，Qt 订阅在线程停止时解除。

业务处理不依赖 Qt；线程、信号与关闭等待由桌面层承担。
插件注册及配置策略装配见[插件系统](../30-overview/02-plugin-system.md)。
