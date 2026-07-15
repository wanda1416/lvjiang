"""工作流 DSL 行解析器

语法：
    click [scene].[field]
    wait <delay_name> | <seconds>
    scan [scene] | [scene].[f1, f2, ...]
    click_match "text" [error "msg"]
    collect
    collect_as <key>
    log "message"
    # comment
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Step:
    """解析后的单步指令"""
    instruction: str                          # click / wait / scan / click_match / collect / collect_as / log
    args: dict = field(default_factory=dict)  # 指令参数


# ─── 正则模式 ────────────────────────────────────────────

# click [scene].[field]
_RE_CLICK = re.compile(r"^click\s+\[(\w+)\]\.\[(\w+)\]$")

# wait <name> | <float>
_RE_WAIT = re.compile(r"^wait\s+(.+)$")

# scan [scene] 或 scan [scene].[f1, f2, ...]
_RE_SCAN_FULL = re.compile(r"^scan\s+\[(\w+)\]\.\[([\w,\s]+)\]$")
_RE_SCAN_SCENE = re.compile(r"^scan\s+\[(\w+)\]$")

# click_match "text" [error "msg"]
_RE_CLICK_MATCH = re.compile(r'^click_match\s+"([^"]+)"(?:\s+error\s+"([^"]+)")?$')

# collect
_RE_COLLECT = re.compile(r"^collect$")

# collect_as <key>
_RE_COLLECT_AS = re.compile(r"^collect_as\s+(\w+)$")

# log "message"
_RE_LOG = re.compile(r'^log\s+"(.+)"$')


def parse_line(line: str) -> Optional[Step]:
    """解析单行 DSL，返回 Step 或 None（空行/注释）"""
    line = line.strip()

    # 空行或注释
    if not line or line.startswith("#"):
        return None

    # click [scene].[field]
    m = _RE_CLICK.match(line)
    if m:
        return Step("click", {"scene": m.group(1), "field": m.group(2)})

    # scan [scene].[f1, f2, ...]
    m = _RE_SCAN_FULL.match(line)
    if m:
        fields = [f.strip() for f in m.group(2).split(",")]
        return Step("scan", {"scene": m.group(1), "fields": fields})

    # scan [scene]
    m = _RE_SCAN_SCENE.match(line)
    if m:
        return Step("scan", {"scene": m.group(1)})

    # click_match "text" [error "msg"]
    m = _RE_CLICK_MATCH.match(line)
    if m:
        args = {"text": m.group(1)}
        if m.group(2):
            args["error_msg"] = m.group(2)
        return Step("click_match", args)

    # collect_as <key>（必须在 collect 之前匹配）
    m = _RE_COLLECT_AS.match(line)
    if m:
        return Step("collect_as", {"key": m.group(1)})

    # collect
    if _RE_COLLECT.match(line):
        return Step("collect", {})

    # wait <name> | <float>
    m = _RE_WAIT.match(line)
    if m:
        val = m.group(1).strip()
        try:
            return Step("wait", {"seconds": float(val)})
        except ValueError:
            return Step("wait", {"delay_name": val})

    # log "message"
    m = _RE_LOG.match(line)
    if m:
        return Step("log", {"message": m.group(1)})

    raise ValueError(f"无法解析 DSL 行: {line!r}")


def parse_file(path: Path) -> list[Step]:
    """解析整个 .wf 文件，返回 Step 列表"""
    steps = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            try:
                step = parse_line(line)
                if step is not None:
                    steps.append(step)
            except ValueError as e:
                raise ValueError(f"{path}:{line_no}: {e}") from None
    return steps
