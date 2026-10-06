"""从独立进程采样指定 PID，不向目标注入代码。

安装诊断依赖：python -m pip install psutil py-spy
用法：python scripts/diagnostics/python_process.py --pid 1234 --duration 180

默认每秒记录资源、每五秒抓一次 Python 栈；--no-stack 禁用抓栈。
py-spy dump 可能短暂暂停目标，不是零干扰采样。Windows 通常需要管理员
权限，打包程序/原生线程可能无法取得 Python 栈；失败原因会写入报告。
报告默认写入仓库的 config/local/diagnostics，不进入主仓库历史。
输出移除 py-spy 的进程命令行头，不记录环境变量或栈局部变量；栈仍可能包含本地路径。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def dump_stack(executable: str, pid: int) -> dict:
    started = timestamp()
    try:
        result = subprocess.run(
            [executable, "dump", "--pid", str(pid)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {"started_at": started, "finished_at": timestamp(),
                "exit_code": result.returncode,
                "stdout": "\n".join(
                    line for line in result.stdout.splitlines()
                    if not line.startswith("Process ")),
                "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"started_at": started, "finished_at": timestamp(),
                "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--duration", type=float, default=180,
                        help="采样秒数，默认 180；Ctrl+C 可提前结束")
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--stack-interval", type=float, default=5)
    parser.add_argument("--no-stack", action="store_true")
    parser.add_argument("--py-spy", help="py-spy 可执行文件路径")
    parser.add_argument("--output", type=Path, help="报告目录（必须不存在）")
    args = parser.parse_args()
    if (args.pid <= 0 or args.duration <= 0 or args.interval < 0.2
            or args.stack_interval < 1):
        parser.error("PID/时长必须为正，采样间隔至少 0.2 秒，栈间隔至少 1 秒")
    try:
        import psutil
    except ImportError:
        parser.error("缺少 psutil，请运行 python -m pip install psutil py-spy")
    try:
        target = psutil.Process(args.pid)
        created_at = target.create_time()
        target_name = target.name()
        target.cpu_percent()
    except psutil.Error as exc:
        parser.error(f"无法访问目标进程：{exc}")

    spy = None if args.no_stack else shutil.which(args.py_spy or "py-spy")
    if not args.no_stack and not spy:
        print("未找到 py-spy：仅采样资源，没有 Python 栈。可用 --py-spy 指定路径。")
    output = args.output or Path(__file__).resolve().parents[2] / "config/local/diagnostics" / (
        f"process-{args.pid}-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}")
    output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "started_at": timestamp(), "pid": args.pid, "name": target_name,
        "process_created_at": created_at, "logical_cpus": psutil.cpu_count(),
        "interval_seconds": args.interval, "duration_seconds": args.duration,
        "stack_interval_seconds": args.stack_interval,
        "stack_enabled": bool(spy),
        "cpu_percent_unit": "process/thread 100%=one logical CPU; system 100%=all CPUs",
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"采样 PID {args.pid}，报告：{output.resolve()}；Ctrl+C 停止。")
    psutil.cpu_percent(percpu=True)
    previous_threads: dict[int, float] = {}
    children: dict[tuple[int, float], object] = {}
    started = previous_at = time.monotonic()
    next_stack = started
    pending = None
    samples = 0
    reason = "duration_elapsed"
    # 抓栈独立执行且最多一项在途，避免阻塞资源采样或堆积诊断子进程。
    with (ThreadPoolExecutor(max_workers=1) as pool,
          (output / "samples.jsonl").open("w", encoding="utf-8") as resource_file,
          (output / "stacks.jsonl").open("w", encoding="utf-8") as stack_file):
        def save_stack(result: dict) -> None:
            stack_file.write(json.dumps(result, ensure_ascii=False) + "\n")
            stack_file.flush()

        try:
            while time.monotonic() - started < args.duration:
                now = time.monotonic()
                if pending is not None and pending.done():
                    save_stack(pending.result())
                    pending = None
                if not target.is_running() or target.status() == psutil.STATUS_ZOMBIE:
                    reason = "target_exited"
                    break
                if target.create_time() != created_at:
                    reason = "pid_reused"
                    break
                with target.oneshot():
                    cpu = target.cpu_percent()
                    memory = target.memory_info()
                    threads = target.threads()
                elapsed = max(now - previous_at, 0.001)
                thread_samples = []
                current_threads = {}
                for thread in threads:
                    total = thread.user_time + thread.system_time
                    current_threads[thread.id] = total
                    thread_samples.append({
                        "tid": thread.id,
                        "cpu_percent": (max(0, total - previous_threads[thread.id])
                                        / elapsed * 100
                                        if thread.id in previous_threads else None),
                        "cpu_seconds": total,
                    })
                previous_threads = current_threads
                previous_at = now
                child_samples = []
                live_children = {}
                for child in target.children(recursive=True):
                    try:
                        key = (child.pid, child.create_time())
                        process = children.get(key, child)
                        child_cpu = process.cpu_percent()
                        live_children[key] = process
                        child_samples.append({"pid": child.pid, "name": child.name(),
                                              "cpu_percent": child_cpu if key in children else None})
                    except psutil.Error:
                        continue  # ADB 等短命子进程可能已退出。
                children = live_children
                sample = {
                    "at": timestamp(), "elapsed_seconds": now - started,
                    "sample_gap_seconds": elapsed, "process_cpu_percent": cpu,
                    "rss_bytes": memory.rss, "thread_count": len(threads),
                    "threads": thread_samples, "children": child_samples,
                    "system_cpu_per_core": psutil.cpu_percent(percpu=True),
                    "system_memory_percent": psutil.virtual_memory().percent,
                }
                resource_file.write(json.dumps(sample, ensure_ascii=False) + "\n")
                resource_file.flush()
                samples += 1
                if spy and pending is None and now >= next_stack:
                    pending = pool.submit(dump_stack, spy, args.pid)
                    next_stack = now + args.stack_interval
                time.sleep(max(0, args.interval - (time.monotonic() - now)))
        except KeyboardInterrupt:
            reason = "user_stopped"
        except psutil.Error as exc:
            reason = f"process_access_failed: {exc}"
        finally:
            if pending is not None:
                save_stack(pending.result())
    metadata.update(ended_at=timestamp(), stop_reason=reason, sample_count=samples)
    (output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结束：{reason}，已保存 {samples} 次采样。")
    return 1 if reason.startswith("process_access_failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
