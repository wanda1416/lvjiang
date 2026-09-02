"""DSL 静态引用搜集与 engine 启动期绑定校验测试

collect_refs 遍历 AST（含嵌套体与过程体），搜集全部静态引用（场景 + key）；
engine _execute_dsl 在解析后据此逐条校验引用是否已在当前布局绑定坐标，
未绑定直接抛 WorkflowUserError，不进入执行阶段（取代手写 required_scenes）。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.workflows.engine import WorkflowEngine, WorkflowUserError
from lvjiang.workflows.grammar import parse_file, parse_text
from lvjiang.workflows.metadata import parse_metadata
from lvjiang.workflows.workflow_references import collect_refs, collect_scene_keys

# ─── 搜集单元测试 ─────────────────────────────────────────

def _collect(text: str) -> set[str]:
    program = parse_text(text)
    return collect_scene_keys(program.body, program.procs)


def _refs(text: str) -> list:
    program = parse_text(text)
    return collect_refs(program.body, program.procs)


def test_collect_from_click_scan_recognize():
    text = (
        'click [scene_a].[btn]\n'
        'scan [scene_b].[label] as $x\n'
        'recognize [scene_c].[field] as $y\n'
    )
    assert _collect(text) == {"scene_a", "scene_b", "scene_c"}


def test_collect_from_nested_bodies():
    """if / loop / for 嵌套体内的场景引用同样被搜集"""
    text = (
        'loop 2\n'
        '    click [scene_loop].[btn]\n'
        'end\n'
        'if 1 == 1\n'
        '    click [scene_then].[btn]\n'
        'else\n'
        '    click [scene_else].[btn]\n'
        'end\n'
    )
    assert _collect(text) == {"scene_loop", "scene_then", "scene_else"}


def test_collect_from_try_and_while_bodies():
    """try / catch 与条件循环体内的引用也要搜集，否则漏检"""
    text = (
        'try\n'
        '    click [scene_try].[btn]\n'
        'catch $err\n'
        '    click [scene_catch].[btn]\n'
        'end\n'
        'loop while 1 == 1\n'
        '    click [scene_while].[btn]\n'
        'end\n'
        'loop until 1 == 1\n'
        '    click [scene_until].[btn]\n'
        'end\n'
    )
    assert _collect(text) == {
        "scene_try", "scene_catch", "scene_while", "scene_until"}


def test_collect_from_proc_body():
    """被调用的 def 过程体内的场景引用会被搜集"""
    text = (
        'def helper()\n'
        '    click [scene_in_proc].[btn]\n'
        'end\n'
        'log "top"\n'
        'call helper()\n'
    )
    assert _collect(text) == {"scene_in_proc"}


def test_uncalled_proc_excluded_by_default_but_included_for_lint():
    """未被调用的过程：执行前闸门不查，CI/编写期检查要查。

    默认（reachable_only=True）供执行前闸门用——拿不会执行的代码挡住用户
    是事故（真实案例：扫装备被江湖号令页的未绑定区域拒绝）。
    reachable_only=False 供 validate_only / CI 门禁用，库函数里的 key 拼错
    才有人发现。
    """
    text = (
        'def never_called()\n'
        '    click [scene_in_proc].[btn]\n'
        'end\n'
        'log "top"\n'
    )
    program = parse_text(text)
    assert collect_scene_keys(program.body, program.procs) == set()
    lint_refs = collect_refs(program.body, program.procs, reachable_only=False)
    assert {ref.scene for ref in lint_refs} == {"scene_in_proc"}


def test_collect_empty_when_no_scene_ref():
    assert _collect('log "hi"\nwait 0\n') == set()


def test_ref_kind_by_statement():
    """kind 决定该 key 在布局里查哪类对象：click 查区域/坐标点/面板，drag 查方向/区域"""
    refs = _refs(
        'click [s].[btn]\n'
        'drag [s].[menu_up]\n'
        'align [s].[grid]\n'
        'scan [s].[f1, f2] as $x\n'
    )
    got = {(r.key, r.kind) for r in refs}
    assert got == {
        ("btn", "click_target"), ("menu_up", "drag_target"), ("grid", "panel"),
        ("f1", "region"), ("f2", "region"),
    }


def test_dynamic_key_collected_as_scene_only():
    """key 为 $var 时运行时才知道，只记场景不记 key"""
    refs = _refs('eval $k = "btn"\nclick [s].$k\n')
    assert [(r.scene, r.key) for r in refs] == [("s", None)]


def test_ref_line_no_matches_source():
    """行号须与源文件一致 —— 多行续行语句不能把后续行号顶偏"""
    text = (
        'log "1"\n'
        'scan [s].[f1,\n'
        '    f2] as $x\n'
        'click [s].[btn]\n'
    )
    by_key = {r.key: r.line_no for r in _refs(text)}
    assert by_key["f1"] == 2
    assert by_key["btn"] == 4


# ─── 与旧 required_scenes 等价性验证 ──────────────────────

def test_daily_jianghu_matches_legacy_required_scenes():
    """daily_jianghu.wf 搜集结果应与原手写 required_scenes 一致

    waiguan_qingjing 为情境动作落地后新增的依赖。

    这里按引擎 _load_and_validate 的方式先合并 import 链的 procs 再搜集，
    断言的才是运行期真正的校验范围。只 parse_file 会漏掉靠子过程引用的
    场景（如 game_main_page 走 nav_main_to_menu、bag_detail 走
    背包子过程），那样每次把一句 click 挪进 subcall 都要改一次期望，
    却并没有任何覆盖真的丢失。
    """
    wf = SYSTEM_CONFIG_DIR / "workflows" / "daily_jianghu.wf"
    program = parse_file(wf)
    procs = dict(program.procs)
    for imported in program.imports:
        procs.update(parse_file((wf.parent / imported.path).resolve()).procs)
    scenes = collect_scene_keys(program.body, procs)
    assert scenes == {
        "activity_jianghu", "waiguan_yigui", "waiguan_qingjing",
        "general_action", "game_menu_page", "game_main_page",
        "general_control", "school_main",
        "bag_detail", "bag_item_detail",
    }


def test_daily_jianghu_reuses_shared_page_detector():
    """号令页判断统一由底层页面判断子过程提供。"""
    workflows = SYSTEM_CONFIG_DIR / "workflows"
    daily_text = (workflows / "daily_jianghu.wf").read_text(encoding="utf-8")
    detection_text = (
        workflows / "subcall" / "page_detection.wf"
    ).read_text(encoding="utf-8")

    assert daily_text.count("is_in_haoling_page()") == 3
    assert 'import "subcall/page_detection.wf"' in daily_text
    assert "scan [activity_jianghu].[label_0" not in daily_text
    assert "def is_in_haoling_page()" in detection_text
    assert (
        'scan [activity_jianghu].[label_0, haoling_label] '
        'as $found by contains "号令"'
    ) in detection_text


def test_daily_jianghu_claim_reputation_guard():
    """领奖必须受完成状态和声望上限约束；当周声望读取即写入 profile。"""
    wf = SYSTEM_CONFIG_DIR / "workflows" / "daily_jianghu.wf"
    text = wf.read_text(encoding="utf-8")
    metadata = parse_metadata(text)
    parameters = {item["name"]: item for item in metadata["parameters"]}

    assert parameters["max_claim_reputation"] == {
        "name": "max_claim_reputation",
        "label": "最大领取声望",
        "type": "number",
        "default": 1500,
        "min": 0,
    }
    assert "default $max_claim_reputation = 1500" in text
    # 声望只接受非负整数；0 合法，只有提取失败（< 0）才跳过领奖
    assert "extract_int($result.haoling_of_week)" in text
    assert "if $value < 0" in text
    assert "return -1" in text
    # 领奖后全屏奖励弹窗用通用空白区域点击关闭，避免遮挡下一轮页面校验
    claim_def = text[text.index("def claim_reward("):text.index("def claim_reward(") + 500]
    assert "click [general_control].[blank_area]" in claim_def

    completed_call = "call $task_completed = is_task_completed($text_result)"
    initial_scan = "scan [activity_jianghu].$label.[label] as $text_result"
    target_call = "call $hit = is_target_task($task_text, $targets)"
    assert "def is_task_completed($text_result)" in text
    assert text.count(completed_call) == 2  # 处理前与动作后共用同一判定
    assert text.index(initial_scan) < text.index(completed_call)
    assert text.index(completed_call) < text.index(target_call)
    assert "continue" in text[text.index(completed_call):text.index(target_call)]
    assert "def is_target_task($text, $targets)" in text
    assert "as $hit by contains_any $targets" not in text

    # 当周声望「读取即写入」：唯一读取入口是 sync_haoling_of_week，它在识别
    # 成功后直接落盘。若仍由各调用点自行 sync，未领奖 / 已达上限等分支就会
    # 漏写，profile 停留在旧值，与实时值不一致。
    assert "read_haoling_of_week" not in text  # 不得绕过唯一入口
    assert text.count("call $haoling_of_week = sync_haoling_of_week()") == 1
    # 起始进入即写入，不依赖后续是否领奖
    assert text.index("call $haoling_of_week = sync_haoling_of_week()") < text.index(
        "for idx in"
    )
    sync_proc = text.index("def sync_haoling_of_week(")
    sync_body = text[sync_proc:sync_proc + 800]
    assert "extract_int($result.haoling_of_week)" in sync_body
    assert "if $value < 0" in sync_body
    assert "call write_haoling_profile($value)" in sync_body
    assert "return -1" in sync_body  # 识别失败不写 profile

    claim_proc = text.index("def claim_completed_reward(")
    limit = text.index("if $current < $maximum", claim_proc)
    claim = text.index("call claim_reward($label, $idx)", claim_proc)
    reread = text.index("call $current = sync_haoling_of_week()", claim_proc)
    assert claim_proc < limit < claim < reread
    assert 'eval $model = profile_model("haoling_of_week")' in text
    assert 'eval profile_set("haoling_of_week", $value)' in text


# ─── engine 启动期绑定校验（集成） ────────────────────────

def _make_engine(bound_scenes: set[str], *, points=None, arrows=None,
                 panels=None, regions=None) -> WorkflowEngine:
    """构造最小引擎；bound_scenes 中的场景视为绑定了区域 btn，其余为空。

    静态检查只读绑定对象的 key / from_key / to_key，用最小对象充当即可。
    disabled 状态直接设在实例属性上，静态检查视已绑定实例为有效（不论 disabled）。
    """
    capture = MagicMock()
    capture.get_capture_size.return_value = (1920, 1080)
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(
        x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    # 默认 region 包含完整属性，供运行时使用
    default_region = SimpleNamespace(key="btn", x_ratio=0.0, y_ratio=0.0, w_ratio=0.5, h_ratio=0.5)
    layout.get_scene_regions.side_effect = lambda k: (
        [regions[k]] if k in regions else
        [default_region] if k in bound_scenes else []) if regions else (
        [default_region] if k in bound_scenes else [])
    layout.get_scene_points.side_effect = lambda k: list((points or {}).get(k, []))
    layout.get_scene_arrows.side_effect = lambda k: list((arrows or {}).get(k, []))
    layout.get_scene_panels.side_effect = lambda k: list((panels or {}).get(k, []))
    return WorkflowEngine(
        capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
        layout=layout, input_sim=MagicMock(), delay_params={},
    )


def _write_wf(wf_root, text: str, name: str = "t.wf"):
    wf = wf_root / name
    wf.write_text(text, encoding="utf-8")
    return wf


def test_missing_scene_raises_before_execution(wf_root):
    """引用未绑定场景：加载即报错，不进入执行阶段

    helper 必须真被 call —— 执行前闸门只校验可达过程（见
    workflow_references.collect_refs 的 reachable_only），
    未被调用的过程不该挡住执行。
    """
    wf = _write_wf(wf_root, (
        'def helper()\n'
        '    click [game_main_page].[btn]\n'
        'end\n'
        'log "start"\n'
        'call helper()\n'
    ))
    engine = _make_engine(bound_scenes=set())
    with pytest.raises(WorkflowUserError, match="场景未绑定任何坐标"):
        engine.execute(wf)


def test_bound_scene_passes_validation(wf_root):
    """场景已绑定坐标：校验通过，正常执行到结束"""
    wf = _write_wf(wf_root, (
        'def helper()\n'
        '    click [game_main_page].[btn]\n'
        'end\n'
        'log "ok"\n'
    ))
    engine = _make_engine(bound_scenes={"game_main_page"})
    engine.execute(wf)  # 不抛异常（helper 未被调用，仅校验不执行）


def test_missing_key_raises_with_file_and_line(wf_root):
    """场景绑了别的区域、但脚本引用的 key 不存在（如把中文名当 key 写）"""
    wf = _write_wf(wf_root, (
        'log "start"\n'
        'click [game_main_page].[返回]\n'
    ))
    engine = _make_engine(bound_scenes={"game_main_page"})
    with pytest.raises(WorkflowUserError) as ei:
        engine.execute(wf)
    msg = str(ei.value)
    assert "返回" in msg
    assert "t.wf:2" in msg


def test_missing_key_in_imported_proc_reports_that_file(wf_root):
    """import 进来的 def 体报错须报它自己的文件名，否则定位到错文件"""
    _write_wf(wf_root, (
        'def helper()\n'
        '    click [game_main_page].[nope]\n'
        'end\n'
    ), name="lib.wf")
    wf = _write_wf(wf_root, (
        'import "lib.wf"\n'
        'log "start"\n'
        'call helper()\n'
    ))
    engine = _make_engine(bound_scenes={"game_main_page"})
    with pytest.raises(WorkflowUserError) as ei:
        engine.execute(wf)
    assert "lib.wf:2" in str(ei.value)


def test_drag_key_checked_against_arrows_and_regions(wf_root):
    """drag 查的是方向/区域，绑了 region 就算绑定"""
    wf = _write_wf(wf_root, 'drag [game_main_page].[btn]\n')
    engine = _make_engine(bound_scenes={"game_main_page"})
    # btn 作为 region 已绑定，静态检查应通过
    engine.execute(wf)


def test_drag_key_unbound_raises(wf_root):
    """drag 查的是方向/区域，都没绑才报错"""
    wf = _write_wf(wf_root, 'drag [game_main_page].[unknown]\n')
    engine = _make_engine(bound_scenes={"game_main_page"})
    with pytest.raises(WorkflowUserError, match="方向/区域未绑定"):
        engine.execute(wf)


def test_drag_with_bound_arrow_passes(wf_root):
    wf = _write_wf(wf_root, 'log "ok"\n')
    arrow = SimpleNamespace(key="menu_up", from_key="p1", to_key=None)
    engine = _make_engine(
        bound_scenes={"game_main_page"},
        arrows={"game_main_page": [arrow]},
        points={"game_main_page": [SimpleNamespace(key="p1")]},
    )
    engine.execute(wf)


def test_arrow_missing_from_point_raises(wf_root):
    """方向绑了、但起点坐标点没绑：运行时同样点不出来，提前拦住"""
    wf = _write_wf(wf_root, 'drag [game_main_page].[menu_up]\n')
    arrow = SimpleNamespace(key="menu_up", from_key="p1", to_key=None)
    engine = _make_engine(
        bound_scenes={"game_main_page"},
        arrows={"game_main_page": [arrow]},
    )
    with pytest.raises(WorkflowUserError, match="起点坐标点未绑定"):
        engine.execute(wf)


def test_panel_key_checked_against_panels(wf_root):
    """align / panel 索引查的是面板"""
    wf = _write_wf(wf_root, 'align [game_main_page].[grid]\n')
    engine = _make_engine(bound_scenes={"game_main_page"})
    with pytest.raises(WorkflowUserError, match="面板未绑定"):
        engine.execute(wf)


def test_dynamic_key_skips_key_check(wf_root):
    """key 为变量：静态只能校验到场景一级，不误报"""
    wf = _write_wf(wf_root, (
        'eval $k = "whatever"\n'
        'log "ok"\n'
        'if 1 == 2\n'
        '    click [game_main_page].$k\n'
        'end\n'
    ))
    engine = _make_engine(bound_scenes={"game_main_page"})
    engine.execute(wf)


# ─── disabled 实例静态检查 ────────────────────────────────

def test_disabled_region_key_does_not_raise(wf_root):
    """disabled 的 region 实例仍在列表中，静态检查视为已绑定"""
    wf = _write_wf(wf_root, (
        'def helper()\n'
        '    click [game_main_page].[main_func]\n'
        'end\n'
        'log "ok"\n'
    ))
    disabled_region = SimpleNamespace(
        key="main_func", x_ratio=0.0, y_ratio=0.0, w_ratio=0.5, h_ratio=0.5, disabled=True)
    engine = _make_engine(
        bound_scenes=set(),  # 没有实际绑定任何非 disabled region
        regions={"game_main_page": disabled_region},
    )
    engine.execute(wf)  # 不抛异常（helper 未被调用，仅校验不执行）


def test_disabled_point_key_does_not_raise(wf_root):
    """disabled 的 point 实例视为已绑定"""
    wf = _write_wf(wf_root, (
        'def helper()\n'
        '    click [game_main_page].[my_point]\n'
        'end\n'
        'log "ok"\n'
    ))
    disabled_point = SimpleNamespace(key="my_point", disabled=True)
    engine = _make_engine(
        bound_scenes=set(),
        points={"game_main_page": [disabled_point]},
    )
    engine.execute(wf)


def test_disabled_panel_key_does_not_raise(wf_root):
    """disabled 的 panel 实例视为已绑定"""
    wf = _write_wf(wf_root, (
        'def helper()\n'
        '    align [game_main_page].[grid]\n'
        'end\n'
        'log "ok"\n'
    ))
    disabled_panel = SimpleNamespace(key="grid", disabled=True)
    engine = _make_engine(
        bound_scenes=set(),
        panels={"game_main_page": [disabled_panel]},
    )
    engine.execute(wf)


def test_disabled_arrow_key_does_not_raise(wf_root):
    """disabled 的 arrow 实例视为已绑定，端点检查仍需通过"""
    wf = _write_wf(wf_root, (
        'def helper()\n'
        '    drag [game_main_page].[menu_up]\n'
        'end\n'
        'log "ok"\n'
    ))
    disabled_arrow = SimpleNamespace(key="menu_up", from_key="p1", to_key=None, disabled=True)
    engine = _make_engine(
        bound_scenes=set(),
        arrows={"game_main_page": [disabled_arrow]},
        points={"game_main_page": [SimpleNamespace(key="p1")]},
    )
    engine.execute(wf)


# 静态检查允许 disabled 绑定存在；只有真正执行到该实例时才报错。

@pytest.mark.parametrize(
    ("statement", "kind", "expected"),
    [
        ("click [game_main_page].[target]", "region", "区域"),
        ("click [game_main_page].[target]", "point", "坐标点"),
        ("align [game_main_page].[target]", "panel", "面板"),
        ("drag [game_main_page].[target]", "arrow", "方向"),
    ],
)
def test_runtime_access_to_disabled_layout_item_raises(
    wf_root, statement, kind, expected,
):
    """执行期选中 disabled 对象时，在读取其坐标前中断。"""
    wf = _write_wf(wf_root, statement + "\n")
    kwargs = {
        "regions": None,
        "points": None,
        "panels": None,
        "arrows": None,
    }
    item = SimpleNamespace(
        key="target", disabled=True,
        x_ratio=0.0, y_ratio=0.0, w_ratio=0.5, h_ratio=0.5,
        cx_ratio=0.0, cy_ratio=0.0, r_ratio=0.01,
        from_key="start", to_key=None,
    )
    if kind == "region":
        kwargs["regions"] = {"game_main_page": item}
    else:
        kwargs[f"{kind}s"] = {"game_main_page": [item]}
    if kind == "arrow":
        kwargs["points"] = {
            "game_main_page": [SimpleNamespace(key="start", disabled=False)]
        }

    engine = _make_engine(bound_scenes=set(), **kwargs)
    with pytest.raises(
        WorkflowUserError,
        match=rf"{expected}.*\[game_main_page\]\.\[target\].*已禁用",
    ):
        engine.execute(wf)


def test_whole_scene_scan_ignores_disabled_region(wf_root):
    """未列字段是便捷全扫，disabled region 应视为当前布局不存在。"""
    wf = _write_wf(wf_root, "scan [game_main_page] as $result\n")
    disabled_region = SimpleNamespace(
        key="hidden", disabled=True,
        x_ratio=0.0, y_ratio=0.0, w_ratio=0.5, h_ratio=0.5,
    )
    engine = _make_engine(
        bound_scenes=set(),
        regions={"game_main_page": disabled_region},
    )

    engine.execute(wf)
    assert engine.variables["result"] == {}
    assert engine._coord_meta["result"] == {}


def test_non_disabled_key_still_raises(wf_root):
    """未绑定的 key 仍然报错（disabled 的是别的 key）"""
    wf = _write_wf(wf_root, (
        'def helper()\n'
        '    click [game_main_page].[unknown_key]\n'
        'end\n'
        'log "ok"\n'
        'call helper()\n'
    ))
    disabled_region = SimpleNamespace(
        key="main_func", x_ratio=0.0, y_ratio=0.0, w_ratio=0.5, h_ratio=0.5, disabled=True)
    engine = _make_engine(
        bound_scenes=set(),
        regions={"game_main_page": disabled_region},  # disabled 的是别的 key
    )
    with pytest.raises(WorkflowUserError):
        engine.execute(wf)


def test_execute_skips_unreachable_but_validate_only_checks_it(wf_root):
    """执行前闸门与 CI 门禁的范围分工。

    execute() 只查可达过程——拿不会执行的代码把用户挡在门外是事故；
    validate_only()（CI 门禁与上机前预检）连没人调用的库函数一起查，
    否则 page_detection.wf 这类函数库里的 key 拼错永远没人发现。

    预检比执行更严，方向是安全的：只会「报了但其实不会炸」。
    """
    _write_wf(wf_root, (
        'def never_called()\n'
        '    click [game_main_page].[typo_key]\n'
        'end\n'
    ), name="lib.wf")
    wf = _write_wf(wf_root, (
        'import "lib.wf"\n'
        'log "start"\n'
    ))
    engine = _make_engine(bound_scenes={"game_main_page"})

    engine.execute(wf)  # 不可达，不该阻断

    with pytest.raises(WorkflowUserError) as ei:
        engine.validate_only(wf)
    assert "typo_key" in str(ei.value)
    assert "lib.wf:2" in str(ei.value)
