# Android 批量执行架构

公共 batch_scheduler 负责冻结计划与串行编排，使用可连接的事件出口。Qt BatchWorker 仅提供 QThread 和 Qt 信号；手机适配器使用 threading.Thread、设备引擎后端及既有任务控制状态。BatchReport 同属公共层。

batch_config 是配置组定义的唯一存储，batch_run 持有按组隔离的运行草稿。ondevice.batch 提供 JSON 页面、草稿、准备和启动接口；准备令牌依据定义、草稿、用户与参数快照，启动重新核对。业务参数由公共任务参数合并规则产生。Kotlin 只展示和传递用户动作，不维护另一套调度。

工作台、悬浮窗和代理服务读取同一个 task_runner 状态；batch 扩展字段携带当前条目任务和进度明细。整批历史复用 daily_history 的 batch_runs/task_runs 关联。

Android 应用控制复用期望方向和稳定帧等待。设备端截图记录方向，手势前校验同一方向；应用生命周期清除引擎识别快照。旋转后的悬浮窗口按新屏幕范围重新布局。

配置组编辑器与本次运行草稿独立；保存单个配置组以最新定义令牌校验并合并，不覆盖其他组。删除只清除所属草稿，并修复指向该组的活动引用。同步选中「保留手机任务参数与批量配置」时保留手机整份批量定义；取消则使用电脑定义，本次运行草稿仍保留并按新定义协调。

`BatchConfigActivity` 编辑定义与生命周期参数，草稿独立于任务页当前组。JSON 编辑接口使用单组内容令牌，保存在 `mutate_batch_config` 锁内再次核对并合并；删除只移除自己的运行草稿。Profile 排序的值读取与稳定排序进入 `core.batch_run.profile_user_order`，桌面和手机共用；执行排序只保存本次草稿。
