"""移动设备体检要把体检自身占用的连接排除掉。

真实回归：体检自己就占着一条设备端连接，若直接展示设备端上报的总数，用户只开一个
窗口也会看到"2 条连接 / 多台电脑"，把人引向根本不存在的第二台电脑。
"""
from __future__ import annotations

from lvjiang.core.android.agent import PROTOCOL_VERSION
from lvjiang.ui.mobile.diagnostics import build_checks


def _status(**over) -> dict:
    base = {"ok": True, "protocol": PROTOCOL_VERSION, "app": "0.13.13"}
    base.update(over)
    return base


def test_health_report_excludes_the_probe_own_connection():
    alone = build_checks(_status(pc_connections=1), "0.13.13", PROTOCOL_VERSION)
    assert not [item for item in alone if item.name == "其他连接"]

    crowded = build_checks(_status(pc_connections=3), "0.13.13", PROTOCOL_VERSION)
    item = next(item for item in crowded if item.name == "其他连接")
    assert item.value == "2" and item.level == "warn"
