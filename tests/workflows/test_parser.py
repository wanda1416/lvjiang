"""DSL 解析器验证测试"""


import pytest
from lark.exceptions import LarkError, VisitError

from lvjiang.workflows.grammar import (
    Click,
    CoordPoint,
    EntityRef,
    Eval,
    Find,
    FuncCall,
    If,
    Literal,
    Move,
    PanelRef,
    Place,
    Press,
    Recognize,
    ReplayInputTrace,
    Scan,
    Scroll,
    TupleLiteral,
    VarRef,
    Wait,
    WaitStable,
    parse_file,
    parse_text,
)
from lvjiang.workflows.grammar.ast_nodes import PressMode
from tests.case_matrix import case_matrix


def test_parse_file_tolerates_utf8_bom(tmp_path):
    """Windows 编辑器常带 BOM；不能在第 1 行第 1 列报 No terminal matches。"""
    path = tmp_path / "bom.wf"
    path.write_bytes("\ufeff#% runnable: true\nwait 1\n".encode("utf-8"))

    program = parse_file(path)

    assert len(program.body) == 1


# ─── click 指令测试 ─────────────────────────────────────────


def test_click_const_or_var():
    """测试 click 支持 const_or_var 统一语法"""
    print("\n=== 测试 click const_or_var ===")

    # click [scene].[region]
    program = parse_text("click [scene].[region]")
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, EntityRef)
    assert n.target.scene == "scene"
    assert n.target.entity == "region"
    print("  click [scene].[region]: OK")

    # click "scene"."region" — 字符串形式不再支持用于场景引用
    # 使用 [scene].[region] 形式
    program = parse_text("click [scene].[region]")
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, EntityRef)
    assert n.target.scene == "scene"
    assert n.target.entity == "region"
    print('  click [scene].[region] (原 "scene"."region" 测试): OK')

    # click $scene.$region
    program = parse_text("click $scene.$region")
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, EntityRef)
    assert isinstance(n.target.scene, VarRef)
    assert n.target.scene.name == "scene"
    assert isinstance(n.target.entity, VarRef)
    assert n.target.entity.name == "region"
    print("  click $scene.$region: OK")

    # click [scene].$var
    program = parse_text("click [scene].$var")
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, EntityRef)
    assert n.target.scene == "scene"
    assert isinstance(n.target.entity, VarRef)
    assert n.target.entity.name == "var"
    print("  click [scene].$var: OK")


# ─── click 鼠标键测试 ───────────────────────────────────────


def test_click_button_aliases_normalize_to_x1_x2():
    """back/forward 是 x1/x2 的别名，解析后规范化为 x1/x2（与轨迹格式的键名统一）"""
    assert parse_text("click [scene].[region] back").body[0].button == "x1"
    assert parse_text("click [scene].[region] forward").body[0].button == "x2"


def test_click_button_combined_with_wait_clause():
    """鼠标键 + wait_clause 同时出现时都要生效"""
    program = parse_text("click [scene].[region] right after wait @step_interval")
    assert len(program.body) == 2
    click_node = program.body[0]
    assert isinstance(click_node, Click)
    assert click_node.button == "right"
    assert click_node.suppress_defaults is True
    assert isinstance(program.body[1], Wait)


# ─── drag 指令测试 ──────────────────────────────────────────


# ─── wait 指令测试 ──────────────────────────────────────────

def test_wait():
    """测试 wait 支持多种延迟形式"""
    print("\n=== 测试 wait ===")

    # wait 固定秒数
    program = parse_text("wait 1.5")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, Literal)
    assert n.delay.value == 1.5
    print("  wait 1.5: OK")

    # wait 命名延迟
    program = parse_text("wait @page_refresh")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, Literal)
    assert n.delay.value == "page_refresh"
    print("  wait @page_refresh: OK")

    # wait $var
    program = parse_text("wait $interval")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, VarRef)
    assert n.delay.name == "interval"
    print("  wait $interval: OK")

    # wait (min, max) → TupleLiteral
    program = parse_text("wait (1, 2)")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, TupleLiteral)
    assert len(n.delay.elements) == 2
    assert n.delay.elements[0].value == 1.0
    assert n.delay.elements[1].value == 2.0
    print("  wait (1, 2): OK")


def test_bare_named_delay_is_syntax_error():
    """裸标识符（无 @ 前缀）应为语法错误"""
    from lark.exceptions import UnexpectedCharacters
    with pytest.raises(UnexpectedCharacters):
        parse_text("wait step_interval")


def test_bare_delay_in_clause_is_syntax_error():
    """click ... after wait step_interval（无 @）也应为语法错误"""
    from lark.exceptions import UnexpectedCharacters
    with pytest.raises(UnexpectedCharacters):
        parse_text("click [s].[r] after wait step_interval")


# ─── 泛化元组混合引用测试 ─────────────────────────────────


# ─── wait stable 指令测试（参数化） ────────────────────────────


# ─── click/drag wait 语法糖测试 ─────────────────────────────


def test_click_around_wait():
    """click ... around wait -> [Wait, Click, Wait]"""
    program = parse_text("click [scene].[region] around wait (0.3, 0.8)")
    assert len(program.body) == 3
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Click)
    assert isinstance(program.body[2], Wait)
    # 同一参数：前后 Wait 的 delay 相同
    assert program.body[0].delay == program.body[2].delay
    assert isinstance(program.body[0].delay, TupleLiteral)
    assert program.body[0].delay.elements[0].value == 0.3
    assert program.body[0].delay.elements[1].value == 0.8
    # 显式 wait_clause 应抑制默认延迟
    assert program.body[1].suppress_defaults is True


def test_click_around_wait_stable():
    """click ... around wait stable -> [WaitStable, Click, WaitStable]"""
    program = parse_text("click [scene].[region] around wait stable 5 threshold 0.03")
    assert len(program.body) == 3
    assert isinstance(program.body[0], WaitStable)
    assert isinstance(program.body[1], Click)
    assert isinstance(program.body[2], WaitStable)
    # 同一参数：前后 WaitStable 相同
    assert program.body[0] == program.body[2]
    assert program.body[0].threshold == 0.03


# ─── move 指令 ──────────────────────────────────────────────


def test_move_explicit_start_expands_to_place():
    program = parse_text(
        "move (0.1, 0.2) to (0.8, 0.7) duration 0.4")
    assert len(program.body) == 2
    place, move = program.body
    assert isinstance(place, Place)
    assert place.target == CoordPoint(0.1, 0.2)
    assert isinstance(move, Move)
    assert move.mode == "to"
    assert move.target == CoordPoint(0.8, 0.7)
    assert move.duration == Literal(0.4)


def test_move_by_canvas_ratio():
    program = parse_text("move by (-0.25, 0.1) duration $turn_time")
    node = program.body[0]
    assert isinstance(node, Move)
    assert node.mode == "by"
    assert node.target == CoordPoint(-0.25, 0.1)
    assert node.duration == VarRef("turn_time")


def test_move_by_explicit_start_and_around_wait():
    program = parse_text(
        "move (0.5, 0.5) by (0.2, -0.1) duration 0.3 around wait 0.2")
    assert [type(node) for node in program.body] == [Wait, Place, Move, Wait]
    assert program.body[1].target == CoordPoint(0.5, 0.5)
    assert program.body[2].target == CoordPoint(0.2, -0.1)


def test_legacy_move_syntax_is_rejected():
    with pytest.raises(LarkError):
        parse_text("move (0.5, 0.3)")


@case_matrix(
    "source",
    [
        "click (-0.1, 0.5)",
        "place (0.5, 1.1)",
        "move to (1.01, 0.5)",
        "move (0.5, -0.1) by (0.2, 0)",
        "drag (0.1, 0.1) to (1.2, 0.2) duration 0.1",
    ],
)
def test_absolute_coordinates_must_stay_in_unit_range(source):
    with pytest.raises(VisitError, match="超出"):
        parse_text(source)


@case_matrix(
    "source",
    [
        "move by (-1.01, 0)",
        "move by (0, 1.01)",
    ],
)
def test_relative_move_components_must_stay_in_signed_unit_range(source):
    with pytest.raises(VisitError, match=r"\[-1,1\]"):
        parse_text(source)


# ─── scroll 解析测试 ─────────────────────────────────────────────


# ─── scroll interval 测试 ─────────────────────────────


def test_scroll_interval_with_wait_clause():
    """interval 与后缀 wait 子句共存，互不干扰"""
    program = parse_text(
        "scroll [scene].[region] down 2 interval 0.05 after wait 0.5")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Scroll)
    assert program.body[0].amount == 2
    assert program.body[0].interval == 0.05
    assert isinstance(program.body[1], Wait)
    assert program.body[1].delay.value == 0.5


# ─── scroll wait_clause 测试 ────────────────────────────────────


# ─── before/after 组合语法测试 ────────────────────────────────


def test_env_guard_desugars_to_if_with_constant_string():
    program = parse_text(
        'env:"desktop" -> press "F" after wait 0.3\n'
    )

    guard = program.body[0]
    assert isinstance(guard, If)
    assert isinstance(guard.condition, FuncCall)
    assert guard.condition.func_name == "env"
    assert guard.condition.func_args == [Literal("desktop")]
    # 一条带等待子句的源语句仍可展开成 Press + Wait。
    assert len(guard.then_body) == 2
    assert isinstance(guard.then_body[0], Press)
    assert isinstance(guard.then_body[1], Wait)
    assert guard.else_body == []
    assert guard.line_no == 1
    assert all(node.line_no == 1 for node in guard.then_body)


@case_matrix("code", [
    'env:$target -> press "F"\n',
    'env:desktop -> press "F"\n',
    'env:"desktop" -> if $ready\n',
])
def test_env_guard_rejects_dynamic_bare_or_block_forms(code):
    with pytest.raises(LarkError):
        parse_text(code)


def test_env_guard_rejects_empty_environment_name():
    with pytest.raises(VisitError, match="环境名不能为空"):
        parse_text('env:"" -> press "F"\n')


# ─── press wait_clause 测试 ────────────────────────────────────


def test_press_hold_direct_range():
    program = parse_text('press "E" hold (0.058, 0.063)')

    node = program.body[0]
    assert isinstance(node, Press)
    assert node.mode == PressMode.HOLD
    assert isinstance(node.duration, TupleLiteral)
    assert [item.value for item in node.duration.elements] == [0.058, 0.063]


def test_click_hold_fixed_range_and_variable():
    fixed = parse_text("click [general_combat].[xuli] hold 1.4").body[0]
    ranged = parse_text(
        "click [general_combat].[xuli] hold (1.35, 1.45)").body[0]
    variable = parse_text(
        "click [general_combat].[xuli] right hold $hold_time").body[0]

    assert isinstance(fixed, Click)
    assert fixed.hold == 1.4
    assert isinstance(ranged.hold, TupleLiteral)
    assert [item.value for item in ranged.hold.elements] == [1.35, 1.45]
    assert variable.button == "right"
    assert isinstance(variable.hold, VarRef)
    assert variable.hold.name == "hold_time"


def test_press_hold_tuple_variable():
    program = parse_text(
        'eval $hold_range = (0.058, 0.063)\n'
        'press "E" hold $hold_range\n'
    )

    node = program.body[1]
    assert isinstance(node, Press)
    assert node.mode == PressMode.HOLD
    assert isinstance(node.duration, VarRef)
    assert node.duration.name == "hold_range"


def test_press_inline_combo():
    program = parse_text('press "SHIFT" + "`"\n')
    assert program.body[0].key == "SHIFT"
    assert program.body[0].keys == ("SHIFT", "`")


# ─── scan/recognize 测试 ────────────────────────────────────


def test_recognize_rich():
    """测试 recognize as rich 语法"""
    print("\n=== 测试 recognize as rich ===")

    # region 模式 + rich
    program = parse_text("recognize [material_grid].[f1, f2] as rich $mats")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.rich is True
    assert isinstance(n.target, VarRef)
    assert n.target.name == "mats"
    print("  recognize [s].[f1,f2] as rich $var: OK")

    # 普通模式：rich=False
    program = parse_text("recognize [material_grid] as $mats")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.rich is False
    print("  recognize [s] as $var (plain): OK")

    # panel cell 模式 + rich
    program = parse_text("recognize [s].[panel][1][2] as rich $cell")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.rich is True
    assert isinstance(n.scene, PanelRef)
    assert n.target.name == "cell"
    print("  recognize [s].[panel][1][2] as rich $var: OK")

    # panel cell 模式无 rich
    program = parse_text("recognize [s].[panel][1][2] as $cell")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.rich is False
    print("  recognize [s].[panel][1][2] as $var (plain): OK")

    # rich + on group + where 子句
    program = parse_text('recognize [s] as rich $m on group "grp" where confidence >= 0.8')
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.rich is True
    assert n.group is not None
    assert n.where is not None
    print("  recognize [s] as rich $var on group ... where ...: OK")

    # 大小写不敏感
    program = parse_text("recognize [s] as RICH $m")
    n = program.body[0]
    assert n.rich is True
    print("  recognize [s] as RICH $var (case insensitive): OK")


@pytest.mark.parametrize("source", [
    'recognize [s].[f1] as rich $m by equals "text"',
    'recognize [s].[p][1][2] as rich $m by equals "text"',
    'recognize [parent].[card].[icon] as rich $m by equals "text"',
])
def test_recognize_rich_and_by_are_mutually_exclusive(source):
    """Region、Panel 和子场景均应在解析阶段拒绝 rich + by。"""
    with pytest.raises(VisitError) as exc_info:
        parse_text(source)

    message = str(exc_info.value.orig_exc)
    assert "不能同时使用 'as rich' 和 'by'" in message
    assert "完整识别信息" in message
    assert "命中位置" in message
    assert "第 1 行" in message


def test_scan_and_find_with_cleaning_group():
    scan = parse_text(
        'scan [s].[f1, f2] as $raw where confidence >= 0.5 with "equip"'
    ).body[0]
    assert isinstance(scan, Scan)
    assert scan.cleaning_group == "equip"

    found = parse_text(
        'find [s].[f1] as $hit by contains "会心率" with "equip"'
    ).body[0]
    assert isinstance(found, Find)
    assert found.cleaning_group == "equip"

    # 无 with 时 with_func 为 None
    program = parse_text("recognize [s].[f1] as rich $mats")
    n = program.body[0]
    assert n.with_func is None

    # panel cell + rich + with
    program = parse_text("recognize [s].[p][1][2] as rich $cell with yysls_rich_parse")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.rich is True
    assert isinstance(n.scene, PanelRef)
    assert n.with_func is not None
    assert isinstance(n.with_func, Literal)
    assert n.with_func.value == "yysls_rich_parse"

    # rich + with + group + where 全组合（with 在末尾）
    program = parse_text(
        'recognize [s].[f1] as rich $m on group "g" where confidence >= 0.5 with my_func'
    )
    n = program.body[0]
    assert n.rich is True
    assert n.with_func is not None
    assert n.with_func.value == "my_func"
    assert n.group is not None
    assert n.where is not None

    # panel + rich + with — 改用 [sc].[pn] 形式
    program = parse_text('recognize [sc].[pn][1][2] as rich $cell with yysls_rich_parse')
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert isinstance(n.scene, PanelRef)
    assert n.scene.scene == "sc"
    assert n.scene.panel == "pn"
    assert n.rich is True
    assert n.with_func is not None
    assert n.with_func.value == "yysls_rich_parse"


# ─── collect 测试 ───────────────────────────────────────────


# ─── log 测试 ───────────────────────────────────────────────


# ─── eval 测试 ──────────────────────────────────────────────


# ─── for 循环测试 ───────────────────────────────────────────


# ─── 条件表达式测试 ─────────────────────────────────────────


# ─── import / def / call proc 测试 ─────────────────────────────────


# ─── 完整工作流测试 ─────────────────────────────────────────


# ─── 隐式 eval 测试 ─────────────────────────────────────────


# ─── scan/recognize list 变量测试 ───────────────────────────


# ─── scan/recognize by 子句测试 ───────────────────────────


# ─── 换行续行测试 ─────────────────────────────────────────

def test_explicit_line_continuation():
    """测试显式续行：行尾反斜杠"""
    print("\n=== 测试显式续行 ===")
    # 行尾 \\ 续行
    program = parse_text('scan [scene].\\\n[f1, f2] as $result')
    n = program.body[0]
    assert isinstance(n, Scan)
    print("  scan [scene].\\\\\\n[f1, f2]: OK")

    # 多行续行
    program = parse_text('eval $list = [\\\n"a",\\\n"b",\\\n"c"\\\n]')
    n = program.body[0]
    assert isinstance(n, Eval)
    print("  eval $list = [\\\\\\n...\\\\\\n]: OK")


def test_implicit_line_continuation_brackets():
    """测试隐式续行：[] 内换行"""
    print("\n=== 测试隐式续行 [] ===")
    text = """\
scan [scene].[
f1,
f2,
f3
] as $result
"""
    program = parse_text(text)
    n = program.body[0]
    assert isinstance(n, Scan)
    assert n.fields is not None
    assert len(n.fields) == 3
    print("  scan [scene].[\\nf1,\\nf2\\n] as $result: OK")


def test_implicit_line_continuation_parens():
    """测试隐式续行：() 内换行"""
    print("\n=== 测试隐式续行 () ===")
    text = """\
eval $result = func(
"arg1",
"arg2"
)
"""
    program = parse_text(text)
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "func"
    assert len(n.func_args) == 2
    print("  eval $result = func(\\n...\\n): OK")


def test_implicit_line_continuation_braces():
    """测试隐式续行：{} 内换行"""
    print("\n=== 测试隐式续行 {} ===")
    text = """\
eval $dict = {
}
"""
    program = parse_text(text)
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "__dict__"
    print("  eval $dict = {\\n}: OK")


def test_no_continuation_outside_brackets():
    """测试括号外换行仍为语句终结"""
    print("\n=== 测试括号外换行 ===")
    text = """\
scan [scene].[f1] as $r1
scan [scene].[f2] as $r2
"""
    program = parse_text(text)
    assert len(program.body) == 2
    print("  括号外换行 = 语句终结: OK")


def test_crlf_line_continuation():
    """测试 CRLF 换行符的显式续行"""
    print("\n=== 测试 CRLF 续行 ===")
    # CRLF 续行：\\\r\n
    program = parse_text('scan [scene].\\\r\n[f1, f2] as $result')
    n = program.body[0]
    assert isinstance(n, Scan)
    print("  scan [scene].\\\\\\r\\n[f1, f2]: OK")

    # 多行 CRLF 续行
    program = parse_text('eval $list = [\\\r\n"a",\\\r\n"b"\\\r\n]')
    n = program.body[0]
    assert isinstance(n, Eval)
    print("  eval $list = [\\\\\\r\\n...\\\\\\r\\n]: OK")


# ─── 主入口 ─────────────────────────────────────────────────

def test_replay_input_trace():
    program = parse_text('replay input_trace "lvtrace/abc.lvtrace"')
    node = program.body[0]
    assert isinstance(node, ReplayInputTrace)
    assert node.path == "lvtrace/abc.lvtrace"
