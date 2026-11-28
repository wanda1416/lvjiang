"""workflows 目录结构约定：可运行任务一律放在子目录里。

根层平铺时，「哪些 .wf 是任务、哪些是过程库或批量钩子」只能逐个打开看
front-matter。而脚本以 ``#% id`` 被引用、路径解析走
``discovery.resolve_workflow_reference``（相对路径 → id → 文件名），所以把任务
收进功能子目录既不改变 id、也不改变 UI 分组（分组取 ``#% group``），却能让
根层只剩专用目录与编辑器草稿。

这条用例守住"根层不再长出任务"：新增任务时随手放在 ``workflows/`` 顶层会
在这里失败，而不是等别人翻目录才发现结构散了。
"""

from __future__ import annotations

from lvjiang.workflows.discovery import discover_scripts


def test_no_runnable_script_sits_at_workflows_root():
    offenders = sorted(
        config["id"]
        for config in discover_scripts()
        if config.get("wf_file") and "/" not in str(config["wf_file"])
    )
    assert offenders == [], (
        "可运行脚本必须放进 workflows 的功能子目录（如 daily/、scan/、gather/、"
        f"stake/、standalone/），根层发现 {len(offenders)} 个: {offenders}"
    )
