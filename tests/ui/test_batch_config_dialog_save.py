from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.ui.batch.batch_config_dialog import BatchConfigDialog


class _Users:
    @staticmethod
    def list_users() -> list[str]:
        return []


def test_save_preserves_script_ids_changed_while_dialog_is_open(monkeypatch, qtbot):
    stale = BatchConfig(
        configs={"旧配置": BatchConfigItem(name="旧配置")},
        active_config="旧配置",
        script_ids=["old-script"],
    )
    latest = BatchConfig(script_ids=["new-script", "second-script"])
    saved = []

    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config",
        lambda: stale,
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.UserConfigManager",
        lambda: _Users(),
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.save_batch_config",
        saved.append,
    )

    dialog = BatchConfigDialog()
    qtbot.addWidget(dialog)
    dialog._cfg.configs["新配置"] = BatchConfigItem(name="新配置")
    dialog._cfg.active_config = "新配置"
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config",
        lambda: latest,
    )

    dialog._on_save()

    assert saved[0].script_ids == ["new-script", "second-script"]
    assert list(saved[0].configs) == ["旧配置", "新配置"]
    assert saved[0].active_config == "新配置"
