---
title: 本地控制面与纯 Python 薄壳 lvjiang-cli 架构方案
status: active
---

# 本地控制面与纯 Python 薄壳 lvjiang-cli 架构方案

## 背景与问题

1. **发行版体积翻倍**：旧版 `packaging/lvjiang.spec` 将同一份 `pyz = PYZ(a.pure)` 同时内嵌进 `lvjiang.exe` 与 `lvjiang-cli.exe`，导致 `lvjiang-cli.exe` 与主程序体积几乎相同。
2. **多进程抢锁与割裂**：旧版 `lvjiang-cli` 独立执行完整 `_bootstrap()` 并抢占实例锁与设备通道，无法与运行中的 GUI 主实例共享执行通道，也不支持调度插件 `class` 工作流与 `BatchScheduler` 批量配置组。
3. **外部计划任务串联诉求**：用户希望通过 Windows 计划任务 / PowerShell / `cron` 在固定时间（如 05:01）依次串联日常与周常批量任务，不在 `lvjiang` 内部引入重型流水线工作台。

## 核心设计决策

1. **主实例保持单活跃槽位，零内置队列**：
   - `lvjiang` 本地控制面（`src/lvjiang/core/control/server.py`）仅维护单个当前活跃任务槽位；空闲返回 `202 Accepted`，繁忙直接返回 `409 Conflict (code="busy")`。
   - `--on-busy wait` 的等待与重试循环完全位于薄壳 `lvjiang-cli` 侧，不向主实例引入队列状态与排队 UI。
2. **跨平台（Windows / Linux / macOS）统一传输与安全隔离**：
   - 统一绑定 `127.0.0.1:0`（随机空闲端口）+ HTTP/1.1 + SSE，不拆分 Named Pipe / Unix Domain Socket，规避 macOS 104 字节 `sun_path` 上限与 `.sock` 残留问题。
   - 发现文件 `config/session/control.json` 通过临时文件 + 原子 `os.replace()` 写入；POSIX 强制 `0o600` 权限；所有请求校验 32 字节随机 Bearer Token；清理时校验 token 避免旧进程误删新实例文件。
   - 仅持有 `core/access.py` 主实例锁（`not is_readonly()`）的实例启动控制面。
3. **未启动时自动拉起无头实例**：
   - 探活（`GET /control/health` + `instance_id` 核对）失败时，`lvjiang-cli` 自动脱离拉起 `lvjiang --headless-server --all-plugins --idle-exit 30`（不导入 PyQt6，不依赖 `DISPLAY`）。
   - 短空闲驻留（默认 30 秒）使外层脚本紧接着串联的第二个任务可直接复用已预热的 OCR 与设备连接；超时无新任务则自动退出释放内存。
4. **全量自动化工作流统一覆盖**：
   - `src/lvjiang/core/control/runner.py` 统一覆盖 `.wf` 脚本、插件 `register_workflow()` 类工作流与 `BatchScheduler` 批量配置组；兼容旧版 `--list` / `--exec`，新增 `list` / `run` / `batch` / `status` / `stop` 子命令与标准退出码（0/2/3/4/5/6/7/8）。
5. **纯 Python 标准库薄壳打包（零新增语言工具链）**：
   - `src/lvjiang/cli_client.py` 仅使用 Python 标准库（由单元测试 AST 守卫强制校验）。
   - `packaging/lvjiang.spec` 为 `packaging/cli_launcher.py` 建立独立最小 `cli_a = Analysis(...)`，排除 `lvjiang`、PyQt6、OCR、OpenCV、numpy 等全部重依赖，与主程序共享 `_internal/` 运行时。
