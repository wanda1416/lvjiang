"""静态引用只收集**可达**过程，不是 import 平铺进来的全部。

真实事故：用户执行「扫描备战装备」被拒绝执行，理由是
``page_detection.wf:52 [activity_jianghu].[haoling_of_week] - 区域未绑定``。
那是江湖号令活动页的区域，扫装备根本不会去那个页面。

成因是 import 按整文件平铺：``scan_equipped.wf`` → ``navigation.wf`` →
``page_detection.wf``，而最后这个是页面判断函数库，给游戏每个页面各备了
一个 ``is_in_*_page()``。校验遍历 ``procs`` 全量，于是拿一个永远不会被调用
的过程里的绑定缺失，把整个脚本挡在门外（``check_refs`` 失败是 raise，不是
warning）。
"""
from __future__ import annotations

from lvjiang.workflows.grammar.ast_nodes import (
    CallProc,
    EntityRef,
    If,
    Literal,
    Loop,
    ProcDef,
    Scan,
    Try,
)
from lvjiang.workflows.workflow_references import collect_refs, reachable_procs


def _scan(scene: str, key: str, line: int = 1) -> Scan:
    return Scan(scene=EntityRef(scene=scene, entity=key), target=None,
                fields=[key], line_no=line)


def _proc(name: str, body: list) -> ProcDef:
    return ProcDef(name=name, params=[], body=body)


def _scenes(body, procs) -> set[str]:
    return {ref.scene for ref in collect_refs(body, procs)}


class TestReachability:
    def test_uncalled_proc_is_not_validated(self):
        """本次事故的回归点：没被调用的过程不该参与校验。"""
        procs = {
            "used": _proc("used", [_scan("bag", "item")]),
            "never_called": _proc("never_called", [_scan("activity_jianghu",
                                                         "haoling_of_week")]),
        }
        body = [CallProc(name="used", args=[])]
        assert _scenes(body, procs) == {"bag"}

    def test_transitive_calls_are_followed(self):
        """可达是传递闭包——间接调用到的过程仍要校验。"""
        procs = {
            "a": _proc("a", [CallProc(name="b", args=[])]),
            "b": _proc("b", [_scan("deep", "region")]),
        }
        assert _scenes([CallProc(name="a", args=[])], procs) == {"deep"}

    def test_calls_inside_nested_bodies_count(self):
        """if / loop / try 体里的调用同样使过程可达，否则会漏检真问题。"""
        for wrapper in (
            If(condition=Literal(True), then_body=[CallProc(name="p", args=[])],
               else_body=[]),
            Loop(count=Literal(1), body=[CallProc(name="p", args=[])]),
            Try(body=[CallProc(name="p", args=[])], catch_body=[]),
        ):
            procs = {"p": _proc("p", [_scan("nested", "r")])}
            assert _scenes([wrapper], procs) == {"nested"}, type(wrapper).__name__

    def test_recursion_terminates(self):
        """自递归与互相调用不能把闭包求解转成死循环。"""
        procs = {
            "a": _proc("a", [CallProc(name="b", args=[]), _scan("s_a", "r")]),
            "b": _proc("b", [CallProc(name="a", args=[]), _scan("s_b", "r")]),
        }
        assert reachable_procs([CallProc(name="a", args=[])], procs) == {"a", "b"}
        assert _scenes([CallProc(name="a", args=[])], procs) == {"s_a", "s_b"}

    def test_call_to_undefined_proc_is_ignored(self):
        """调用了不存在的过程属于另一类错误，这里静默跳过而不是崩。"""
        assert reachable_procs([CallProc(name="missing", args=[])], {}) == {"missing"}
        assert _scenes([CallProc(name="missing", args=[])], {}) == set()

    def test_top_level_refs_always_collected(self):
        """顶层语句本身的引用与可达性无关，恒要校验。"""
        assert _scenes([_scan("top", "r")], {}) == {"top"}


class TestRealWorkflowScope:
    """拿仓库里真实的 scan_equipped.wf 验证范围收敛。"""

    def test_scan_equipped_does_not_pull_in_unrelated_pages(self):
        from pathlib import Path

        from lvjiang.workflows.grammar import parse_file

        root = Path(__file__).parents[2] / "config" / "system" / "workflows"
        entry = root / "scan_equipped.wf"
        if not entry.exists():
            import pytest
            pytest.skip("出厂脚本不在仓库内")

        def flatten(path, procs=None, seen=None):
            """按引擎语义递归平铺 import 的全部 def"""
            procs = {} if procs is None else procs
            seen = set() if seen is None else seen
            resolved = path.resolve()
            if resolved in seen:
                return procs
            seen.add(resolved)
            program = parse_file(resolved)
            for imp in program.imports:
                target = resolved.parent / imp.path
                flatten(target if target.exists() else root / imp.path, procs, seen)
            procs.update(program.procs)
            return procs

        program = parse_file(entry)
        procs = flatten(entry)
        assert procs, "预期 import 会平铺进多个过程"

        scenes = _scenes(program.body, procs)
        # 事故现场：这个活动页与扫装备无关，绝不该进入校验范围
        assert "activity_jianghu" not in scenes
        # 而它真正要用的场景仍在
        assert "bag_equip_detail" in scenes
