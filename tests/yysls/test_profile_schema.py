"""玩家数据模型配置加载测试

覆盖 user_profile.py 的 ProfileSchema 加载、查询、序列化。
"""

import pytest
import yaml

from lvjiang.apps.yysls.config.profile_models import (
    DailyKeyDef,
    RealtimeKeyDef,
    ResourceKeyDef,
)
from lvjiang.apps.yysls.config.user_profile import (
    ProfileSchema,
    _load_config,
    get_profile_config,
    reload_profile_config,
    save_profile_config,
)


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    """隔离的 profile.yaml 环境"""
    from lvjiang import constants
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", session_dir)

    # 重置单例
    import lvjiang.apps.yysls.config.user_profile as mod
    mod._config = None
    mod._PROFILE_PATH = session_dir / "profile.yaml"

    yield session_dir

    mod._config = None


def _write_profile_yaml(session_dir, data):
    """写入 profile.yaml"""
    path = session_dir / "profile.yaml"
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


# ─── ProfileSchema ───────────────────────────────────────────


class TestProfileSchema:
    def test_empty_schema(self):
        schema = ProfileSchema()
        assert schema.get_all_keys() == []
        assert schema.get_key("nonexistent") is None
        assert schema.get_model_type("nonexistent") is None

    def test_get_keys_by_model(self):
        daily_keys = [
            DailyKeyDef(key="k1", label="日常1"),
            DailyKeyDef(key="k2", label="日常2"),
        ]
        realtime_keys = [
            RealtimeKeyDef(key="k3", label="实时1"),
        ]
        schema = ProfileSchema(keys_by_model={
            "daily": daily_keys,
            "realtime": realtime_keys,
        })

        assert len(schema.get_keys_by_model("daily")) == 2
        assert len(schema.get_keys_by_model("realtime")) == 1
        assert len(schema.get_keys_by_model("resource")) == 0

    def test_get_key(self):
        kd = DailyKeyDef(key="test", label="测试")
        schema = ProfileSchema(keys_by_model={"daily": [kd]})
        found = schema.get_key("test")
        assert found is kd
        assert schema.get_key("nonexistent") is None

    def test_get_model_type(self):
        kd = RealtimeKeyDef(key="tili", label="体力")
        schema = ProfileSchema(keys_by_model={"realtime": [kd]})
        assert schema.get_model_type("tili") == "realtime"
        assert schema.get_model_type("unknown") is None

    def test_get_all_keys_order(self):
        d1 = DailyKeyDef(key="d1", label="D1")
        d2 = DailyKeyDef(key="d2", label="D2")
        r1 = RealtimeKeyDef(key="r1", label="R1")
        schema = ProfileSchema(keys_by_model={
            "daily": [d1, d2],
            "realtime": [r1],
        })
        all_keys = schema.get_all_keys()
        assert len(all_keys) == 3
        assert [k.key for k in all_keys] == ["d1", "d2", "r1"]

    def test_to_dict(self):
        d1 = DailyKeyDef(key="k1", label="l1", period="week")
        r1 = RealtimeKeyDef(key="k2", label="l2", cap=100)
        schema = ProfileSchema(keys_by_model={
            "daily": [d1],
            "realtime": [r1],
        })
        result = schema.to_dict()
        assert "daily" in result
        assert "realtime" in result
        assert len(result["daily"]) == 1
        assert result["daily"][0]["key"] == "k1"


# ─── 加载 ────────────────────────────────────────────────────


class TestLoadConfig:
    def test_load_new_format(self, profile_env):
        _write_profile_yaml(profile_env, {
            "daily": [
                {"key": "niaoniao", "label": "袅袅", "period": "week"},
            ],
            "realtime": [
                {"key": "tili", "label": "体力", "cap": 2500, "regen_period": "day", "regen_value": 450},
            ],
        })
        schema = _load_config()
        assert len(schema.get_all_keys()) == 2
        assert isinstance(schema.get_key("niaoniao"), DailyKeyDef)
        assert isinstance(schema.get_key("tili"), RealtimeKeyDef)

    def test_load_old_format_raises(self, profile_env):
        """旧格式（fields/groups）抛出异常"""
        _write_profile_yaml(profile_env, {
            "fields": [{"name": "old_field"}],
            "groups": [{"name": "old_group"}],
        })
        with pytest.raises(ValueError, match="旧格式"):
            _load_config()

    def test_load_missing_file(self, profile_env):
        """文件不存在返回空配置"""
        schema = _load_config()
        assert schema.get_all_keys() == []

    def test_load_invalid_yaml(self, profile_env):
        """无效 YAML 抛出异常"""
        path = profile_env / "profile.yaml"
        path.write_text("{invalid yaml: [", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            _load_config()

    def test_load_raises_on_invalid_entry(self, profile_env):
        """非 dict 条目抛出异常"""
        _write_profile_yaml(profile_env, {
            "daily": [
                {"key": "valid", "label": "有效"},
                "not_a_dict",
            ],
        })
        with pytest.raises(ValueError, match="非 dict"):
            _load_config()

    def test_load_skips_empty_key(self, profile_env):
        """key 为空的条目被过滤（不报错）"""
        _write_profile_yaml(profile_env, {
            "daily": [
                {"key": "valid", "label": "有效"},
                {"key": "", "label": "空key"},
            ],
        })
        schema = _load_config()
        assert len(schema.get_all_keys()) == 1
        assert schema.get_key("valid") is not None


# ─── 保存 ────────────────────────────────────────────────────


class TestSaveConfig:
    def test_save_and_reload(self, profile_env):
        kd = DailyKeyDef(key="test", label="测试", period="week")
        schema = ProfileSchema(keys_by_model={"daily": [kd]})
        save_profile_config(schema)

        # 验证文件存在
        path = profile_env / "profile.yaml"
        assert path.exists()

        # 重新加载
        reloaded = _load_config()
        assert reloaded.get_key("test") is not None
        assert isinstance(reloaded.get_key("test"), DailyKeyDef)

    def test_roundtrip_all_models(self, profile_env):
        schema = ProfileSchema(keys_by_model={
            "daily": [DailyKeyDef(key="d1", label="D1", period="week")],
            "realtime": [RealtimeKeyDef(key="r1", label="R1", cap=100)],
            "resource": [ResourceKeyDef(key="res1", label="Res1")],
        })
        save_profile_config(schema)
        reloaded = _load_config()

        assert len(reloaded.get_all_keys()) == 3
        assert isinstance(reloaded.get_key("d1"), DailyKeyDef)
        assert isinstance(reloaded.get_key("r1"), RealtimeKeyDef)
        assert isinstance(reloaded.get_key("res1"), ResourceKeyDef)


# ─── 单例 ────────────────────────────────────────────────────


class TestSingleton:
    def test_get_profile_config_singleton(self, profile_env):
        _write_profile_yaml(profile_env, {
            "daily": [{"key": "k", "label": "l"}],
        })
        c1 = get_profile_config()
        c2 = get_profile_config()
        assert c1 is c2

    def test_reload_profile_config(self, profile_env):
        _write_profile_yaml(profile_env, {
            "daily": [{"key": "k1", "label": "l1"}],
        })
        c1 = get_profile_config()
        assert c1.get_key("k1") is not None

        # 修改文件后 reload
        _write_profile_yaml(profile_env, {
            "daily": [{"key": "k2", "label": "l2"}],
        })
        c2 = reload_profile_config()
        assert c2.get_key("k1") is None
        assert c2.get_key("k2") is not None
        assert c2 is not c1
