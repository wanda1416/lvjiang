"""位置声明有源码位置和静态诊断，执行时完全无操作。"""
from lvjiang.core.scene_definition_models import SceneDef, ViewDef
from lvjiang.workflows.engine.core import WorkflowEngine
from lvjiang.workflows.grammar import SceneDeclaration, parse_text
from lvjiang.workflows.scene_declarations import (
    collect_scene_declarations,
    validate_scene_declarations,
)
from lvjiang.workflows.workflow_references import collect_refs


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
