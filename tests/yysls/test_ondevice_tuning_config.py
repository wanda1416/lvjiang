"""设备端调律参数配置（core.ondevice.tuning_config）测试

覆盖 save_tuning_config 的三条校验（与桌面 _start_tuning 一致）与
「保存 → 回读」闭环。插件 session 单例替换为 tmp_path 隔离实例；
ensure_loaded 打桩为空操作——规则/开关注册表本就不依赖插件加载。
"""

import json

import pytest

import lvjiang.apps.yysls.session as ps_module
import lvjiang.core.ondevice.plugins as plugins_module
from lvjiang.apps.yysls.session import PluginSession
from lvjiang.core.ondevice.tuning_config import (
    get_tuning_config,
    save_tuning_config,
)


@pytest.fixture
def session_path(tmp_path, monkeypatch):
    """插件 session 单例 → tmp_path 隔离实例；ensure_loaded → 空操作"""
    import lvjiang.apps.yysls.tune_config as tc_module
    path = tmp_path / "session.json"
    monkeypatch.setattr(ps_module, "_session", PluginSession(path))
    monkeypatch.setattr(plugins_module, "ensure_loaded", lambda: None)
    monkeypatch.setattr(tc_module, "_instance", None)
    return path


def _save(payload: dict) -> dict:
    return json.loads(save_tuning_config(json.dumps(payload, ensure_ascii=False)))


class TestSaveValidation:
    def test_empty_slots_rejected(self, session_path):
        result = _save({
            "selected_slots": [],
            "rules": {"huiyi_general": {"enabled": True}},
        })
        assert result["ok"] is False
        assert "部位" in result["message"]
        assert not session_path.exists()  # 校验失败不落盘

    def test_locked_slot_only_rejected(self, session_path):
        # 副武器是禁用部位，只勾它等于没勾
        result = _save({
            "selected_slots": ["sub_weapon"],
            "rules": {"huiyi_general": {"enabled": True}},
        })
        assert result["ok"] is False
        assert "部位" in result["message"]

    def test_no_enabled_rule_rejected(self, session_path):
        result = _save({"selected_slots": ["ring"], "rules": {}})
        assert result["ok"] is False
        assert "规则" in result["message"]

    def test_enabled_rule_without_playstyle_rejected(self, session_path):
        # heal_fire 声明了玩法，启用后玩法清空应被拦下
        result = _save({
            "selected_slots": ["ring"],
            "rules": {"heal_fire": {"enabled": True, "playstyles": []}},
        })
        assert result["ok"] is False
        assert "玩法" in result["message"]


class TestSaveRoundtrip:
    def test_save_then_get_reflects_state(self, session_path):
        result = _save({
            "selected_slots": ["ring", "head", "sub_weapon"],  # 禁用项应被丢弃
            "rules": {"huiyi_general": {"enabled": True}},
            "switches": {"keep_pvp": True},
        })
        assert result["ok"] is True

        saved = json.loads(session_path.read_text(encoding="utf-8"))["yysls"]["tuning"]
        assert saved["selected_slots"] == ["ring", "head"]
        assert saved["rules"]["huiyi_general"]["enabled"] is True
        assert saved["switches"] == {"keep_pvp": True}
        assert saved["skip_tuning"] is False

        view = json.loads(get_tuning_config())
        assert view["ok"] is True
        rules = {r["key"]: r for r in view["rules"]}
        assert rules["huiyi_general"]["enabled"] is True
        slots = {
            s["key"]: s
            for g in view["slot_groups"] for s in g["slots"]
        }
        assert slots["ring"]["checked"] and slots["head"]["checked"]
        assert not slots["main_weapon"]["checked"]
        assert slots["sub_weapon"]["locked"] and not slots["sub_weapon"]["checked"]
        switches = {s["key"]: s for s in view["switches"]}
        assert switches["keep_pvp"]["checked"] is True

    def test_skip_tuning_preserved(self, session_path):
        # skip_tuning 不进设备端 UI，保存不得覆盖已有值
        session_path.write_text(json.dumps({
            "yysls": {"tuning": {"skip_tuning": True}},
        }), encoding="utf-8")
        ps_module._session = PluginSession(session_path)  # 重新加载文件内容

        result = _save({
            "selected_slots": ["ring"],
            "rules": {"huiyi_general": {"enabled": True}},
        })
        assert result["ok"] is True
        saved = json.loads(session_path.read_text(encoding="utf-8"))["yysls"]["tuning"]
        assert saved["skip_tuning"] is True
