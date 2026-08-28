"""批量执行报告生成器

在批量执行过程中记录各阶段的启动时间、耗时与结果，
执行结束后生成 Markdown 报告落盘到 logs/batch/。

设计原则：
- 报告只记录任务级信息（启动/结束时间、耗时、状态），
  不记录内部日志打印
- collect 结果只提取顶层摘要（key → 条目数），不深拷贝完整数据
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ...constants import PROJECT_ROOT
from ...i18n import tr

# 报告输出目录
BATCH_REPORT_DIR = PROJECT_ROOT / "logs" / "batch"


@dataclass
class _ScriptRecord:
    """单个脚本的执行记录"""
    script_id: str
    script_name: str
    status: str = ""
    duration: float = 0.0
    collect_summary: str = ""
    _start: float = 0.0

    def start(self) -> None:
        self._start = time.perf_counter()

    def finish(self, status: str, collect_result: dict | None = None) -> None:
        self.status = status
        if self._start:
            self.duration = time.perf_counter() - self._start
        if collect_result:
            self.collect_summary = _summarize_collect(collect_result)


@dataclass
class _EntryRecord:
    """单行（一个用户/角色）的执行记录"""
    label: str
    username: str
    status: str = ""
    prepare_status: str = ""
    finish_status: str = ""
    scripts: list[_ScriptRecord] = field(default_factory=list)
    duration: float = 0.0
    _start: float = 0.0

    def start(self) -> None:
        self._start = time.perf_counter()

    def finish(self) -> None:
        if self._start:
            self.duration = time.perf_counter() - self._start


class BatchReport:
    """批量执行报告

    用法：
        report = BatchReport(config_name, scripts, workflows)
        report.start_batch()
        for ...:
            report.start_entry(label, username)
            report.record_prepare(status)
            for script in scripts:
                report.start_script(script)
                ... execute ...
                report.end_script(status, collect_result)
            report.end_entry()
        report.end_batch(stopped=False)
        report.write()
    """

    def __init__(
        self,
        config_name: str,
        scripts: list[tuple[str, str]],   # [(id, name), ...]
        workflows: dict[str, str],         # 生命周期 wf
        user_column: str = "",
        total_rows: int = 0,
    ):
        self._config_name = config_name
        self._scripts = scripts
        self._workflows = workflows
        self._user_column = user_column
        self._total_rows = total_rows

        self._start_time: datetime | None = None
        self._end_time: datetime | None = None
        self._stopped = False
        self._entries: list[_EntryRecord] = []
        self._current_entry: _EntryRecord | None = None
        self._current_script: _ScriptRecord | None = None

    # ─── 生命周期回调 ──────────────────────────────────

    def start_batch(self) -> None:
        self._start_time = datetime.now()

    def start_entry(self, label: str, username: str) -> None:
        entry = _EntryRecord(label=label, username=username)
        entry.start()
        self._entries.append(entry)
        self._current_entry = entry

    def record_prepare(self, status: str) -> None:
        if self._current_entry:
            self._current_entry.prepare_status = status

    def record_finish(self, status: str) -> None:
        if self._current_entry:
            self._current_entry.finish_status = status

    def start_script(self, script_id: str, script_name: str) -> None:
        rec = _ScriptRecord(script_id=script_id, script_name=script_name)
        rec.start()
        if self._current_entry:
            self._current_entry.scripts.append(rec)
        self._current_script = rec

    def end_script(self, status: str, collect_result: dict | None = None) -> None:
        if self._current_script:
            self._current_script.finish(status, collect_result)
            self._current_script = None

    def end_entry(self) -> None:
        if self._current_entry:
            self._current_entry.finish()
            self._current_entry = None

    def finish_pending(self) -> None:
        """中断时关闭尚未结束的脚本记录"""
        if self._current_script:
            self._current_script.finish(tr("跳过"))
            self._current_script = None

    def end_batch(self, stopped: bool = False) -> None:
        self._end_time = datetime.now()
        self._stopped = stopped

    # ─── 报告生成 ──────────────────────────────────────

    def write(self) -> Path | None:
        """生成 Markdown 报告并落盘，返回文件路径

        无实际执行条目时不生成报告（避免空文件）。
        """
        if not self._start_time or not self._entries:
            return None

        BATCH_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = self._start_time.strftime("%Y%m%d_%H%M%S")
        path = BATCH_REPORT_DIR / f"批量报告_{ts}.md"
        path.write_text(self.render(), encoding="utf-8")
        return path

    def render(self) -> str:
        """生成 Markdown 文本"""
        lines: list[str] = []
        st = self._start_time or datetime.now()
        lines.append(f"# 批量执行报告 — {st:%Y-%m-%d %H:%M:%S}")
        lines.append("")

        # ── 头部：启动参数 ──
        lines.append(f"- 配置名称：{self._config_name}")
        lines.append(f"- 启动时间：{st:%H:%M:%S}")
        if self._end_time:
            lines.append(f"- 结束时间：{self._end_time:%H:%M:%S}"
                         f"{tr('（用户中断）') if self._stopped else tr('（正常完成）')}")
            total_sec = (self._end_time - st).total_seconds()
            lines.append(f"- 总耗时：{_fmt_duration(total_sec)}")
        lines.append(f"- 计划行数：{self._total_rows}")
        lines.append(f"- 实际执行：{len(self._entries)} 行")
        if self._user_column:
            lines.append(f"- 用户列：{self._user_column}")

        # 脚本列表
        script_names = "、".join(name for _, name in self._scripts)
        lines.append(f"- 执行脚本：{script_names or tr('（无）')}")

        # 工作流槽位
        wf_parts = []
        for key in ("batch_setup", "prepare_item", "finish_item",
                    "batch_teardown"):
            val = self._workflows.get(key, "")
            if val:
                wf_parts.append(f"{key}={val}")
        if wf_parts:
            lines.append(f"- 工作流：{', '.join(wf_parts)}")
        lines.append("")

        # ── 逐行记录 ──
        for i, entry in enumerate(self._entries):
            lines.append(f"## {i + 1}. {entry.label}")
            lines.append("")
            if entry.username:
                lines.append(f"- 用户：{entry.username}")
            lines.append(f"- 耗时：{_fmt_duration(entry.duration)}")

            if entry.prepare_status:
                lines.append(f"- 条目准备：{entry.prepare_status}")
            if entry.finish_status:
                lines.append(f"- 条目收尾：{entry.finish_status}")

            # 各脚本
            for sr in entry.scripts:
                status_icon = {tr("成功"): "✓", tr("失败"): "✗", tr("跳过"): "⊘"}.get(
                    sr.status, "?")
                line = f"- {sr.script_name}：{status_icon} {sr.status}"
                line += f"（{_fmt_duration(sr.duration)}）"
                if sr.collect_summary:
                    line += f"  {sr.collect_summary}"
                lines.append(line)
            lines.append("")

        # ── 汇总 ──
        lines.append(tr("## 汇总"))
        lines.append("")

        # 行级统计
        total_entries = len(self._entries)
        entry_success = sum(
            1 for e in self._entries
            if e.prepare_status != tr("失败") and e.finish_status != tr("失败")
            and e.scripts
            and all(s.status == tr("成功") for s in e.scripts)
        )
        entry_lifecycle_fail = sum(
            1 for e in self._entries
            if e.prepare_status == tr("失败") or e.finish_status == tr("失败"))
        entry_partial = total_entries - entry_success - entry_lifecycle_fail
        lines.append(f"- 行执行总计：{total_entries} 行")
        lines.append(f"  - 全部成功：{entry_success}")
        if entry_partial:
            lines.append(f"  - 部分失败：{entry_partial}")
        if entry_lifecycle_fail:
            lines.append(f"  - 生命周期失败：{entry_lifecycle_fail}")
        lines.append("")

        # 脚本级统计
        total_scripts = sum(len(e.scripts) for e in self._entries)
        success_count = sum(
            1 for e in self._entries for s in e.scripts if s.status == tr("成功"))
        fail_count = sum(
            1 for e in self._entries for s in e.scripts if s.status == tr("失败"))
        skip_count = sum(
            1 for e in self._entries for s in e.scripts if s.status == tr("跳过"))
        lines.append(f"- 脚本执行总计：{total_scripts} 次")
        lines.append(f"  - 成功：{success_count}")
        if fail_count:
            lines.append(f"  - 失败：{fail_count}")
        if skip_count:
            lines.append(f"  - 跳过：{skip_count}")

        return "\n".join(lines) + "\n"


# ─── 工具函数 ─────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    """格式化耗时：秒 → '1m 23s' 或 '4.5s'"""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def _summarize_collect(result: dict) -> str:
    """将 collect 结果压缩为简短摘要

    只提取顶层 key 和条目数，不深拷贝完整数据。
    """
    if not isinstance(result, dict) or not result:
        return ""
    parts = []
    for key, val in result.items():
        if isinstance(val, list):
            parts.append(f"{key}={len(val)}")
        elif isinstance(val, dict):
            parts.append(f"{key}={len(val)}项")
        else:
            display = str(val)
            if len(display) > 50:
                display = display[:47] + "..."
            parts.append(f"{key}={display}")
    return f"collect: {{{', '.join(parts)}}}" if parts else ""
