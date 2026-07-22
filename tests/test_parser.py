"""DSL 解析器验证测试"""

from pathlib import Path
from lvjiang.workflows.grammar import parse_file, parse_text
from lvjiang.workflows.grammar import (
    Program, Click, Drag, Wait, Scan, Recognize, Collect, Log, Eval,
    Import, ProcDef, CallProc,
    If, For, Loop, Break, Return, Label, Goto,
    Contains, Equals, InList, IsEmpty, FieldAccess, VarRef, Literal, SceneRef,
    Not, And, Or, GreaterThan, LessThan, GreaterEqual, LessEqual, NotEqual, NumericEqual,
    EvalFieldChainAssign, FuncCall, ByClause,
)


# ─── 现有 .wf 文件验证 ─────────────────────────────────────

def test_existing_wf_files():
    """验证现有 .wf 文件能正常解析"""
    print("=== 验证现有 .wf 文件 ===")
    for wf in ["equip_analysis", "single_tuning"]:
        path = Path(f"config/system/workflows/{wf}.wf")
        program = parse_file(path)
        print(f"  {path}: {len(program.body)} 条指令 OK")
        assert isinstance(program, Program)


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
    assert isinstance(n.target, SceneRef)
    assert n.target.scene == "game_main_page"
    assert n.target.region == "menu"
    print("  click [scene].[region]: OK")


def test_click_dynamic_region():
    """测试 click [scene].$var（动态 region）"""
    print("\n=== 测试 click [scene].$var ===")

    program = parse_text("click [scene].$tune_pos")
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, SceneRef)
    assert n.target.scene == "scene"
    assert isinstance(n.target.region, VarRef)
    assert n.target.region.name == "tune_pos"
    print("  click [scene].$var: OK")


def test_click_const_or_var():
    """测试 click 支持 const_or_var 统一语法"""
    print("\n=== 测试 click const_or_var ===")
    
    # click [scene].[region]
    program = parse_text("click [scene].[region]")
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, SceneRef)
    assert n.target.scene == "scene"
    assert n.target.region == "region"
    print("  click [scene].[region]: OK")
    
    # click "scene"."region"
    program = parse_text('click "scene"."region"')
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, SceneRef)
    assert n.target.scene == "scene"
    assert n.target.region == "region"
    print('  click "scene"."region": OK')
    
    # click $scene.$region
    program = parse_text("click $scene.$region")
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, SceneRef)
    assert isinstance(n.target.scene, VarRef)
    assert n.target.scene.name == "scene"
    assert isinstance(n.target.region, VarRef)
    assert n.target.region.name == "region"
    print("  click $scene.$region: OK")
    
    # click [scene].$var
    program = parse_text("click [scene].$var")
    n = program.body[0]
    assert isinstance(n, Click)
    assert isinstance(n.target, SceneRef)
    assert n.target.scene == "scene"
    assert isinstance(n.target.region, VarRef)
    assert n.target.region.name == "var"
    print("  click [scene].$var: OK")


# ─── drag 指令测试 ──────────────────────────────────────────

def test_drag_const_or_var():
    """测试 drag 支持 const_or_var 统一语法"""
    print("\n=== 测试 drag const_or_var ===")
    
    # drag [scene].[arrow]
    program = parse_text("drag [scene].[arrow]")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert isinstance(n.scene, SceneRef)
    assert n.scene.scene == "scene"
    assert n.scene.region == "arrow"
    print("  drag [scene].[arrow]: OK")
    
    # drag "scene"."arrow"
    program = parse_text('drag "scene"."arrow"')
    n = program.body[0]
    assert isinstance(n, Drag)
    assert isinstance(n.scene, SceneRef)
    assert n.scene.scene == "scene"
    assert n.scene.region == "arrow"
    print('  drag "scene"."arrow": OK')
    
    # drag $scene.$arrow
    program = parse_text("drag $scene.$arrow")
    n = program.body[0]
    assert isinstance(n, Drag)
    assert isinstance(n.scene, SceneRef)
    assert isinstance(n.scene.scene, VarRef)
    assert n.scene.scene.name == "scene"
    assert isinstance(n.scene.region, VarRef)
    assert n.scene.region.name == "arrow"
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
    program = parse_text("wait page_refresh")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, Literal)
    assert n.delay.value == "page_refresh"
    print("  wait page_refresh: OK")
    
    # wait $var
    program = parse_text("wait $interval")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, VarRef)
    assert n.delay.name == "interval"
    print("  wait $interval: OK")
    
    # wait (min, max)
    program = parse_text("wait (1, 2)")
    n = program.body[0]
    assert isinstance(n, Wait)
    assert isinstance(n.delay, tuple)
    assert n.delay == (1.0, 2.0)
    print("  wait (1, 2): OK")


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
    try:
        parse_text("scan [scene1]")
        assert False, "应该解析失败"
    except Exception:
        print("  scan 无 as 正确报错: OK")


def test_scan_dynamic_scene():
    """测试 scan 支持动态场景名"""
    print("\n=== 测试 scan 动态场景名 ===")
    
    # scan $scene.[field] as $var
    program = parse_text("scan $scene.[field] as $result")
    n = program.body[0]
    assert isinstance(n, Scan)
    assert isinstance(n.scene, SceneRef)
    assert isinstance(n.scene.scene, VarRef)
    assert n.scene.scene.name == "scene"
    print("  scan $scene.[field] as $var: OK")
    
    # scan "scene".[field] as $var
    program = parse_text('scan "scene".[field] as $result')
    n = program.body[0]
    assert isinstance(n, Scan)
    assert isinstance(n.scene, SceneRef)
    assert n.scene.scene == "scene"
    print('  scan "scene".[field] as $var: OK')


def test_recognize():
    """测试 recognize 语法"""
    print("\n=== 测试 recognize ===")

    program = parse_text("recognize [material_grid] as $mats")
    assert len(program.body) == 1
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert isinstance(n.scene, SceneRef)
    assert n.scene.scene == "material_grid"
    assert isinstance(n.target, VarRef)
    assert n.target.name == "mats"
    print('  recognize [material_grid] as $mats: OK')

    # 不带 as 的 recognize 应该解析失败
    try:
        parse_text("recognize [material_grid]")
        assert False, "应该解析失败"
    except Exception:
        print("  recognize 无 as 正确报错: OK")


def test_recognize_dynamic_scene():
    """测试 recognize 支持动态场景名"""
    print("\n=== 测试 recognize 动态场景名 ===")
    
    # recognize $scene.[field] as $var
    program = parse_text("recognize $scene.[field] as $result")
    n = program.body[0]
    assert isinstance(n, Recognize)
    assert isinstance(n.scene, SceneRef)
    assert isinstance(n.scene.scene, VarRef)
    assert n.scene.scene.name == "scene"
    print("  recognize $scene.[field] as $var: OK")


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
    assert isinstance(n.scene, SceneRef)
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


def test_find_tune_material_wf():
    """验证改写后的 find_tune_material.wf 能正常解析（def 格式）"""
    print("\n=== 测试 find_tune_material.wf ===")
    path = Path("config/system/workflows/subcall/find_tune_material.wf")
    program = parse_file(path)
    assert len(program.body) == 0  # 内容在 def 内
    assert "find_tune_material" in program.procs
    proc = program.procs["find_tune_material"]
    assert proc.params == ["material_name"]
    assert len(proc.body) == 2  # recognize + eval
    n = proc.body[0]
    assert isinstance(n, Recognize)
    assert n.by is not None
    assert n.by.match_mode == "equals"
    assert isinstance(n.by.target, VarRef)
    assert n.by.target.name == "material_name"
    print("  find_tune_material.wf 解析 OK")


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


# ─── 主入口 ─────────────────────────────────────────────────

if __name__ == "__main__":
    # 现有文件验证
    test_existing_wf_files()
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
    test_find_tune_material_wf()
    
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
