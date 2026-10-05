"""单搭配存储、写入权限与编辑上下文隔离。"""
import copy

import pytest
from PyQt6.QtGui import QStandardItemModel

from lvjiang.apps.yysls.config.builds import (
    GEAR_SETS_DIR,
    BuildDefinition,
    BuildRepository,
)
from lvjiang.apps.yysls.ui.loadout.build_calculator import BuildEditor, BuildListPanel
from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected
from tests.yysls.test_build_calculator import allocate, counts


def repositories(tmp_path):
    roots = dict(system_dir=tmp_path / "system", local_dir=tmp_path / "local", remote_dir=tmp_path / "remote")
    return (BuildRepository(ConfigResolver(**roots, dev_mode=True)),
            BuildRepository(ConfigResolver(**roots, dev_mode=False)))


def build(name, storage="local"):
    value = BuildDefinition.create(name, "无名", 115)
    value.storage = storage
    value.equipment = allocate(counts()).equipment
    return value


def test_single_files_sort_system_first_and_user_cannot_write_system(tmp_path):
    developer, user = repositories(tmp_path)
    preset, local = build("系统预置", "system"), build("本地搭配")
    developer.save(preset)
    user.save(local)
    assert [item.storage for item in user.all()] == ["system", "local"]
    assert (developer.resolver.system_dir / GEAR_SETS_DIR / f"{preset.id}.yaml").is_file()
    assert (developer.resolver.local_dir / GEAR_SETS_DIR / f"{local.id}.yaml").is_file()
    with pytest.raises(SystemContentProtected):
        user.save(preset, expected=preset.to_dict())
    with pytest.raises(SystemContentProtected):
        user.delete(preset)
    with pytest.raises(SystemContentProtected):
        user.save(build("非法写入", "system"))
    # 开发模式编辑本地条目仍写本地，而不是按运行模式写进 system。
    expected = local.to_dict()
    local.name = "本地改名"
    developer.save(local, expected=expected)
    assert not (developer.resolver.system_dir / GEAR_SETS_DIR / f"{local.id}.yaml").exists()


def test_constraints_follow_build_and_system_save_as_keeps_original(tmp_path, qtbot):
    developer, user = repositories(tmp_path)
    preset, local = build("有约束", "system"), build("无约束")
    preset.requirements = [{"affix": "势", "priority": "required", "minimum": 8, "maximum": 8}]
    developer.save(preset)
    user.save(local)
    editor = BuildEditor("无名", repository=user, initial=user.all()[0])
    qtbot.addWidget(editor)
    assert editor._requirement_rows() == preset.requirements
    assert not editor.save_button.isEnabled()
    model = editor.save_location.model()
    assert isinstance(model, QStandardItemModel)
    assert not model.item(editor.save_location.findData("system")).isEnabled()
    before = copy.deepcopy(user.all()[0].to_dict())
    editor.save_location.setCurrentIndex(editor.save_location.findData("local"))
    editor.save()
    assert editor._build.id != preset.id
    assert editor._build.storage == "local"
    assert user.all()[0].to_dict() == before
    editor.load_build(local)
    assert editor._requirement_rows() == []
    editor._load_current()
    assert editor._requirement_rows() == []
    panel = BuildListPanel(repository=user)
    qtbot.addWidget(panel)
    panel.set_playstyle("无名")
    assert panel.table.columnCount() == 5
    assert panel.table.item(0, 4).text() == "系统预置"


def test_developer_can_explicitly_save_system_without_changing_source(tmp_path, qtbot):
    developer, _user = repositories(tmp_path)
    local = build("用户源")
    developer.save(local)
    editor = BuildEditor("无名", repository=developer, initial=local)
    qtbot.addWidget(editor)
    editor.save_location.setCurrentIndex(editor.save_location.findData("system"))
    editor.save()
    saved = developer.all()
    assert [item.storage for item in saved] == ["system", "local"]
    assert saved[1].id == local.id and saved[1].to_dict() == local.to_dict()
    assert saved[0].id != local.id
