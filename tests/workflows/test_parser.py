"""DSL 解析器验证测试"""

from pathlib import Path

import pytest
from lark.exceptions import LarkError, VisitError

from lvjiang.workflows.grammar import (
    And,
    ByClause,
    CallProc,
    Click,
    Collect,
    Contains,
    CoordPoint,
    Drag,
    EntityRef,
    Equals,
    Eval,
    EvalFieldChainAssign,
    FieldAccess,
    For,
    FuncCall,
    Goto,
    GreaterThan,
    If,
    InList,
    IsEmpty,
    Label,
    Literal,
    Log,
    Loop,
    Move,
    Not,
    NumericEqual,
    PanelRef,
    Place,
    Press,
    ProcDef,
    Program,
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

# ─── 现有 .wf 文件验证 ─────────────────────────────────────

def test_workflow_parser():
    """测试读取所有注册的 workflow 文件并解析"""
    print("\n=== 测试所有 workflow 文件解析 ===")
    wf_dir = Path("config/system/workflows")
    wf_files = list(wf_dir.rglob("*.wf"))
    print(f"  找到 {len(wf_files)} 个 .wf 文件")

    for wf_path in sorted(wf_files):
        program = parse_file(wf_path)
        stmt_count = len(program.body)
        print(f"  ✓ {wf_path} ({stmt_count} statements)")
        assert isinstance(program, Program)

    print(f"  全部 {len(wf_files)} 个文件解析成功")


# ─── click 指令测试 ─────────────────────────────────────────

def test_click_scene_ref():
    """测试 click [scene].[region]（静态点击）"""
    print("\n=== 测试 click [scene].[region] ===")

    program = parse_text("click [game_main_page].[menu]")
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, EntityRef)
    assert n.target.scene == "game_main_page"
    assert n.target.entity == "menu"
    print("  click [scene].[region]: OK")


def test_click_dynamic_region():
    """测试 click [scene].$var（动态 region）"""
    print("\n=== 测试 click [scene].$var ===")

    program = parse_text("click [scene].$tune_pos")
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, EntityRef)
    assert n.target.scene == "scene"
    assert isinstance(n.target.entity, VarRef)
    assert n.target.entity.name == "tune_pos"
    print("  click [scene].$var: OK")


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

def test_click_default_button_is_left():
    """省略鼠标键时默认 left，不影响任何既有脚本"""
    program = parse_text("click [scene].[region]")
    assert program.body[0].button == "left"


@pytest.mark.parametrize("name", ["left", "right", "middle", "x1", "x2"])
def test_click_explicit_button(name):
    """click 支持显式指定鼠标键：left/right/middle/x1/x2"""
    program = parse_text(f"click [scene].[region] {name}")
    n = program.body[0]
    assert isinstance(n, Click)
    assert n.button == name


def test_click_button_aliases_normalize_to_x1_x2():
    """back/forward 是 x1/x2 的别名，解析后规范化为 x1/x2（与轨迹格式的键名统一）"""
    assert parse_text("click [scene].[region] back").body[0].button == "x1"
    assert parse_text("click [scene].[region] forward").body[0].button == "x2"


def test_click_button_case_insensitive():
    program = parse_text("click [scene].[region] RIGHT")
    assert program.body[0].button == "right"


def test_click_button_with_coord_target():
    program = parse_text("click (0.3, 0.4) right")
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, CoordPoint)
    assert n.button == "right"


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

def test_drag_const_or_var():
    """测试 drag 支持 const_or_var 统一语法"""
    print("\n=== 测试 drag const_or_var ===")

    # drag [scene].[arrow]
    program = parse_text("drag [scene].[arrow]")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert isinstance(n.scene, EntityRef)
    assert n.scene.scene == "scene"
    assert n.scene.entity == "arrow"
    print("  drag [scene].[arrow]: OK")

    # drag "scene"."arrow" — 字符串形式不再支持用于场景引用
    # 使用 [scene].[arrow] 形式
    program = parse_text("drag [scene].[arrow]")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert isinstance(n.scene, EntityRef)
    assert n.scene.scene == "scene"
    assert n.scene.entity == "arrow"
    print('  drag [scene].[arrow] (原 "scene"."arrow" 测试): OK')

    # drag $scene.$arrow
    program = parse_text("drag $scene.$arrow")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert isinstance(n.scene, EntityRef)
    assert isinstance(n.scene.scene, VarRef)
    assert n.scene.scene.name == "scene"
    assert isinstance(n.scene.entity, VarRef)
    assert n.scene.entity.name == "arrow"
    print("  drag $scene.$arrow: OK")

    # drag with duration
    program = parse_text("drag [scene].[arrow] 0.5")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert isinstance(n.duration, Literal)
    assert n.duration.value == 0.5
    print("  drag [scene].[arrow] 0.5: OK")

    # drag with hold
    program = parse_text("drag [scene].[arrow] 0.5 hold 0.2")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert n.hold == 0.2
    print("  drag [scene].[arrow] 0.5 hold 0.2: OK")


def test_drag_point_pair():
    """drag [scene].[point_1] [scene].[point_2] — 两个命名点之间拖拽"""
    print("\n=== 测试 drag 点对模式 ===")

    # 静态场景名 + 静态点名
    program = parse_text("drag [game_login_page].[point_1] [game_login_page].[point_2]")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert n.from_scene_ref is not None
    assert n.to_scene_ref is not None
    assert n.from_scene_ref.scene == "game_login_page"
    assert n.from_scene_ref.entity == "point_1"
    assert n.to_scene_ref.scene == "game_login_page"
    assert n.to_scene_ref.entity == "point_2"
    print("  drag [scene].[p1] [scene].[p2]: OK")

    # 跨场景点对
    program = parse_text("drag [scene_a].[pt1] [scene_b].[pt2]")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert n.from_scene_ref.scene == "scene_a"
    assert n.from_scene_ref.entity == "pt1"
    assert n.to_scene_ref.scene == "scene_b"
    assert n.to_scene_ref.entity == "pt2"
    print("  drag 跨场景点对: OK")

    # 动态变量场景名和点名
    program = parse_text("drag $scene.$from_pt $scene.$to_pt")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert isinstance(n.from_scene_ref.scene, VarRef)
    assert n.from_scene_ref.scene.name == "scene"
    assert isinstance(n.from_scene_ref.entity, VarRef)
    assert n.from_scene_ref.entity.name == "from_pt"
    assert isinstance(n.to_scene_ref.scene, VarRef)
    assert isinstance(n.to_scene_ref.entity, VarRef)
    print("  drag $scene.$from_pt $scene.$to_pt: OK")

    # 带 duration 和 hold
    program = parse_text("drag [s].[p1] [s].[p2] 0.5 hold 0.2")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert n.from_scene_ref is not None
    assert n.to_scene_ref is not None
    assert isinstance(n.duration, Literal)
    assert n.duration.value == 0.5
    assert n.hold == 0.2
    print("  drag 点对带 duration + hold: OK")

    # 带 wait 子句
    program = parse_text("drag [s].[p1] [s].[p2] after wait @step_interval")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Drag)
    assert isinstance(program.body[1], Wait)
    print("  drag 点对带 after wait: OK")


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

def test_wait_tuple_mixed_var_var():
    """wait ($lo, $hi) → TupleLiteral with VarRef + VarRef"""
    program = parse_text("wait ($lo, $hi)")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, TupleLiteral)
    assert isinstance(n.delay.elements[0], VarRef)
    assert n.delay.elements[0].name == "lo"
    assert isinstance(n.delay.elements[1], VarRef)
    assert n.delay.elements[1].name == "hi"


def test_wait_tuple_mixed_lit_var():
    """wait (1, $hi) → TupleLiteral with Literal + VarRef"""
    program = parse_text("wait (1, $hi)")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, TupleLiteral)
    assert isinstance(n.delay.elements[0], Literal)
    assert n.delay.elements[0].value == 1.0
    assert isinstance(n.delay.elements[1], VarRef)
    assert n.delay.elements[1].name == "hi"


def test_wait_tuple_mixed_var_lit():
    """wait ($lo, 2) → TupleLiteral with VarRef + Literal"""
    program = parse_text("wait ($lo, 2)")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, TupleLiteral)
    assert isinstance(n.delay.elements[0], VarRef)
    assert n.delay.elements[0].name == "lo"
    assert isinstance(n.delay.elements[1], Literal)
    assert n.delay.elements[1].value == 2.0


def test_eval_tuple_mixed():
    """eval $var = ($a, $b) / eval $var = (1, $b) → Eval(__tuple__)"""
    # eval $var = (1, $b)
    program = parse_text("eval $v = (1, $b)")
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "__tuple__"
    assert isinstance(n.func_args[0], Literal)
    assert n.func_args[0].value == 1.0
    assert isinstance(n.func_args[1], VarRef)
    assert n.func_args[1].name == "b"

    # eval $var = ($a, $b)
    program = parse_text("eval $v = ($a, $b)")
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "__tuple__"
    assert isinstance(n.func_args[0], VarRef)
    assert n.func_args[0].name == "a"
    assert isinstance(n.func_args[1], VarRef)
    assert n.func_args[1].name == "b"


# ─── wait stable 指令测试（参数化） ────────────────────────────

@pytest.mark.parametrize("dsl,expected", [
    ("wait stable 5",
     {"timeout": 5.0, "threshold": 0.02, "interval": 0.3, "stable_duration": 0.5, "area": None}),
    ("wait stable 3 threshold 0.05",
     {"timeout": 3.0, "threshold": 0.05, "interval": 0.3}),
    ("wait stable 3 interval 0.5",
     {"timeout": 3.0, "threshold": 0.02, "interval": 0.5}),
    ("wait stable 5 threshold 0.03 interval 0.5 duration 1.0 least 0.3",
     {"timeout": 5.0, "threshold": 0.03, "interval": 0.5, "stable_duration": 1.0, "least": 0.3}),
    ("wait stable 5 least 0.3 duration 0.5 interval 0.3 threshold 0.01",
     {"timeout": 5.0, "threshold": 0.01, "interval": 0.3, "stable_duration": 0.5, "least": 0.3}),
    ("wait stable 5 on [equip_page].[bag_area]",
     {"timeout": 5.0, "area": ("equip_page", "bag_area")}),
    ("wait stable 5 on [equip_page].[bag_area] threshold 0.05 interval 0.2",
     {"timeout": 5.0, "threshold": 0.05, "interval": 0.2, "area": ("equip_page", "bag_area")}),
])
def test_wait_stable(dsl, expected):
    """wait_stable 各参数组合解析正确"""
    program = parse_text(dsl)
    n = program.body[0]
    assert isinstance(n, WaitStable)
    for key, val in expected.items():
        if key == "area" and val is not None:
            assert isinstance(n.area, EntityRef)
            assert n.area.scene == val[0]
            assert n.area.entity == val[1]
        else:
            assert getattr(n, key) == val


def test_click_after_wait_stable_on_region():
    """click [x].[y] after wait stable 5 on [scene].[region] — wait_clause 组合语法"""
    program = parse_text("click [main].[btn] after wait stable 5 on [equip_page].[bag_area]")
    # click after wait stable 展开为 [Click, WaitStable]
    assert len(program.body) == 2
    assert isinstance(program.body[0], Click)
    ws = program.body[1]
    assert isinstance(ws, WaitStable)
    assert isinstance(ws.area, EntityRef)
    assert ws.area.scene == "equip_page"
    assert ws.area.entity == "bag_area"


# ─── click/drag wait 语法糖测试 ─────────────────────────────

def test_click_after_wait():
    """click ... after wait -> [Click, Wait]"""
    program = parse_text("click [scene].[region] after wait @step_interval")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Click)
    assert isinstance(program.body[1], Wait)
    assert isinstance(program.body[1].delay, Literal)
    assert program.body[1].delay.value == "step_interval"


def test_click_before_wait():
    """click ... before wait -> [Wait, Click]"""
    program = parse_text("click [scene].[region] before wait 0.5")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Click)
    assert isinstance(program.body[0].delay, Literal)
    assert program.body[0].delay.value == 0.5
    # 显式 wait_clause 应抑制默认延迟
    assert program.body[1].suppress_defaults is True


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


def test_click_after_wait_var():
    """click ... after wait $var -> [Click, Wait(VarRef)]"""
    program = parse_text("click [scene].[region] after wait $myvar")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Click)
    assert isinstance(program.body[1], Wait)
    assert isinstance(program.body[1].delay, VarRef)
    assert program.body[1].delay.name == "myvar"
    # 显式 wait_clause 应抑制默认延迟
    assert program.body[0].suppress_defaults is True


def test_click_no_wait():
    """无 wait 子句的 click 行为不变（返回单节点）"""
    program = parse_text("click [scene].[region]")
    assert len(program.body) == 1
    assert isinstance(program.body[0], Click)


def test_click_after_wait_stable():
    """click ... after wait stable -> [Click, WaitStable]"""
    program = parse_text("click [scene].[region] after wait stable 8 least 1.0")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Click)
    assert isinstance(program.body[1], WaitStable)
    assert program.body[1].timeout == 8.0
    assert program.body[1].least == 1.0


def test_click_before_wait_stable():
    """click ... before wait stable -> [WaitStable, Click]"""
    program = parse_text("click [scene].[region] before wait stable 5")
    assert len(program.body) == 2
    assert isinstance(program.body[0], WaitStable)
    assert isinstance(program.body[1], Click)
    assert program.body[0].timeout == 5.0


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

def test_move_scene_ref():
    program = parse_text("move to [scene].[region]")
    assert len(program.body) == 1
    node = program.body[0]
    assert isinstance(node, Move)
    assert isinstance(node.target, EntityRef)
    assert node.target.scene == "scene"
    assert node.target.entity == "region"


def test_move_panel_target():
    program = parse_text("move to [scene].[panel][1][2]")
    node = program.body[0]
    assert isinstance(node, Move)
    assert isinstance(node.target, PanelRef)
    assert node.target.scene == "scene"
    assert node.target.panel == "panel"
    assert node.target.row == 1
    assert node.target.col == 2


def test_move_coord_target():
    program = parse_text("move to (0.5, 0.3)")
    node = program.body[0]
    assert isinstance(node, Move)
    assert isinstance(node.target, CoordPoint)
    assert node.target.rx == 0.5
    assert node.target.ry == 0.3


def test_move_var_target():
    program = parse_text("move to $var")
    node = program.body[0]
    assert isinstance(node, Move)
    assert isinstance(node.target, VarRef)
    assert node.target.name == "var"


def test_move_after_wait():
    """move ... after wait -> [Move, Wait]"""
    program = parse_text("move to [scene].[region] after wait 0.5")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Move)
    assert isinstance(program.body[1], Wait)


def test_move_before_wait():
    """move ... before wait -> [Wait, Move]"""
    program = parse_text("move to [scene].[region] before wait 0.3")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Move)


def test_move_around_wait():
    """move ... around wait -> [Wait, Move, Wait]"""
    program = parse_text("move to [scene].[region] around wait 0.5")
    assert len(program.body) == 3
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Move)
    assert isinstance(program.body[2], Wait)


def test_place_coord_target():
    program = parse_text("place (0.25, 0.75)")
    node = program.body[0]
    assert isinstance(node, Place)
    assert node.target == CoordPoint(0.25, 0.75)


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


@pytest.mark.parametrize(
    "source",
    [
        "click (-0.1, 0.5)",
        "place (0.5, 1.1)",
        "move to (1.01, 0.5)",
        "move (0.5, -0.1) by (0.2, 0)",
        "drag (0.1, 0.1) (1.2, 0.2) 0.1",
    ],
)
def test_absolute_coordinates_must_stay_in_unit_range(source):
    with pytest.raises(VisitError, match="超出"):
        parse_text(source)


@pytest.mark.parametrize(
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


def test_scroll_down_no_target():
    """scroll down — 无目标，默认数量 1"""
    program = parse_text("scroll down")
    node = program.body[0]
    assert isinstance(node, Scroll)
    assert node.direction == "down"
    assert node.target is None
    assert node.amount == 1


def test_scroll_up_no_target():
    """scroll up — 无目标，默认数量 1"""
    program = parse_text("scroll up")
    node = program.body[0]
    assert isinstance(node, Scroll)
    assert node.direction == "up"
    assert node.target is None
    assert node.amount == 1


def test_scroll_down_with_amount():
    """scroll down 3 — 无目标，数量 3"""
    program = parse_text("scroll down 3")
    node = program.body[0]
    assert isinstance(node, Scroll)
    assert node.direction == "down"
    assert node.target is None
    assert node.amount == 3


def test_scroll_up_with_target():
    """scroll [scene].[region] up — 有目标，默认数量"""
    program = parse_text("scroll [scene].[region] up")
    node = program.body[0]
    assert isinstance(node, Scroll)
    assert node.direction == "up"
    assert isinstance(node.target, EntityRef)
    assert node.target.scene == "scene"
    assert node.target.entity == "region"
    assert node.amount == 1


def test_scroll_down_with_target_and_amount():
    """scroll [scene].[panel][1][2] down 5 — panel 目标 + 数量"""
    program = parse_text("scroll [scene].[panel][1][2] down 5")
    node = program.body[0]
    assert isinstance(node, Scroll)
    assert node.direction == "down"
    assert isinstance(node.target, PanelRef)
    assert node.target.scene == "scene"
    assert node.target.panel == "panel"
    assert node.target.row == 1
    assert node.target.col == 2
    assert node.amount == 5


def test_scroll_with_var_amount():
    """scroll [scene].[region] down $n — 变量数量"""
    program = parse_text("scroll [scene].[region] down $n")
    node = program.body[0]
    assert isinstance(node, Scroll)
    assert node.direction == "down"
    assert isinstance(node.target, EntityRef)
    assert isinstance(node.amount, VarRef)
    assert node.amount.name == "n"


def test_scroll_with_coord_target():
    """scroll (0.5, 0.3) up — 坐标目标"""
    program = parse_text("scroll (0.5, 0.3) up")
    node = program.body[0]
    assert isinstance(node, Scroll)
    assert node.direction == "up"
    assert isinstance(node.target, CoordPoint)
    assert node.target.rx == 0.5
    assert node.target.ry == 0.3


def test_scroll_with_var_target():
    """scroll $var down — 变量目标"""
    program = parse_text("scroll $var down")
    node = program.body[0]
    assert isinstance(node, Scroll)
    assert node.direction == "down"
    assert isinstance(node.target, VarRef)
    assert node.target.name == "var"
    assert node.amount == 1


# ─── scroll wait_clause 测试 ────────────────────────────────────


def test_scroll_after_wait():
    """scroll down after wait -> [Scroll, Wait]"""
    program = parse_text("scroll down after wait 0.5")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Scroll)
    assert isinstance(program.body[1], Wait)
    assert program.body[0].direction == "down"
    assert program.body[1].delay.value == 0.5


def test_scroll_before_wait():
    """scroll [scene].[region] up before wait -> [Wait, Scroll]"""
    program = parse_text("scroll [scene].[region] up before wait @step_interval")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Scroll)
    assert program.body[1].direction == "up"


def test_scroll_around_wait():
    """scroll down 3 around wait -> [Wait, Scroll, Wait]"""
    program = parse_text("scroll down 3 around wait 0.5")
    assert len(program.body) == 3
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Scroll)
    assert isinstance(program.body[2], Wait)
    assert program.body[1].amount == 3
    # 同一参数：前后 Wait 的 delay 相同
    assert program.body[0].delay == program.body[2].delay


def test_scroll_before_after_wait():
    """scroll [scene].[region] down before wait 0.3 after wait 0.8 -> [Wait, Scroll, Wait]"""
    program = parse_text("scroll [scene].[region] down before wait 0.3 after wait 0.8")
    assert len(program.body) == 3
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Scroll)
    assert isinstance(program.body[2], Wait)
    assert program.body[0].delay.value == 0.3
    assert program.body[2].delay.value == 0.8


def test_scroll_after_wait_stable():
    """scroll down after wait stable -> [Scroll, WaitStable]"""
    program = parse_text("scroll down after wait stable 5")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Scroll)
    assert isinstance(program.body[1], WaitStable)
    assert program.body[1].timeout == 5.0


def test_drag_after_wait_stable():
    """drag ... after wait stable -> [Drag, WaitStable]"""
    program = parse_text("drag [scene].[panel] up 2 after wait stable 6")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Drag)
    assert isinstance(program.body[1], WaitStable)
    assert program.body[1].timeout == 6.0


def test_drag_after_wait():
    """drag ... after wait -> [Drag, Wait]"""
    program = parse_text("drag [scene].[panel] up 2 after wait @step_interval")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Drag)
    assert isinstance(program.body[1], Wait)
    assert program.body[1].delay.value == "step_interval"


def test_drag_before_wait():
    """drag ... before wait -> [Wait, Drag]"""
    program = parse_text("drag [scene].[panel] up 2 before wait @step_interval")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Drag)


def test_drag_around_wait():
    """drag ... around wait -> [Wait, Drag, Wait]"""
    program = parse_text("drag [scene].[panel] up 2 around wait @step_interval")
    assert len(program.body) == 3
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Drag)
    assert isinstance(program.body[2], Wait)


def test_drag_with_duration_after_wait():
    """drag ... 0.5 after wait -> [Drag(duration), Wait]"""
    program = parse_text("drag [scene].[panel] 0.5 after wait @step_interval")
    assert len(program.body) == 2
    assert isinstance(program.body[0], Drag)
    assert isinstance(program.body[1], Wait)
    # duration 应被正确解析
    assert program.body[0].duration is not None


def test_drag_no_wait():
    """无 wait 子句的 drag 行为不变"""
    program = parse_text("drag [scene].[panel] up 2")
    assert len(program.body) == 1
    assert isinstance(program.body[0], Drag)


# ─── before/after 组合语法测试 ────────────────────────────────

def test_click_before_after_wait():
    """click ... before wait X after wait Y → [Wait(X), Click, Wait(Y)]"""
    program = parse_text("click [scene].[region] before wait 0.5 after wait 1.0")
    assert len(program.body) == 3
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Click)
    assert isinstance(program.body[2], Wait)
    assert program.body[0].delay.value == 0.5
    assert program.body[2].delay.value == 1.0
    # suppress_defaults 应被设置
    assert program.body[1].suppress_defaults is True


def test_click_after_before_wait():
    """click ... after wait X before wait Y → [Wait(Y), Click, Wait(X)]（语义顺序：before 始终在前）"""
    program = parse_text("click [scene].[region] after wait 1.0 before wait 0.5")
    assert len(program.body) == 3
    # 无论书写顺序，before 在 click 前，after 在 click 后
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Click)
    assert isinstance(program.body[2], Wait)
    assert program.body[0].delay.value == 0.5  # before
    assert program.body[2].delay.value == 1.0  # after


def test_click_before_after_wait_stable():
    """click ... before wait stable 3 after wait stable 5 → [WaitStable(3), Click, WaitStable(5)]"""
    program = parse_text("click [scene].[region] before wait stable 3 after wait stable 5")
    assert len(program.body) == 3
    assert isinstance(program.body[0], WaitStable)
    assert isinstance(program.body[1], Click)
    assert isinstance(program.body[2], WaitStable)
    assert program.body[0].timeout == 3.0
    assert program.body[2].timeout == 5.0


def test_drag_before_after_wait():
    """drag ... before wait X after wait Y → [Wait(X), Drag, Wait(Y)]"""
    program = parse_text("drag [scene].[panel] up 2 before wait 0.3 after wait 0.8")
    assert len(program.body) == 3
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Drag)
    assert isinstance(program.body[2], Wait)
    assert program.body[0].delay.value == 0.3
    assert program.body[2].delay.value == 0.8
    assert program.body[1].suppress_defaults is True


def test_click_wait_in_for_loop():
    """for 体内的 click ... after wait 应展平为多条语句"""
    program = parse_text("for s in [\"a\", \"b\"]\nclick [a].[x] after wait 0.5\nend")
    for_node = program.body[0]
    assert isinstance(for_node, For)
    # for 体应包含 2 条语句：Click + Wait（而非一个 list）
    assert len(for_node.body) == 2
    assert isinstance(for_node.body[0], Click)
    assert isinstance(for_node.body[1], Wait)


def test_click_wait_in_if_body():
    """if 体内的 click ... before wait 应展平"""
    program = parse_text("if $x\nclick [a].[b] before wait 0.3\nend")
    if_node = program.body[0]
    assert isinstance(if_node, If)
    assert len(if_node.then_body) == 2
    assert isinstance(if_node.then_body[0], Wait)
    assert isinstance(if_node.then_body[1], Click)


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


@pytest.mark.parametrize("code", [
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


def test_click_wait_in_loop_body():
    """loop 体内的 click ... around wait 应展平为 3 条语句"""
    program = parse_text("loop 3\nclick [a].[b] around wait 0.5\nend")
    loop_node = program.body[0]
    assert isinstance(loop_node, Loop)
    assert len(loop_node.body) == 3
    assert isinstance(loop_node.body[0], Wait)
    assert isinstance(loop_node.body[1], Click)
    assert isinstance(loop_node.body[2], Wait)


def test_click_wait_in_def_body():
    """def 体内的 click ... after wait 应展平为多条语句"""
    program = parse_text("def my_proc()\nclick [a].[b] after wait 0.5\nlog \"done\"\nend")
    proc = program.procs["my_proc"]
    assert isinstance(proc, ProcDef)
    # def 体应包含 3 条语句：Click + Wait + Log（而非 list + Log）
    assert len(proc.body) == 3
    assert isinstance(proc.body[0], Click)
    assert isinstance(proc.body[1], Wait)
    assert isinstance(proc.body[2], Log)


# ─── press wait_clause 测试 ────────────────────────────────────

def test_press_after_wait():
    """press "KEY" after wait -> [Press, Wait]"""
    program = parse_text('press "A" after wait 0.5')
    assert len(program.body) == 2
    assert isinstance(program.body[0], Press)
    assert isinstance(program.body[1], Wait)
    assert program.body[0].key == "A"
    assert program.body[1].delay.value == 0.5


def test_press_before_wait():
    """press "KEY" before wait -> [Wait, Press]"""
    program = parse_text('press "A" before wait @step_interval')
    assert len(program.body) == 2
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Press)
    assert program.body[0].delay.value == "step_interval"


def test_press_around_wait():
    """press "KEY" around wait -> [Wait, Press, Wait]"""
    program = parse_text('press "A" around wait (0.3, 0.8)')
    assert len(program.body) == 3
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Press)
    assert isinstance(program.body[2], Wait)
    # 同一参数：前后 Wait 的 delay 相同
    assert program.body[0].delay == program.body[2].delay


def test_press_before_after_wait():
    """press "KEY" before wait X after wait Y -> [Wait(X), Press, Wait(Y)]"""
    program = parse_text('press "SHIFT" before wait 0.3 after wait 1.0')
    assert len(program.body) == 3
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Press)
    assert isinstance(program.body[2], Wait)
    assert program.body[0].delay.value == 0.3
    assert program.body[2].delay.value == 1.0
    assert program.body[1].key == "SHIFT"


def test_press_hold_after_wait():
    """press "KEY" hold N after wait -> [Press(hold), Wait]"""
    program = parse_text('press "W" hold 2.0 after wait 0.5')
    assert len(program.body) == 2
    assert isinstance(program.body[0], Press)
    assert isinstance(program.body[1], Wait)
    assert program.body[0].mode == PressMode.HOLD
    assert program.body[0].duration == 2.0


def test_press_down_before_wait():
    """press "KEY" down before wait -> [Wait, Press(down)]"""
    program = parse_text('press "CTRL" down before wait 0.2')
    assert len(program.body) == 2
    assert isinstance(program.body[0], Wait)
    assert isinstance(program.body[1], Press)
    assert program.body[1].mode == PressMode.DOWN


def test_press_no_wait():
    """无 wait 子句的 press 行为不变"""
    program = parse_text('press "A"')
    assert len(program.body) == 1
    assert isinstance(program.body[0], Press)


def test_press_inline_combo():
    program = parse_text('press "SHIFT" + "`"\n')
    assert program.body[0].key == "SHIFT"
    assert program.body[0].keys == ("SHIFT", "`")


def test_press_after_wait_stable():
    """press "KEY" after wait stable -> [Press, WaitStable]"""
    program = parse_text('press "A" after wait stable 5')
    assert len(program.body) == 2
    assert isinstance(program.body[0], Press)
    assert isinstance(program.body[1], WaitStable)
    assert program.body[1].timeout == 5.0


# ─── scan/recognize 测试 ────────────────────────────────────

def test_scan_as_required():
    """测试 scan 必须带 as，且 as 后为 $var"""
    print("\n=== 测试 scan as ===")

    # 带 as $var 的 scan
    program = parse_text("scan [scene1].[field1, field2] as $result")
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, Scan)
    assert isinstance(n.target, VarRef)
    assert n.target.name == "result"
    print("  scan as $var: OK")

    # 不带 as 的 scan 应该解析失败
    with pytest.raises(LarkError):
        parse_text("scan [scene1]")
    print("  scan 无 as 正确报错: OK")


def test_scan_dynamic_scene():
    """测试 scan 支持动态场景名"""
    print("\n=== 测试 scan 动态场景名 ===")

    # scan $scene.[field] as $var
    program = parse_text("scan $scene.[field] as $result")
    n = program.body[0]
    assert isinstance(n, Scan)
    assert isinstance(n.scene, EntityRef)
    assert isinstance(n.scene.scene, VarRef)
    assert n.scene.scene.name == "scene"
    print("  scan $scene.[field] as $var: OK")

    # scan "scene".[field] — 字符串形式不再支持用于场景引用
    # 使用 [scene].[field] 形式
    program = parse_text("scan [scene].[field] as $result")
    n = program.body[0]
    assert isinstance(n, Scan)
    assert isinstance(n.scene, EntityRef)
    assert n.scene.scene == "scene"
    print('  scan [scene].[field] as $var (原 "scene".[field] 测试): OK')


def test_recognize():
    """测试 recognize 语法"""
    print("\n=== 测试 recognize ===")

    program = parse_text("recognize [material_grid] as $mats")
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert isinstance(n.scene, EntityRef)
    assert n.scene.scene == "material_grid"
    assert isinstance(n.target, VarRef)
    assert n.target.name == "mats"
    print('  recognize [material_grid] as $mats: OK')

    # 不带 as 的 recognize 应该解析失败
    with pytest.raises(LarkError):
        parse_text("recognize [material_grid]")
    print("  recognize 无 as 正确报错: OK")


def test_recognize_dynamic_scene():
    """测试 recognize 支持动态场景名"""
    print("\n=== 测试 recognize 动态场景名 ===")

    # recognize $scene.[field] as $var
    program = parse_text("recognize $scene.[field] as $result")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert isinstance(n.scene, EntityRef)
    assert isinstance(n.scene.scene, VarRef)
    assert n.scene.scene.name == "scene"
    print("  recognize $scene.[field] as $var: OK")


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

    # rich + by 子句（语法允许，运行时 by 优先）
    program = parse_text('recognize [s].[f1] as rich $m by equals "text"')
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.rich is True
    assert n.by is not None
    print("  recognize [s].[f1] as rich $var by equals ...: OK")

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


def test_recognize_rich_no_regression():
    """回归测试：rich 关键字不影响其他标识符和字符串常量语法"""
    # "rich" 作为字段名
    program = parse_text("recognize [s].[rich] as $var")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.rich is False
    assert n.fields[0].value == "rich"

    # 字符串常量 panel 语法（无 rich）— 改用 [sc].[pn] 形式
    program = parse_text('recognize [sc].[pn][1][2] as $cell')
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert isinstance(n.scene, PanelRef)
    assert n.scene.scene == "sc"
    assert n.scene.panel == "pn"
    assert n.rich is False

    # 字符串常量 panel 语法（有 rich）— 改用 [sc].[pn] 形式
    program = parse_text('recognize [sc].[pn][1][2] as rich $cell')
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert isinstance(n.scene, PanelRef)
    assert n.scene.scene == "sc"
    assert n.scene.panel == "pn"
    assert n.rich is True


def test_recognize_with_clause():
    """测试 recognize as rich with func_name 语法"""
    # region 模式 + rich + with
    program = parse_text("recognize [s].[f1] as rich $mats with yysls_rich_parse")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.rich is True
    assert n.with_func is not None
    assert isinstance(n.with_func, Literal)
    assert n.with_func.value == "yysls_rich_parse"

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

    # rich + with + by + group + where 全组合（with 在末尾）
    program = parse_text(
        'recognize [s].[f1] as rich $m by equals "x" on group "g" where confidence >= 0.5 with my_func'
    )
    n = program.body[0]
    assert n.rich is True
    assert n.with_func is not None
    assert n.with_func.value == "my_func"
    assert n.by is not None
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


def test_recognize_group_list_literal():
    """on group 支持列表常量 ["a", "b"] 直接内联多分组"""
    # 列表常量
    program = parse_text('recognize [s].[f1] as rich $m on group ["a", "b"]')
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert isinstance(n.group, list)
    assert len(n.group) == 2
    assert isinstance(n.group[0], Literal) and n.group[0].value == "a"
    assert isinstance(n.group[1], Literal) and n.group[1].value == "b"

    # 单字符串（向后兼容）
    program = parse_text('recognize [s].[f1] as $m on group "调律材料"')
    n = program.body[0]
    assert isinstance(n.group, Literal) and n.group.value == "调律材料"

    # 变量引用（向后兼容）
    program = parse_text('recognize [s].[f1] as $m on group $groups')
    n = program.body[0]
    assert isinstance(n.group, VarRef) and n.group.name == "groups"

    # 全组合：by + on group list + where + with
    program = parse_text(
        'recognize [s].[f1] as rich $m by equals "x" on group ["g1", "g2"] where confidence >= 0.5 with my_func'
    )
    n = program.body[0]
    assert n.rich is True
    assert n.by is not None
    assert isinstance(n.group, list) and len(n.group) == 2
    assert n.where is not None
    assert n.with_func is not None

    # panel 模式 + 列表常量
    program = parse_text('recognize [s].[p][1][2] as rich $m on group ["x", "y"]')
    n = program.body[0]
    assert isinstance(n.scene, PanelRef)
    assert isinstance(n.group, list)


def test_recognize_panel_range_index():
    """recognize [scene].[panel][r1...r2][c1...c2] — 范围索引"""
    # 基本范围
    program = parse_text('recognize [s].[p][1...2][1...6] as rich $m')
    n = program.body[0]
    assert isinstance(n, Recognize)
    ref = n.scene
    assert isinstance(ref, PanelRef)
    assert ref.row == (1, 2)
    assert ref.col == (1, 6)
    assert n.rich is True

    # 单索引向后兼容
    program = parse_text('recognize [s].[p][1][2] as $m')
    n = program.body[0]
    assert n.scene.row == 1 and n.scene.col == 2

    # 变量索引向后兼容
    program = parse_text('recognize [s].[p][$r][$c] as $m')
    n = program.body[0]
    assert isinstance(n.scene.row, VarRef) and n.scene.row.name == "r"

    # 范围端点支持变量
    program = parse_text('recognize [s].[p][1...$n][$start...6] as $m')
    n = program.body[0]
    assert n.scene.row == (1, VarRef("n"))
    assert n.scene.col == (VarRef("start"), 6)

    # 全组合：range + rich + group list + where + with
    program = parse_text(
        'recognize [s].[p][1...2][1...6] as rich $r '
        'on group ["a", "b"] where confidence >= 0.65 with my_func'
    )
    n = program.body[0]
    assert n.scene.row == (1, 2)
    assert isinstance(n.group, list)
    assert n.where is not None
    assert n.with_func is not None


def test_scan_panel_range_index():
    """scan [scene].[panel][r1...r2][c1...c2] — 范围索引"""
    # 基本范围
    program = parse_text('scan [s].[p][1...2][1...6] as $v')
    n = program.body[0]
    assert isinstance(n, Scan)
    ref = n.scene
    assert isinstance(ref, PanelRef)
    assert ref.row == (1, 2)
    assert ref.col == (1, 6)

    # 单索引向后兼容
    program = parse_text('scan [s].[p][1][2] as $v')
    n = program.body[0]
    assert n.scene.row == 1 and n.scene.col == 2

    # 变量索引向后兼容
    program = parse_text('scan [s].[p][$r][$c] as $v')
    n = program.body[0]
    assert isinstance(n.scene.row, VarRef) and n.scene.row.name == "r"

    # 范围端点支持变量
    program = parse_text('scan [s].[p][1...$n][$start...6] as $v')
    n = program.body[0]
    assert n.scene.row == (1, VarRef("n"))
    assert n.scene.col == (VarRef("start"), 6)

    # 范围 + by 子句
    program = parse_text('scan [s].[p][1...3][1...4] as $v by contains "文本"')
    n = program.body[0]
    assert n.scene.row == (1, 3)
    assert n.by is not None

    # 范围 + where 子句
    program = parse_text('scan [s].[p][1...2][1...6] as $v where confidence >= 0.8')
    n = program.body[0]
    assert n.where is not None


def test_recognize_full_by():
    """recognize ... full by ... — 全量匹配取最高置信度"""
    # Region 模式 + full by
    program = parse_text('recognize [s].[f1, f2] as $v full by equals "材料"')
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.by is not None
    assert n.by.full is True
    assert n.by.match_mode == "equals"

    # Panel 模式 + full by
    program = parse_text('recognize [s].[p] as $v full by contains "材料"')
    n = program.body[0]
    assert n.by is not None
    assert n.by.full is True

    # 普通 by（无 full）向后兼容
    program = parse_text('recognize [s].[f1] as $v by equals "材料"')
    n = program.body[0]
    assert n.by is not None
    assert n.by.full is False

    # full by + where 组合
    program = parse_text('recognize [s].[f1, f2] as $v full by equals "材料" where confidence >= 0.8')
    n = program.body[0]
    assert n.by.full is True
    assert n.where is not None

    # full by + on group 组合
    program = parse_text('recognize [s].[f1] as $v full by equals "材料" on group ["分组A", "分组B"]')
    n = program.body[0]
    assert n.by.full is True
    assert isinstance(n.group, list)


# ─── collect 测试 ───────────────────────────────────────────

def test_collect():
    """测试 collect 语法"""
    print("\n=== 测试 collect ===")

    # collect $var
    program = parse_text("collect $result")
    n = program.body[0]
    assert isinstance(n, Collect)
    assert isinstance(n.source, VarRef)
    assert n.source.name == "result"
    assert n.alias is None
    assert n.alias_var is None
    print("  collect $var: OK")

    # collect $var as "label"
    program = parse_text('collect $result as "label"')
    n = program.body[0]
    assert isinstance(n, Collect)
    assert isinstance(n.source, VarRef)
    assert n.source.name == "result"
    assert n.alias == "label"
    assert n.alias_var is None
    print('  collect $var as "label": OK')

    # collect $var as $alias（动态别名）
    program = parse_text("collect $result as $alias")
    n = program.body[0]
    assert isinstance(n, Collect)
    assert isinstance(n.source, VarRef)
    assert n.source.name == "result"
    assert n.alias is None
    assert isinstance(n.alias_var, VarRef)
    assert n.alias_var.name == "alias"
    print("  collect $var as $alias: OK")


# ─── log 测试 ───────────────────────────────────────────────

def test_log():
    """测试 log 支持多种参数形式"""
    print("\n=== 测试 log ===")

    # log "string"
    program = parse_text('log "hello"')
    n = program.body[0]
    assert isinstance(n, Log)
    assert isinstance(n.message, Literal)
    assert n.message.value == "hello"
    print('  log "string": OK')

    # log $var
    program = parse_text("log $var")
    n = program.body[0]
    assert isinstance(n, Log)
    assert isinstance(n.message, VarRef)
    assert n.message.name == "var"
    print("  log $var: OK")

    # log $dict.field
    program = parse_text("log $dict.field")
    n = program.body[0]
    assert isinstance(n, Log)
    assert isinstance(n.message, FieldAccess)
    print("  log $dict.field: OK")

    # log func(...)
    program = parse_text('log concat("a", "b")')
    n = program.body[0]
    assert isinstance(n, Log)
    assert isinstance(n.message, FuncCall)
    assert n.message.func_name == "concat"
    print("  log func(...): OK")


# ─── eval 测试 ──────────────────────────────────────────────

def test_eval_with_var():
    """测试 eval 使用 $var 参数"""
    print("\n=== 测试 eval ===")

    program = parse_text("eval $result = is_good_equip($scan_result)")
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "is_good_equip"
    assert n.target == "result"
    assert len(n.func_args) == 1
    assert isinstance(n.func_args[0], VarRef)
    assert n.func_args[0].name == "scan_result"
    print("  eval with $var arg: OK")


def test_eval_field_assign():
    """测试 eval 字段赋值"""
    print("\n=== 测试 eval 字段赋值 ===")

    # eval $dict.key = value
    program = parse_text('eval $dict.key = "value"')
    n = program.body[0]
    assert isinstance(n, EvalFieldChainAssign)
    assert isinstance(n.target, FieldAccess)
    print("  eval $dict.key = value: OK")

    # eval $dict.$key = value（动态 key）
    program = parse_text('eval $dict.$key = "value"')
    n = program.body[0]
    assert isinstance(n, EvalFieldChainAssign)
    print("  eval $dict.$key = value: OK")

    # eval $dict."key" = value
    program = parse_text('eval $dict."key" = "value"')
    n = program.body[0]
    assert isinstance(n, EvalFieldChainAssign)
    print('  eval $dict."key" = value: OK')

    # eval $dict.[key] = value
    program = parse_text('eval $dict.[key] = "value"')
    n = program.body[0]
    assert isinstance(n, EvalFieldChainAssign)
    print("  eval $dict.[key] = value: OK")

    # eval $dict.a.b.c = value（链式赋值）
    program = parse_text('eval $dict.a.b.c = "value"')
    n = program.body[0]
    assert isinstance(n, EvalFieldChainAssign)
    assert isinstance(n.target, FieldAccess)
    print("  eval $dict.a.b.c = value: OK")


# ─── for 循环测试 ───────────────────────────────────────────

def test_for():
    """测试 for 循环"""
    print("\n=== 测试 for ===")

    # for with static list (quoted strings)
    program = parse_text('for x in ["a", "b", "c"]\n    log $x\nend')
    n = program.body[0]
    assert isinstance(n, For)
    assert n.var == "x"
    print('  for x in ["a", "b", "c"]: OK')

    # for with $var
    program = parse_text("for x in $list\n    log $x\nend")
    n = program.body[0]
    assert isinstance(n, For)
    assert isinstance(n.iterable, VarRef)
    print("  for x in $list: OK")

    # for with mixed list
    program = parse_text('for x in ["a", $var, "c"]\n    log $x\nend')
    n = program.body[0]
    assert isinstance(n, For)
    print('  for x in ["a", $var, "c"]: OK')


# ─── 条件表达式测试 ─────────────────────────────────────────

def test_conditions():
    """测试条件表达式"""
    print("\n=== 测试条件表达式 ===")

    # contains
    program = parse_text('if $var.field contains "text"\n    log "ok"\nend')
    n = program.body[0]
    assert isinstance(n, If)
    assert isinstance(n.condition, Contains)
    print("  if $var.field contains: OK")

    # equals
    program = parse_text('if $var.field equals "text"\n    log "ok"\nend')
    n = program.body[0]
    assert isinstance(n, If)
    assert isinstance(n.condition, Equals)
    print("  if $var.field equals: OK")

    # in
    program = parse_text('if $var.field in ["a", "b"]\n    log "ok"\nend')
    n = program.body[0]
    assert isinstance(n, If)
    assert isinstance(n.condition, InList)
    print('  if $var.field in [...]: OK')

    # $var in list
    program = parse_text('if $var in ["a", "b"]\n    log "ok"\nend')
    n = program.body[0]
    assert isinstance(n, If)
    assert isinstance(n.condition, InList)
    print('  if $var in [...]: OK')

    # is_empty
    program = parse_text("if $var.field is_empty\n    log \"ok\"\nend")
    n = program.body[0]
    assert isinstance(n, If)
    assert isinstance(n.condition, IsEmpty)
    print("  if $var.field is_empty: OK")

    # numeric comparisons
    program = parse_text("if $var.field > 100\n    log \"ok\"\nend")
    n = program.body[0]
    assert isinstance(n, If)
    assert isinstance(n.condition, GreaterThan)
    print("  if $var.field > 100: OK")

    # and/or
    program = parse_text('if $a.field contains "x" and $b.field equals "y"\n    log "ok"\nend')
    n = program.body[0]
    assert isinstance(n, If)
    assert isinstance(n.condition, And)
    print("  if ... and ...: OK")

    # not
    program = parse_text('if not $var.field contains "x"\n    log "ok"\nend')
    n = program.body[0]
    assert isinstance(n, If)
    assert isinstance(n.condition, Not)
    print("  if not ...: OK")


# ─── import / def / call proc 测试 ─────────────────────────────────

def test_import_stmt():
    """测试 import 语句"""
    print("\n=== 测试 import ===")

    program = parse_text('import "subcall/utils.wf"')
    assert len(program.imports) == 1
    assert program.imports[0].path == "subcall/utils.wf"
    print('  import "subcall/utils.wf": OK')

    # 多个 import
    program = parse_text('import "a.wf"\nimport "b.wf"')
    assert len(program.imports) == 2
    assert program.imports[1].path == "b.wf"
    print('  multiple imports: OK')


def test_def_stmt():
    """测试 def 过程定义"""
    print("\n=== 测试 def ===")

    text = """\
def greet($name)
    log $name
end
"""
    program = parse_text(text)
    assert len(program.procs) == 1
    assert "greet" in program.procs
    proc = program.procs["greet"]
    assert proc.params == ["name"]
    assert len(proc.body) == 1
    print("  def with params: OK")

    # 无参数 def
    text = """\
def do_something()
    log "hello"
end
"""
    program = parse_text(text)
    proc = program.procs["do_something"]
    assert proc.params == []
    print("  def without params: OK")


def test_call_proc_stmt():
    """测试 call proc 调用"""
    print("\n=== 测试 call proc ===")

    program = parse_text('call greet("world")')
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, CallProc)
    assert n.name == "greet"
    assert len(n.args) == 1
    print('  call proc("arg"): OK')

    # 多参数 call
    program = parse_text('call process(1, $col, "scene")')
    n = program.body[0]
    assert isinstance(n, CallProc)
    assert len(n.args) == 3
    print('  call proc(1, $var, "str"): OK')

    # 无参数 call
    program = parse_text("call do_something()")
    n = program.body[0]
    assert isinstance(n, CallProc)
    assert n.name == "do_something"
    assert n.args == []
    print("  call proc(): OK")


def test_import_def_mixed():
    """测试 import + def + call 混合"""
    print("\n=== 测试 import + def + call 混合 ===")

    text = """\
import "subcall/utils.wf"

def local_proc($x)
    log $x
end

call local_proc("hello")
call utils_func(1, 2)
"""
    program = parse_text(text)
    assert len(program.imports) == 1
    assert len(program.procs) == 1
    assert "local_proc" in program.procs
    assert len(program.body) == 2
    assert isinstance(program.body[0], CallProc)
    assert isinstance(program.body[1], CallProc)
    print("  import + def + call mixed: OK")


# ─── 完整工作流测试 ─────────────────────────────────────────

def test_if_with_scan_as():
    """测试 if 条件与 scan as 配合"""
    print("\n=== 测试 if + scan as ===")
    text = """\
scan [equip_weapon_detail] as $result
if not $result.field1 contains "调律"
    collect $result as "good"
    log "好装备"
end
"""
    program = parse_text(text)
    assert len(program.body) == 2  # scan + if
    assert isinstance(program.body[0], Scan)
    assert isinstance(program.body[1], If)

    if_node = program.body[1]
    # "if not ... contains" → Not(Contains(...))
    assert isinstance(if_node.condition, Not)
    assert isinstance(if_node.condition.operand, Contains)
    assert len(if_node.then_body) == 2  # collect + log
    print("  if + scan as: OK")


def test_else_if_basic():
    """else if 基本语法：if ... else if ... else ... end"""
    text = """\
if $x
    log "a"
else if $y
    log "b"
else
    log "c"
end
"""
    program = parse_text(text)
    assert len(program.body) == 1
    if_node = program.body[0]
    assert isinstance(if_node, If)
    assert len(if_node.then_body) == 1
    # else_body 应包含一个嵌套的 If
    assert len(if_node.else_body) == 1
    nested_if = if_node.else_body[0]
    assert isinstance(nested_if, If)
    assert len(nested_if.then_body) == 1
    # 嵌套 if 的 else_body 应包含 log "c"
    assert len(nested_if.else_body) == 1


def test_else_if_chain():
    """多个 else if 链：if ... else if ... else if ... else ... end"""
    text = """\
if $a
    log "a"
else if $b
    log "b"
else if $c
    log "c"
else
    log "d"
end
"""
    program = parse_text(text)
    assert len(program.body) == 1
    if_node = program.body[0]
    assert isinstance(if_node, If)
    # 第一层 else 包含嵌套 if
    nested1 = if_node.else_body[0]
    assert isinstance(nested1, If)
    # 第二层 else 包含另一个嵌套 if
    nested2 = nested1.else_body[0]
    assert isinstance(nested2, If)
    assert len(nested2.else_body) == 1  # log "d"


def test_else_if_no_else():
    """else if 不带最终 else：if ... else if ... end"""
    text = """\
if $x
    log "a"
else if $y
    log "b"
end
"""
    program = parse_text(text)
    assert len(program.body) == 1
    if_node = program.body[0]
    assert isinstance(if_node, If)
    assert len(if_node.else_body) == 1
    nested_if = if_node.else_body[0]
    assert isinstance(nested_if, If)
    # 嵌套 if 没有 else
    assert len(nested_if.else_body) == 0


def test_full_workflow():
    """测试完整工作流片段"""
    print("\n=== 测试完整工作流 ===")
    text = """\
scan [scene1].[field1] as $scan1
if not $scan1.field1 contains "关键词"
    log "未找到"
    return
end
click [scene1].$scan1
collect $scan1 as "output"
"""
    program = parse_text(text)
    assert len(program.body) == 4  # scan + if + click + collect
    assert isinstance(program.body[0], Scan)
    assert isinstance(program.body[1], If)
    assert isinstance(program.body[2], Click)
    assert isinstance(program.body[3], Collect)
    print("  完整工作流: OK")


def test_comment_in_if_body():
    """测试 if 块体内的 # 注释被忽略（wf-scripts/test_comment.py 合并）"""
    text = """\
loop 100
   scan [scene].[field] as $result
   if $result.[field] contains "test"
       # 这是注释
       goto label1
   end
end
@label1
collect $result
"""
    program = parse_text(text)
    # 顶层：loop + @label1 + collect
    assert len(program.body) == 3
    loop_node = program.body[0]
    assert isinstance(loop_node, Loop)
    assert loop_node.count == 100
    # loop 体：scan + if
    assert len(loop_node.body) == 2
    assert isinstance(loop_node.body[0], Scan)
    if_node = loop_node.body[1]
    assert isinstance(if_node, If)
    assert isinstance(if_node.condition, Contains)
    # if 体：只有 goto，注释已被忽略
    assert len(if_node.then_body) == 1
    assert isinstance(if_node.then_body[0], Goto)
    assert if_node.then_body[0].target == "label1"
    # @label1
    label_node = program.body[1]
    assert isinstance(label_node, Label)
    assert label_node.name == "label1"


def test_goto_with_if_else():
    """测试 goto 与 if/else 配合跳转（wf-scripts/test_goto.py 合并）"""
    text = """\
$var = 1
@label1
log concat("var = ", $var)
if $var == 1
   $var = 2
   goto label1
else
   goto label2
end
@label2
collect $var
"""
    program = parse_text(text)
    # 顶层：$var=1 + @label1 + log + if + @label2 + collect
    assert len(program.body) == 6
    assert isinstance(program.body[0], Eval)
    assert program.body[0].target == "var"
    assert isinstance(program.body[1], Label)
    assert program.body[1].name == "label1"
    assert isinstance(program.body[2], Log)
    if_node = program.body[3]
    assert isinstance(if_node, If)
    assert isinstance(if_node.condition, NumericEqual)
    # then 分支：$var=2 + goto label1
    assert len(if_node.then_body) == 2
    assert isinstance(if_node.then_body[0], EvalFieldChainAssign) or isinstance(if_node.then_body[0], Eval)
    assert isinstance(if_node.then_body[1], Goto)
    assert if_node.then_body[1].target == "label1"
    # else 分支：goto label2
    assert len(if_node.else_body) == 1
    assert isinstance(if_node.else_body[0], Goto)
    assert if_node.else_body[0].target == "label2"
    # @label2
    assert isinstance(program.body[4], Label)
    assert program.body[4].name == "label2"
    assert isinstance(program.body[5], Collect)


# ─── 隐式 eval 测试 ─────────────────────────────────────────

def test_implicit_eval():
    """测试隐式 eval：$var = value 不需要 eval 前缀"""
    print("\n=== 测试隐式 eval ===")

    # $var = "string"
    program = parse_text('$result = "hello"')
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.target == "result"
    assert n.func_name == "__literal__"
    print('  $var = "string": OK')

    # $var = 123
    program = parse_text("$count = 123")
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.target == "count"
    print("  $var = 123: OK")

    # $var = {}
    program = parse_text("$data = {}")
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "__dict__"
    print("  $var = {}: OK")

    # $var = ["a", "b", "c"]
    program = parse_text('$list = ["a", "b", "c"]')
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "__list__"
    assert len(n.func_args) == 3
    print('  $var = ["a", "b", "c"]: OK')

    # $var = func($arg)
    program = parse_text("$result = to_equipment($scan)")
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "to_equipment"
    assert n.target == "result"
    assert len(n.func_args) == 1
    assert isinstance(n.func_args[0], VarRef)
    print("  $var = func($arg): OK")

    # $var = $other
    program = parse_text("$copy = $original")
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "__expr__"
    assert n.target == "copy"
    print("  $var = $other: OK")

    # $var = $dict.field
    program = parse_text("$val = $dict.field")
    n = program.body[0]
    assert isinstance(n, Eval)
    assert n.func_name == "__expr__"
    print("  $var = $dict.field: OK")

    # $dict.key = value（隐式字段赋值）
    program = parse_text('$dict.key = "value"')
    n = program.body[0]
    assert isinstance(n, EvalFieldChainAssign)
    assert isinstance(n.target, FieldAccess)
    print("  $dict.key = value: OK")

    # $dict.$key = value（隐式动态 key 赋值）
    program = parse_text('$equipment.$slot = to_equipment($scan)')
    n = program.body[0]
    assert isinstance(n, EvalFieldChainAssign)
    print("  $dict.$key = func($arg): OK")


# ─── scan/recognize list 变量测试 ───────────────────────────

def test_scan_list_var():
    """测试 scan [scene].$list 支持列表变量"""
    print("\n=== 测试 scan list 变量 ===")

    # scan [scene].$list as $var
    program = parse_text("scan [equip_weapon_detail].$fields as $result")
    n = program.body[0]
    assert isinstance(n, Scan)
    assert isinstance(n.scene, EntityRef)
    assert n.scene.scene == "equip_weapon_detail"
    assert n.region_var is not None
    assert isinstance(n.region_var, VarRef)
    assert n.region_var.name == "fields"
    print("  scan [scene].$list as $var: OK")

    # recognize [scene].$list as $var
    program = parse_text("recognize [equip_tune_detail].$slots as $mats")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.region_var is not None
    assert isinstance(n.region_var, VarRef)
    assert n.region_var.name == "slots"
    print("  recognize [scene].$list as $var: OK")


# ─── scan/recognize by 子句测试 ───────────────────────────

def test_scan_by_equals():
    """测试 scan ... by equals"""
    print("\n=== 测试 scan by equals ===")
    program = parse_text('scan [scene].[f1, f2] as $found by equals "target"')
    n = program.body[0]
    assert isinstance(n, Scan)
    assert n.by is not None
    assert isinstance(n.by, ByClause)
    assert n.by.match_mode == "equals"
    assert isinstance(n.by.target, Literal)
    assert n.by.target.value == "target"
    print("  scan by equals: OK")


def test_scan_by_contains():
    """测试 scan ... by contains"""
    print("\n=== 测试 scan by contains ===")
    program = parse_text("scan [scene].[f1, f2] as $found by contains $keyword")
    n = program.body[0]
    assert isinstance(n, Scan)
    assert n.by is not None
    assert n.by.match_mode == "contains"
    assert isinstance(n.by.target, VarRef)
    assert n.by.target.name == "keyword"
    print("  scan by contains $var: OK")


def test_recognize_by_equals_any():
    """测试 recognize ... by equals_any"""
    print("\n=== 测试 recognize by equals_any ===")
    program = parse_text("recognize [scene].[s1, s2, s3] as $found by equals_any $materials")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.by is not None
    assert n.by.match_mode == "equals_any"
    assert isinstance(n.by.target, VarRef)
    assert n.by.target.name == "materials"
    print("  recognize by equals_any $var: OK")


def test_recognize_by_contains_any():
    """测试 recognize ... by contains_any"""
    print("\n=== 测试 recognize by contains_any ===")
    program = parse_text("recognize [scene].[s1, s2] as $found by contains_any $keywords")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert n.by is not None
    assert n.by.match_mode == "contains_any"
    assert isinstance(n.by.target, VarRef)
    assert n.by.target.name == "keywords"
    print("  recognize by contains_any $var: OK")


def test_scan_without_by():
    """测试无 by 子句时 by 为 None"""
    print("\n=== 测试 scan 无 by 子句 ===")
    program = parse_text("scan [scene].[f1] as $result")
    n = program.body[0]
    assert isinstance(n, Scan)
    assert n.by is None
    print("  scan 无 by → by=None: OK")


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


def test_mixed_line_continuation():
    """测试混合续行：显式 + 隐式"""
    print("\n=== 测试混合续行 ===")
    text = """\
scan [scene].[
f1, \\
f2
] as $result
"""
    program = parse_text(text)
    n = program.body[0]
    assert isinstance(n, Scan)
    assert len(n.fields) == 2
    print("  显式 + 隐式混合: OK")


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


if __name__ == "__main__":
    test_workflow_parser()

    # click
    test_click_scene_ref()
    test_click_dynamic_region()
    test_click_const_or_var()

    # drag
    test_drag_const_or_var()

    # wait
    test_wait()

    # scan/recognize
    test_scan_as_required()
    test_scan_dynamic_scene()
    test_recognize()
    test_recognize_dynamic_scene()

    # collect
    test_collect()

    # log
    test_log()

    # eval
    test_eval_with_var()
    test_eval_field_assign()
    test_implicit_eval()

    # scan/recognize by 子句
    test_scan_by_equals()
    test_scan_by_contains()
    test_recognize_by_equals_any()
    test_recognize_by_contains_any()
    test_scan_without_by()

    # 换行续行
    test_explicit_line_continuation()
    test_implicit_line_continuation_brackets()
    test_implicit_line_continuation_parens()
    test_implicit_line_continuation_braces()
    test_mixed_line_continuation()
    test_no_continuation_outside_brackets()

    # scan/recognize list var
    test_scan_list_var()

    # for
    test_for()

    # conditions
    test_conditions()

    # import / def / call proc
    test_import_stmt()
    test_def_stmt()
    test_call_proc_stmt()
    test_import_def_mixed()

    # full workflow
    test_if_with_scan_as()
    test_full_workflow()
    test_comment_in_if_body()
    test_goto_with_if_else()

    print("\n" + "=" * 50)
    print("ALL TESTS PASSED")
    print("=" * 50)
