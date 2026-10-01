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


class TestCompiler:
    """条目 → 后端原语：两个字段名的坑都出在这一层，必须用真实 AST 驱动

    之前只测了模型层与解析层，compile 这一段没覆盖，于是 `@0.0` 被当成 hold
    （要求 > 0）、`Press.duration` 被写成 `Press.hold`，两个都是上机才炸。
    """

    @staticmethod
    def _host():
        from types import SimpleNamespace

        from lvjiang.core.layout_models import Arrow, Point, Region
        from lvjiang.workflows.engine.actions import _ActionsMixin
        from lvjiang.workflows.engine.timeline import _TimelineMixin

        regions = [Region("tiaoyue", 0.4, 0.4, 0.1, 0.1,
                          activation_key="SPACE"),
                   Region("pickup", 0.5, 0.5, 0.1, 0.1)]
        points = [Point("move_center", 0.2, 0.8),
                  Point("move_backward", 0.2, 0.9)]
        arrows = [Arrow("move_backward", from_key="move_center",
                        to_key="move_backward")]

        class _Layout:
            def get_scene_regions(self, _scene): return regions
            def get_scene_points(self, _scene): return points
            def get_scene_arrows(self, _scene): return arrows

        class _Workflow:
            def _region_to_screen(self, region, jitter=True):
                return int(region.x_ratio * 1000), int(region.y_ratio * 1000)

            def _point_to_screen(self, point, jitter=True):
                return int(point.cx_ratio * 1000), int(point.cy_ratio * 1000)

            def _ratio_to_screen(self, cx, cy):
                return int(cx * 1000), int(cy * 1000)

        class _Host(_TimelineMixin):
            run_env = "desktop"
            _layout = _Layout()
            variables: dict = {}
            _input = SimpleNamespace()
            _resolve_hold_duration = _ActionsMixin._resolve_hold_duration

            def _resolve(self, value):
                return getattr(value, "value", value)

            def _ensure_workflow(self):
                return _Workflow()

        return _Host()

    def _compile(self, src: str):
        host = self._host()
        node = parse_text(src).body[0]
        return [host._compile_timeline_entry(e) for e in node.entries]

    def test_zero_offset_is_valid(self):
        """@0.0 是最常见的第一条，不能套用 hold「必须 > 0」的规则。"""
        steps = self._compile(
            "timeline\n    @0.0  press \"S\" hold 2.4\nend\n")
        assert steps[0].offset == 0.0
        assert steps[0].kind == "key"
        assert steps[0].key == "S"
        assert steps[0].hold == 2.4

    def test_rejects_negative_offset(self):
        from lvjiang.workflows.engine.signals import WorkflowUserError
        with pytest.raises(WorkflowUserError, match=">= 0"):
            self._compile("timeline\n    @-1  press \"S\" hold 1\nend\n")

    def test_press_without_hold_is_a_tap(self):
        steps = self._compile("timeline\n    @1.0  press \"SPACE\"\nend\n")
        assert steps[0].hold == 0.0

    def test_click_with_activation_key_becomes_key_step(self):
        """桌面布局给 tiaoyue 绑了 SPACE，所以这一路应落成按键而不是触点。"""
        steps = self._compile(
            "timeline\n    @0.4  click [general_combat].[tiaoyue]\nend\n")
        assert steps[0].kind == "key"
        assert steps[0].key == "SPACE"

    def test_click_without_activation_key_becomes_touch_step(self):
        steps = self._compile(
            "timeline\n    @0.0  click [general_combat].[pickup]\nend\n")
        step = steps[0]
        assert step.kind == "touch"
        assert (step.x1, step.y1) == (step.x2, step.y2) == (500, 500)

    def test_drag_arrow_becomes_touch_step_with_endpoints(self):
        steps = self._compile(
            "timeline\n"
            "    @0.0  drag [general_move].[move_backward] "
            "duration 0.1 hold 2.4\n"
            "end\n")
        step = steps[0]
        assert step.kind == "touch"
        assert (step.x1, step.y1) == (200, 800)     # move_center
        assert (step.x2, step.y2) == (200, 900)     # move_backward
        assert step.move == pytest.approx(0.1)
        assert step.hold == pytest.approx(2.4)

    def test_rejects_press_down_up(self):
        from lvjiang.workflows.engine.signals import WorkflowUserError
        for mode in ("down", "up"):
            with pytest.raises(WorkflowUserError, match="hold"):
                self._compile(
                    f"timeline\n    @0.0  press \"S\" {mode}\nend\n")

    def test_rejects_unbound_entity(self):
        from lvjiang.workflows.engine.signals import WorkflowUserError
        with pytest.raises(WorkflowUserError, match="未在当前布局绑定"):
            self._compile(
                "timeline\n    @0.0  click [general_combat].[nope]\nend\n")


class TestEnvGuard:
    """条目级 env 守卫：两端只有一两路不同时，共享的其余几路不必复制一遍"""

    def test_parses_guard_after_offset(self):
        prog = parse_text(
            "timeline\n"
            "    @0.0  env:\"desktop\" -> press \"S\" hold 2.4\n"
            "    @0.0  env:\"android\" -> drag [general_move].[move_backward] "
            "duration 0.1 hold 2.4\n"
            "    @0.4  click [general_combat].[tiaoyue]\n"
            "end\n")
        entries = prog.body[0].entries
        assert [e.env for e in entries] == ["desktop", "android", ""]

    def test_filters_by_run_env(self):
        """不匹配的那一路编译期就被过滤掉，根本不生成步骤。"""
        host = TestCompiler._host()
        src = ("timeline\n"
               "    @0.0  env:\"desktop\" -> press \"S\" hold 2.4\n"
               "    @0.0  env:\"android\" -> drag [general_move]."
               "[move_backward] duration 0.1 hold 2.4\n"
               "    @0.4  click [general_combat].[tiaoyue]\n"
               "end\n")
        node = parse_text(src).body[0]

        host.run_env = "desktop"
        kept = [e for e in node.entries if not e.env or e.env == host.run_env]
        steps = [host._compile_timeline_entry(e) for e in kept]
        assert [s.kind for s in steps] == ["key", "key"]
        assert steps[0].key == "S"

        host.run_env = "android"
        kept = [e for e in node.entries if not e.env or e.env == host.run_env]
        steps = [host._compile_timeline_entry(e) for e in kept]
        assert [s.kind for s in steps] == ["touch", "key"]

    def test_rejects_empty_env_name(self):
        problems = check_syntax(
            "timeline\n    @0.0  env:\"\" -> press \"S\" hold 1\n" "end\n")
        assert problems and "环境名" in problems[0]

    def test_guard_still_rejects_non_input_statements(self):
        """守卫不是后门：块内仍然只允许输入类语句。"""
        problems = check_syntax(
            "timeline\n"
            "    @0.0  env:\"desktop\" -> scan [a].[b] as $x by contains \"x\"\n"
            "end\n")
        assert problems
