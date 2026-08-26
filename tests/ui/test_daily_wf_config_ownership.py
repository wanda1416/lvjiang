"""日常页工作流配置所有权边界测试"""

import json
from types import SimpleNamespace

import pytest

from lvjiang.core.config.wf_configs import get_wf_config, set_wf_config
from lvjiang.ui.main.window import MainWindow


class _Combo:
    def currentIndex(self):
        return 0


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    import lvjiang.constants as constants_mod
    import lvjiang.core.config.session as store_mod
    path = tmp_path / "session.json"
    monkeypatch.setattr(constants_mod, "SESSION_PATH", path)
    store_mod.reset_session_store()
    return path


def test_daily_save_does_not_overwrite_dedicated_auto_tuning_config(store_path):
    """日常页只保存 daily.workflow_id，不回写无参数脚本的旧 wf_configs 快照"""
    set_wf_config("auto_tuning", {
        "selected_slots": ["ring"],
        "rules": {"old": {"enabled": True}},
    })

    host = SimpleNamespace(
        workflow_combo=_Combo(),
        _workflow_configs=[
            {
                "id": "auto_tuning",
                "name": "自动调律",
                "parameters": [],
                "_saved_params": {
                    "selected_slots": ["head"],
                    "rules": {"stale": {"enabled": True}},
                },
            }
        ],
    )
    host._get_selected_flow_config = lambda: host._workflow_configs[0]

    MainWindow._save_daily_config(host)

    assert get_wf_config("auto_tuning") == {
        "selected_slots": ["ring"],
        "rules": {"old": {"enabled": True}},
    }
    saved = json.loads(store_path.read_text(encoding="utf-8"))
    assert saved["daily"]["workflow_id"] == "auto_tuning"
