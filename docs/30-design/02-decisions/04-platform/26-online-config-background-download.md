# 在线配置后台下载与下次启动安装

> 状态：已实现。

## 边界

在线配置的联网下载移出启动同步段，放到后台线程；安装时机不变，仍然只在下次启动、
加载插件与场景之前。

- 启动同步段只保留 `recover_installation()`（回滚未提交事务、撤下失效会员层），
  只动本地文件，不联网。
- 下载与安装分离：后台线程只下载候选并登记暂存；唯一安装点是
  `prepare_startup_configuration()`，必须在加载插件与场景注册表之前。
- 运行中的进程永不安装、永不替换 `config/system`：安装会让正在跑的任务读到半新半旧的内容。
- 后台下载不参与任何 UI 刷新，不重建场景注册表，不刷新 OCR、模板或插件缓存。
- 下载失败只记日志，不弹窗、不阻断启动，也不改变「保留当前磁盘配置」的语义。
- 不做下载重试队列、不做运行中安装，也不提供「立即安装」按钮：下一次启动自然会再试。

## 暂存与安装

- 候选登记在 `UpdateStore` 的 `pending`（`mark_pending()`）；`prune_archives()` 据此保活，
  未登记的候选会在下次启动被当成垃圾删掉，等于白下载。
- 归档按 `r{revision}/{manifest_sha256}` 寻址，同修订重发不同内容不互相覆盖。
- 安装阶段只读暂存并校验：`pending_public()` / `pending_member()` 在文件缺失或清单摘要
  与登记值不符时返回 `None`，安装段跳过离线安装、回落联网判断，磁盘上的现有配置不动。
- 会员包暂存只留密文，安装阶段重新取一次授权解密，明文不落盘。

## 线程生命周期

- 单线程，`daemon=True`，模块级 `stop` 事件；必须在拿到单实例锁、确认自己是主实例之后启动，
  否则第二个实例也在下载。
- 退出挂在 `QApplication.aboutToQuit`：`stop_event.set()` 后 `join` 至多约 2 秒，超时放弃等待。
- 写入一律「临时文件 + `os.replace`」，线程被强杀最多留下 `.tmp`，下次启动清理。

## 可见性

- 设置页的在线配置一栏显示两种状态之一：已下载待安装的修订号（「下次启动生效」），
  或本次未获取到更新。
- 日志给出可行动的结论：下载了什么修订、是否命中本地已有修订、失败原因（不打印整包 JSON）。

## 契约

1. 能下就下：下载完成即登记 `pending`，本次不安装；下次启动读配置前安装。
2. 启动时校验：暂存存在且清单摘要与归档校验通过才安装，安装完成前不加载插件与场景。
3. 摘要不符、缺失或损坏即跳过离线安装并回落联网判断，不报错给用户。
4. 连续启动两次，`prune_archives()` 不回收未安装候选。
5. 运行中的任务不会因后台下载或安装读到混合配置。

## 落点

- `core/config/remote.py`：后台线程、暂存登记、离线安装。
- `core/config/update_store.py`：`pending` 登记、公开包暂存、`prune_archives()` 保活、
  安装前摘要校验。
- `core/config/member_download.py`：暂存密文的授权解密。
- `app.py`：窗口出现后启动后台同步，`aboutToQuit` 先停线程再等 Qt 清理。
- 回归：`tests/core/config/test_config_update_store.py`、`test_update_sync_channels.py`、
  `test_startup_configuration.py`、`test_member_staged_install.py`、
  `tests/ui/settings/test_settings_remote_config.py`。
