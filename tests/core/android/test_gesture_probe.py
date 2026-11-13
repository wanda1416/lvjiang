"""并发手势探针必须真的把时间线发出去。

时间线下发在 ``AgentInput`` 上，不在 ``AgentClient`` 上：client 只管连接与 RPC，
输入后端才知道怎么把时间线编译成**一次**多 stroke 手势（多 stroke 同属一个
GestureDescription 才是真并发，拆成多次 dispatch 就退回顺序执行）。探针拿到的
是 ``connect_agent`` 返回的 client，所以必须自己包一层。

少了那一层就是 `AttributeError: 'AgentClient' object has no attribute
'run_timeline'`——而探针把任何异常都当结果展示，于是界面上只报一行属性错误，
看起来像"手势不被支持"，实际一次手势都没发出去。这条用例用假 client 把最终发出
的 RPC 钉住，不需要真机。
"""
from lvjiang.core.android.gesture_probe import (
    build_probe_steps,
    run_concurrent_probe,
)


class _FakeClient:
    """只记录 RPC，不碰网络。"""

    timeout = 15.0

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, op: str, timeout=None, **params):
        self.calls.append((op, params))
        return {"ok": True}, None


class _BrokenClient(_FakeClient):
    def call(self, op: str, timeout=None, **params):
        raise RuntimeError("设备端拒绝")


def test_probe_dispatches_one_gesture_with_every_stroke() -> None:
    client = _FakeClient()

    outcome = run_concurrent_probe(client, 2800, 1260, hold=2.0)

    assert outcome.ok, outcome.message
    # 必须是一次 RPC：拆成多次就不是并发了
    assert len(client.calls) == 1
    op, params = client.calls[0]
    assert op == "gesture"
    strokes = params["strokes"]
    # 一路按住 + 两次点击
    assert len(strokes) == 3
    assert strokes[0]["hold_ms"] == 2000
    assert [stroke["start_ms"] for stroke in strokes] == [0, 500, 1200]
    # 推住与点击分落屏幕两侧，且与回报给界面的落点一致
    assert strokes[0]["points"][0] == list(outcome.push_point)
    assert strokes[1]["points"][0] == list(outcome.tap_point)


def test_probe_reports_dispatch_failure_instead_of_raising() -> None:
    """探针要把任何失败原样展示，但不能自己崩——界面靠它的文案说话。"""
    outcome = run_concurrent_probe(_BrokenClient(), 2800, 1260, hold=1.0)

    assert not outcome.ok
    assert "设备端拒绝" in outcome.message
    # 失败也要带上落点，否则用户不知道该盯屏幕哪里
    assert outcome.push_point != (0, 0)


def test_probe_points_avoid_the_status_bar_and_screen_centre() -> None:
    """状态栏会被系统拦，屏幕中心常有游戏的主要控件，误触代价高。"""
    _steps, push, tap = build_probe_steps(2800, 1260, hold=2.0)

    for point in (push, tap):
        assert point[1] > 1260 * 0.5, point
    assert push[0] < 2800 * 0.3
    assert tap[0] > 2800 * 0.7
