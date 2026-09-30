"""输入时间线：并发语义、能力门禁与编译产物

这条能力存在的理由是「严格线性」表达不了「推住摇杆的同时跳三次」。所以测试盯的是
三件事：并发的时刻对不对、不支持的通道有没有被明确拦住、块内的非法写法有没有在
解析期就报错。

不测时钟精度——那是系统调度，测了只会变成偶发失败的脆弱用例。
"""

import pytest

from lvjiang.core.input_base import InputBackendKind, TimelineUnsupported
from lvjiang.core.timeline import (
    MAX_TIMELINE_SECONDS,
    TimelineStep,
    expand_key_events,
    run_key_timeline,
    validate_timeline,
)
from lvjiang.ui.scripts.editor_dialog import check_syntax
from lvjiang.workflows.grammar import parse_text


class TestConcurrency:
    """并发就是"一路按着、另一路照跑"，这是整条能力的存在理由"""

    def test_hold_spans_other_taps(self):
        """后退从头按到尾，期间跳两次——原脚本 press "S" down ... up 的语义。"""
        events = expand_key_events([
            TimelineStep(0.4, "key", key="S", hold=2.1),
            TimelineStep(1.4, "key", key="SPACE"),
            TimelineStep(2.4, "key", key="SPACE"),
        ])
        timeline = [(round(e.at, 3), e.op, e.step.key) for e in events]
        assert timeline == [
            (0.4, "key_down", "S"),
            (1.4, "key_down", "SPACE"),
            (1.45, "key_up", "SPACE"),
            (2.4, "key_down", "SPACE"),
            (2.45, "key_up", "SPACE"),
            (2.5, "key_up", "S"),
        ]

    def test_same_instant_releases_before_presses(self):
        """同一时刻先抬后按：同键紧邻的 up→down 反过来会丢一次按键。"""
        events = expand_key_events([
            TimelineStep(0.0, "key", key="Q", hold=0.5),
            TimelineStep(0.5, "key", key="Q", hold=0.5),
        ])
        ops = [e.op for e in events if abs(e.at - 0.5) < 1e-9]
        assert ops == ["key_up", "key_down"]

    def test_runner_releases_on_stop(self):
        """停止时必须放掉按下的键，否则角色会一直往一个方向走。"""
        log: list[tuple[str, str]] = []
        calls = {"n": 0}

        def stop() -> bool:
            calls["n"] += 1
            return calls["n"] > 3

        run_key_timeline(
            [TimelineStep(0.02, "key", key="S", hold=5.0),
             TimelineStep(4.0, "key", key="SPACE")],
            lambda k: log.append(("down", k)),
            lambda k: log.append(("up", k)),
            stop_check=stop,
        )
        assert ("up", "S") in log
        assert ("down", "SPACE") not in log      # 停止后不再继续下发

    def test_runner_releases_on_exception(self):
        """异常路径同样要抬手——半截状态比失败更难查。"""
        log: list[tuple[str, str]] = []

        def key_up(key: str) -> None:
            log.append(("up", key))

        def key_down(key: str) -> None:
            log.append(("down", key))
            if key == "SPACE":
                raise RuntimeError("注入失败")

        with pytest.raises(RuntimeError):
            run_key_timeline(
                [TimelineStep(0.0, "key", key="S", hold=1.0),
                 TimelineStep(0.01, "key", key="SPACE")],
                key_down, key_up)
        assert ("up", "S") in log


class TestValidation:
    """校验放在下发之前：a11y 多 stroke 任一路失败是整组取消，宁可不执行"""

    def test_rejects_over_long_timeline(self):
        with pytest.raises(ValueError, match="超过上限"):
            validate_timeline(
                [TimelineStep(0.0, "touch", hold=MAX_TIMELINE_SECONDS + 1)],
                touch=True)

    def test_rejects_too_many_touch_strokes(self):
        with pytest.raises(ValueError, match="上限"):
            validate_timeline(
                [TimelineStep(0.0, "touch", label=str(i)) for i in range(11)],
                touch=True)

    def test_key_steps_have_no_stroke_limit(self):
        """按键没有 stroke 概念，不该套用手势的路数上限。"""
        validate_timeline(
            [TimelineStep(0.0, "key", key=f"F{i}") for i in range(11)],
            touch=False)

    def test_rejects_negative_offset(self):
        with pytest.raises(ValueError, match="不能为负"):
            validate_timeline([TimelineStep(-1.0, "key", key="A")], touch=False)


class TestBackendCapability:
    """通道不支持要明确拒绝——静默降级的现象是「脚本点了没反应」"""

    def test_default_backend_refuses(self):
        from lvjiang.core.input_base import InputBackend

        class _Bare(InputBackend):
            kind = InputBackendKind.ADB

            def click_screen(self, *a, **k): ...
            def place_screen(self, *a, **k): ...
            def move_screen(self, *a, **k): ...
            def move_relative(self, *a, **k): ...
            def drag_screen(self, *a, **k): ...
            def scroll_screen(self, *a, **k): ...
            def key_down(self, key): ...
            def key_up(self, key): ...
            def press_key(self, *a, **k): ...
            def paste_text(self, *a, **k): ...

        with pytest.raises(TimelineUnsupported, match="不支持输入时间线"):
            _Bare.run_timeline(_Bare.__new__(_Bare), [])

    def test_kind_mismatch_names_the_step_type(self):
        from lvjiang.core.input_base import InputBackend

        class _KeyOnly(InputBackend):
            kind = InputBackendKind.SEND
            timeline_kinds = frozenset({"key"})

            def click_screen(self, *a, **k): ...
            def place_screen(self, *a, **k): ...
            def move_screen(self, *a, **k): ...
            def move_relative(self, *a, **k): ...
            def drag_screen(self, *a, **k): ...
            def scroll_screen(self, *a, **k): ...
            def key_down(self, key): ...
            def key_up(self, key): ...
            def press_key(self, *a, **k): ...
            def paste_text(self, *a, **k): ...

        backend = _KeyOnly.__new__(_KeyOnly)
        with pytest.raises(TimelineUnsupported, match="touch"):
            _KeyOnly.check_timeline_kinds(
                backend, [TimelineStep(0.0, "touch")])


class TestSyntax:
    """块内只允许输入类语句，且在解析期就报错、指向那一行"""

    def test_parses_and_sorts_by_offset(self):
        prog = parse_text(
            "timeline\n"
            "    @1.5  click [general_combat].[tiaoyue]\n"
            "    @0.0  press \"S\" hold 2.1\n"
            "end\n")
        node = prog.body[0]
        assert [e.offset for e in node.entries] == [0.0, 1.5]
        # 行号保留源码顺序：报错要指真实行
        assert [e.line_no for e in node.entries] == [3, 2]

    @pytest.mark.parametrize("body", [
        'scan [a].[b] as $x by contains "x"',
        "if $x\n        click [a].[b]\n    end",
        "loop 3\n        click [a].[b]\n    end",
        "wait 1",
    ])
    def test_rejects_non_input_statements(self, body):
        problems = check_syntax(f"timeline\n    @0.0 {body}\nend\n")
        assert problems, f"{body} 竟然被接受了"

    def test_rejects_after_wait(self):
        problems = check_syntax(
            "timeline\n    @0.0 click [a].[b] after wait 1\nend\n")
        assert problems and "after wait" in problems[0]

    def test_rejects_empty_block(self):
        problems = check_syntax("timeline\nend\n")
        assert problems and "不能为空" in problems[0]


class TestCapabilityMetadata:
    def test_requires_accepts_known_capability(self):
        from lvjiang.workflows.metadata import parse_metadata
        meta = parse_metadata(
            "#% id: x\n#% name: X\n#% requires: [device_gesture]\n")
        assert meta["requires"] == ["device_gesture"]

    def test_requires_rejects_typo(self):
        from lvjiang.workflows.metadata import (
            WorkflowMetadataError,
            parse_metadata,
        )
        with pytest.raises(WorkflowMetadataError, match="未知能力"):
            parse_metadata("#% id: x\n#% name: X\n#% requires: [gesture]\n")
