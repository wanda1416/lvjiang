"""设备端调律参数配置（apps.yysls.ondevice.tuning_config）测试

覆盖 save_tuning_config 的三条校验（与桌面 _start_tuning 一致）与
「保存 → 回读」闭环。插件 session 单例替换为 tmp_path 隔离实例；
ensure_loaded 打桩为空操作——规则/开关注册表本就不依赖插件加载。
"""

import json

import pytest

import lvjiang.core.ondevice.plugins as plugins_module
from lvjiang.apps.yysls.ondevice.tuning_config import (
    get_tuning_config,
    save_tuning_config,
)


@pytest.fixture
def session_path(tmp_path, monkeypatch):
    """core SessionStore → tmp_path 隔离实例；ensure_loaded → 空操作"""
    import lvjiang.constants as constants_mod
    import lvjiang.core.config.session as store_mod
    path = tmp_path / "session.json"
    monkeypatch.setattr(constants_mod, "SESSION_PATH", path)
    store_mod.reset_session_store()
    monkeypatch.setattr(plugins_module, "ensure_loaded", lambda *_args: None)
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
            "switches": {"keep_danti": True, "keep_wanjia": True},
        })
        assert result["ok"] is True

        saved = json.loads(session_path.read_text(encoding="utf-8"))["wf_configs"]["auto_tuning"]
        assert saved["selected_slots"] == ["ring", "head"]
        assert saved["rules"]["huiyi_general"]["enabled"] is True
        assert saved["switches"] == {"keep_danti": True, "keep_wanjia": True}
        assert "skip_tuning" not in saved

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
        assert switches["keep_danti"]["checked"] is True
        assert switches["keep_wanjia"]["checked"] is True

    def test_desktop_only_parameters_preserved(self, session_path):
        # 桌面调试/后台参数不进设备端 UI，保存不得覆盖已有值
        session_path.write_text(json.dumps({
            "wf_configs": {"auto_tuning": {
                "skip_tuning": True,
                "pc_background_scroll": True,
                "scroll_strategy": "positional",
                "use_stone_cache": True,
                "initial_stone_check_enabled": True,
                "initial_stone_min_count": 120,
                "validate_stone_cache": True,
            }},
        }), encoding="utf-8")
        import lvjiang.core.config.session as store_mod
        store_mod.reset_session_store()  # 重新加载文件内容

        result = _save({
            "selected_slots": ["ring"],
            "rules": {"huiyi_general": {"enabled": True}},
        })
        assert result["ok"] is True
        saved = json.loads(session_path.read_text(encoding="utf-8"))["wf_configs"]["auto_tuning"]
        assert saved["skip_tuning"] is True
        assert saved["pc_background_scroll"] is True
        assert saved["scroll_strategy"] == "positional"
        assert saved["use_stone_cache"] is True
        assert saved["initial_stone_check_enabled"] is True
        assert saved["initial_stone_min_count"] == 120
        assert saved["validate_stone_cache"] is True
