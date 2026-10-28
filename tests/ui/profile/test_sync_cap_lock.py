"""满值锁定：挂在「同步目标」的每条边上，控制目标满时是否锁住源 key。

同步目标是多源的，同一个目标满了不代表所有指向它的源都该停用。`shijian_of_week
→ regen:xinli` 这类边在心力满 600 时只应停止写入，不该把试剑配额显示成 —。
本文件锁定两条契约：

1. 只有「勾了满值锁定 + 目标已达硬上限」的那条边才参与锁定，取消勾选即豁免；
2. 该开关在同步目标表格里逐行可改并能落盘，回读时与其它列互不影响。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QPushButton, QWidget

from lvjiang.core.profile.models import QuotaKeyDef, RegenKeyDef, SyncTargetDef
from lvjiang.core.profile.schema import (
    ProfileSchema,
    get_profile_config,
    reload_profile_config,
    save_profile_config,
)
from lvjiang.ui.profile.cell_formatting import locked_sync_targets
from lvjiang.ui.profile.settings_dialog import (
    ProfileDefinitionDialog,
    _SyncTargetsTable,
    _SyncTargetsWidget,
)


def _schema(xinli_lock: bool = True) -> ProfileSchema:
    return ProfileSchema(keys_by_model={
        "quota": [
            QuotaKeyDef(key="mid", label="心力消耗", cap=500),
            QuotaKeyDef(key="huaruizhi", label="花蕊织", cap=4000),
            # 试剑：指向心力，心力满 600 就是它最容易撞上的现场。
            QuotaKeyDef(
                key="shijian", label="试剑", cap=2,
                sync_targets=[SyncTargetDef(key="regen:xinli", ratio=-180.0)],
            ),
        ],
        "regen": [
            RegenKeyDef(
                key="xinli", label="心力", cap=600,
                sync_targets=[SyncTargetDef(
                    key="quota:mid", ratio=-1.0, lock_on_cap=xinli_lock)],
            ),
        ],
    })


def _data(xinli: float, mid: float) -> dict:
    return {
        "regen": {"xinli": {"value": xinli}},
        "quota": {"mid": {"value": mid}},
    }


def test_capped_edge_locks_only_when_its_switch_is_on():
    save_profile_config(_schema(xinli_lock=True))
    reload_profile_config()
    xinli = get_profile_config().get_key("xinli")

    # 心力消耗满 500：默认锁住心力，避免继续记录无法传导的消耗。
    assert locked_sync_targets(xinli, _data(xinli=100, mid=500))
    assert not locked_sync_targets(xinli, _data(xinli=100, mid=499))

    # 同一条边取消勾选：目标满只停止写入，心力照常展示和可编辑。
    save_profile_config(_schema(xinli_lock=False))
    reload_profile_config()
    xinli = get_profile_config().get_key("xinli")
    assert locked_sync_targets(xinli, _data(xinli=100, mid=500)) == []
    assert locked_sync_targets(xinli, _data(xinli=100, mid=0)) == []


def test_regen_target_cap_no_longer_hides_weekly_quota():
    """regen 的 cap 是恢复上限：心力满 600 曾把试剑配额锁成 —。"""
    save_profile_config(_schema())
    reload_profile_config()
    shijian = get_profile_config().get_key("shijian")

    assert locked_sync_targets(shijian, _data(xinli=600, mid=0))
    shijian.sync_targets[0].lock_on_cap = False
    assert locked_sync_targets(shijian, _data(xinli=600, mid=0)) == []
    assert locked_sync_targets(shijian, _data(xinli=599, mid=0)) == []


def test_one_unchecked_edge_does_not_hide_another_capped_edge():
    """逐条边判断：豁免一条不能顺手把同一 key 的另一条满额边也放过去。"""
    save_profile_config(ProfileSchema(keys_by_model={
        "quota": [
            QuotaKeyDef(key="open", label="不锁", cap=1),
            QuotaKeyDef(key="closed", label="锁", cap=1),
            QuotaKeyDef(
                key="source", label="源", cap=9,
                sync_targets=[
                    SyncTargetDef(key="quota:open", lock_on_cap=False),
                    SyncTargetDef(key="quota:closed"),
                ],
            ),
        ],
    }))
    reload_profile_config()
    source = get_profile_config().get_key("source")
    data = {"quota": {"open": {"value": 1}, "closed": {"value": 1}}}
    locked = locked_sync_targets(source, data)
    assert [target.key for target in locked] == ["quota:closed"]


def test_lock_switch_roundtrips_through_sync_target_row(qapp, qtbot):
    save_profile_config(_schema(xinli_lock=False))
    reload_profile_config()
    definition = get_profile_config().get_key("xinli")

    widget = _SyncTargetsWidget()
    qtbot.addWidget(widget)
    for target in definition.sync_targets:
        widget.add_row(target)

    assert widget._table.columnCount() == 6
    lock_cell = widget._table.cellWidget(0, 3)
    checkbox = lock_cell.findChild(QCheckBox)
    assert checkbox is not None and not checkbox.isChecked()
    collected = widget.get_sync_targets()
    assert collected[0].key == "quota:mid"
    assert collected[0].lock_on_cap is False

    checkbox.setChecked(True)
    assert widget.get_sync_targets()[0].lock_on_cap is True


def test_delete_column_sticks_to_right_edge(qapp, qtbot):
    """删除列必须贴住右边缘：弹性列按比例吃掉剩余宽度，不留右侧空白。"""
    widget = _SyncTargetsWidget()
    qtbot.addWidget(widget)
    widget.resize(900, 240)
    widget.show()
    qapp.processEvents()

    table = widget._table
    widths = [table.columnWidth(column) for column in range(table.columnCount())]
    assert sum(widths) == table.viewport().width()
    # 目标列比来源列宽：key 选择按钮的文字远比来源输入长。
    assert widths[0] > widths[4]

    # 视口变窄时两列各自保底，不把选择按钮压到看不清。
    widget.resize(360, 240)
    qapp.processEvents()
    assert table.columnWidth(0) >= _SyncTargetsTable._TARGET_MIN
    assert table.columnWidth(4) >= _SyncTargetsTable._SOURCE_MIN
    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_lock_switch_roundtrips_through_key_editor(qapp, monkeypatch):
    definition = QuotaKeyDef(
        key="mid", label="心力消耗", cap=500,
        sync_targets=[SyncTargetDef(key="regen:xinli", ratio=-1.0, lock_on_cap=False)])
    save_profile_config(ProfileSchema(keys_by_model={
        "quota": [definition],
        "regen": [RegenKeyDef(key="xinli", label="心力", cap=600)],
    }))
    reload_profile_config()

    def edit(dialog):
        table = dialog.findChildren(QWidget)
        checkbox = next(
            box for box in dialog.findChildren(QCheckBox)
            if box.toolTip().startswith("勾选：该目标达到上限后锁定本项"))
        assert not checkbox.isChecked()
        assert checkbox.isVisibleTo(dialog)
        checkbox.setChecked(True)
        next(button for button in dialog.findChildren(QPushButton)
             if button.text() == "保存").click()
        assert table  # 保持对话框存活到保存完成
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", edit)
    parent = QWidget()
    ProfileDefinitionDialog.open_key_editor(
        parent, "quota", definition, {"mid"})
    saved = get_profile_config().get_key("mid")
    assert saved.sync_targets[0].lock_on_cap is True
    parent.close()
