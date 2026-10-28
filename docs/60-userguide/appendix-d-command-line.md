# 附录 D：命令行执行脚本

律匠的图形界面不是唯一入口。安装目录里的 `lvjiang-cli.exe`（源码环境用
`python -m lvjiang`）可以在不打开界面的情况下列出脚本、查看参数并跑完一次任务，
适合计划任务、外部编排或批量脚本调用。

命令行入口和执行器共用同一套引擎、配置快照与用户锁，不是另一套实现。

---

## 1. 两个动作

```powershell
# 列出当前可运行的脚本
lvjiang-cli --list

# 查看某个脚本的参数定义
lvjiang-cli --list --id haoyun_lianji

# 运行一次
lvjiang-cli --exec --id haoyun_lianji --user 蔡元君 --target adb --yes
```

源码检出下等价写法：

```powershell
$env:PYTHONPATH="src"
python -m lvjiang --list
```

---

## 2. 执行（--exec）的常用参数

| 参数 | 说明 |
|---|---|
| `--id ID` | 按脚本 id 精确指定 |
| `--name NAME` | 按显示名指定；重名时改用 `--id` |
| `--file PATH` | 直接执行任意位置的 `.wf` 文件 |
| `--user NAME` | 执行用户，默认取界面当前用户 |
| `--target auto\|adb\|adb:<serial>` | 执行目标；`auto` 取第一台已连接设备 |
| `-p, --param K=V` | 覆盖脚本参数，可重复；按参数定义自动转成数字/布尔/选项 |
| `--params-json JSON` | 以 JSON 对象批量提供参数，`--param` 优先 |
| `--layout NAME` | 布局 key，默认 `android` |
| `--timeout 秒` | 到时自动请求停止；默认不限 |
| `--yes` | `confirm()` 一律返回是；不加时返回否 |
| `--dry-run` | 只做静态预检（语法、场景绑定、等待参数），不连接设备 |
| `--json` | 标准输出只打印一行 JSON 结果，便于外部解析 |
| `--result PATH` | 额外把整份结果写到指定文件 |
| `--quiet` | 控制台只保留错误，日志文件仍完整 |

参数示例：

```powershell
lvjiang-cli --exec --id haoyun_lianji --user 蔡元君 --param max_rolls=10 --json
```

未在脚本里声明的参数会直接报错，而不是被静默忽略。

---

## 3. 退出码

| 码 | 含义 |
|---|---|
| 0 | 执行完成 |
| 2 | 参数错误（含 `--params-json` 非法、未声明参数） |
| 3 | 脚本或工作流文件不存在 |
| 4 | 环境问题（无设备、布局缺失、用户不存在、用户/设备被占用） |
| 5 | 运行异常或预检失败 |
| 6 | 运行被中断（Ctrl+C 或超时） |

`--json` 时标准输出形如：

```json
{"ok": true, "code": 0, "script": "haoyun_lianji", "name": "好运连叽摇骰子",
 "user": "蔡元君", "target": "192.168.0.100:5555", "layout": "android",
 "started_at": "2026-10-29T05:42:07.224+08:00", "duration_ms": 18200,
 "result_path": "…/output/蔡元君/2026-10/29/haoyun_lianji_….json",
 "result": {}, "error": ""}
```

失败时同样输出一行 JSON（`ok` 为 false，并带上退出码与错误文本），调用方不需要
同时解析日志。标准输出固定 UTF-8；日志仍写 `logs/`，不受 `--json` 影响。

---

## 4. 行为约定

- **无人值守**：脚本里的 `pause` 会终止本次运行（退出码 5），不会弹出等待点击的窗口；
  `confirm` 只有加 `--yes` 才返回是；`input` 返回空并在日志中提示。
- **用户锁**：同一用户同一时间只能有一个执行进程，和界面共用同一把锁。
  被占用时直接以退出码 4 结束，不会排队。
- **配置快照**：任务启动时冻结配置，运行中即使界面安装在线配置也不影响本次运行。
- **结果位置**：与界面执行完全一致，写在
  `config/session/output/<用户>/<年-月>/<日>/<脚本>_<时间>.json`，同时写入任务历史。

---

## 5. 当前限制

- 支持 DSL 脚本（`.wf`）；内置类实现的脚本请在界面启动，命令行会明确拒绝。
- 目标只支持 ADB 设备；桌面窗口目标尚未开放。
- 同一台设备不要同时跑界面任务与命令行任务，设备侧手势会互相干扰。

---

## 6. 打包产物

安装包与便携 ZIP 都会包含 `lvjiang.exe` 和 `lvjiang-cli.exe`，两者共用同目录下的
`_internal` 运行库与 `config/`。`lvjiang-cli.exe` 是控制台程序，从 PowerShell 或
计划任务调用时能正确拿到标准输出与退出码。
