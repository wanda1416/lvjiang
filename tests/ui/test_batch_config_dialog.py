"""批量配置对话框的单条目生命周期选项。"""

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.ui.batch.batch_config_dialog import BatchConfigDialog


def test_single_item_lifecycle_checkbox_loads_and_saves(qtbot, monkeypatch):
    item = BatchConfigItem(
        name="测试配置",
        skip_lifecycle_for_single_item=False,
    )
    config = BatchConfig(
        configs={item.name: item},
        active_config=item.name,
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config",
        lambda: config,
    )

    dialog = BatchConfigDialog()
    qtbot.addWidget(dialog)

    assert dialog._skip_single_lifecycle.isChecked() is False
    dialog._skip_single_lifecycle.setChecked(True)
    dialog._save_current_config()
    assert item.skip_lifecycle_for_single_item is True
