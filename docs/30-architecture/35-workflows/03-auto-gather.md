# 采集录制与专有任务

燕云插件注入「采集」左页签和「燕云 → 采集录制」菜单，并注册 `auto_gather`。
类继承 `BaseWorkflow`、声明 `SCOPE = dedicated`，通过既有
`run_workflow_implementation` 接入执行租约、后台线程、暂停/停止和任务历史，
不进入日常/批量的默认脚本清单。不新增通用 DSL 语法或修改地图模型。采集录制器
只记录领域事件，并把路线确定性编译为现有 `press`、`click`、`wait` WF 动作预览；
它不复用原始脚本录制器，因后者会包含鼠标轨迹和无业务意义的中间输入。
页面连锁反应使用脚本开头的 `gather_map_open_wait`、`gather_target_select_wait`、
`gather_travel_prompt_wait`、`gather_collect_wait` 四个 `default` 参数，不借用
语义不同的全局 `page_refresh`。

| 组件 | 职责 |
|---|---|
| `apps/yysls/core/gather.py` | 路线、步骤、语义 WF 生成、解析与单文件原子保存 |
| `apps/yysls/core/gather_recorder.py` | F1 当前鼠标坐标换算 |
| `apps/yysls/ui/gather.py` | F1/F2 状态机、WF 预览、采集页、启动快照 |
| `apps/yysls/workflows/implementations/auto_gather.py` | 带视口和到达检查的专有回放状态机 |
| `scenes/map_gather.yaml` / 布局 | 地图范围、识途按钮、可选确认框、主页及运动检查区域 |

录制状态流：F1 读取鼠标坐标 → 第一次 F2 发送 V/F 并开始计时 → 第二次 F2
结束计时并封存采集点。回放状态流：检查地图 → 核验视口并点击 → 发送 V/F
→ 等待录制耗时并确认主页 → 触发采集键 → 等待动作 → 下一点重新开图。
运行快照包括路线、参数和布局；用户及环境沿用宿主启动快照。异常保留部分结构化事件，
交给宿主标记任务失败；停止通过基类 `_BreakSignal` 正常退出。

路线以标准 DSL 保存于 `config/local/workflows/gather/<稳定ID>.wf`，文件是唯一事实来源，
可以由脚本编辑器打开并直接执行。保存时比较该文件的旧版本并原子替换；不同路线互不覆盖。
编辑器选择、
采集页当前选择和执行用户选择均不反写路线。运行状态及实测耗时不覆盖持久化路线。

默认到达识别仍是实验性启发式；已对提供的静态截图完成 OCR 核对，
尚不能替代真机导航验收。采集产出检测、视口平移校正、智能模式及循环尚未实现。
