"""位置声明有源码位置和静态诊断，执行时完全无操作。"""
from lvjiang.core.scene.definition_models import SceneDef, ViewDef
from lvjiang.workflows.analysis.scene_declarations import (
    collect_scene_declarations,
    validate_scene_declarations,
)
from lvjiang.workflows.analysis.workflow_references import collect_refs
from lvjiang.workflows.engine.core import WorkflowEngine
from lvjiang.workflows.grammar import SceneDeclaration, parse_text


def test_scene_unknown_unspecified_and_explicit_view():
    program = parse_text('scene [settings]\nscene [settings] view [other]\nscene unknown\n')
    assert program.body == [SceneDeclaration('settings', None, 1),
                            SceneDeclaration('settings', 'other', 2),
                            SceneDeclaration(None, None, 3)]
    assert not collect_refs(program.body, program.procs)
    # No initialized engine, input backend, variables, pause or debugger needed.
    for node in program.body:
        WorkflowEngine._exec_stmt(object(), node)


def test_declarations_in_branches_and_unused_procedures_are_checked():
    program = parse_text('''def unused()
    scene [missing]
end
if $ready
    scene [settings] view [missing]
else
    scene unknown
end
scene [settings]
''')
    scenes = {'settings': SceneDef('settings', '设置', views=[ViewDef('base', '基底')])}
    assert len(collect_scene_declarations(program)) == 4
    errors = validate_scene_declarations(program, scenes)
    assert {line for line, _ in errors} == {2, 5}


def test_subscene_cannot_be_declared_as_a_page():
    """子场景是父画布里的局部，不是可跳转的页面。

    声明它只会得到一个永远匹配不上的流程位置，白白给人「这段流程有锚点」的错觉。
    """
    program = parse_text('scene [jianghu_card]\n')
    scenes = {
        'jianghu_card': SceneDef('jianghu_card', '江湖卡片', type='subscene'),
        'activity_jianghu': SceneDef('activity_jianghu', '江湖活动'),
    }

    errors = validate_scene_declarations(program, scenes)

    assert [line for line, _ in errors] == [1]
    assert '子场景' in errors[0][1]
