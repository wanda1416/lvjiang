"""用户配置加载链与保存函数测试

覆盖链路：
- 代码默认值 ← session.json（settings 及其 reference_grid 子节点）
- 代码默认值 ← app.yaml（input_simulation/delay_params，经 core.config 合并）

保存函数：
- save_settings / save_reference_grid（读改写 session.json 的 settings 节点，各自保留对方字段）
- save_app_config（经 core.config 写入 app.yaml）
"""

import json

import pytest

from lvjiang.core.config import (
    load_user_config,
    save_app_config,
    save_reference_grid,
    save_settings,
)
from lvjiang.core.config.session import reset_session_store


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """隔离的 session 环境：monkeypatch SESSION_PATH 并重置单例"""
    from lvjiang import constants
    session_path = tmp_path / "session.json"
    monkeypatch.setattr(constants, "SESSION_PATH", session_path)
    reset_session_store()
    yield session_path
    reset_session_store()


class TestLoadUserConfig:
    def test_defaults_without_files(self, session_env, monkeypatch):
        """无任何配置文件时使用代码默认值"""
        monkeypatch.setattr(
            "lvjiang.core.config.load_app_config", lambda: {})
        config = load_user_config()
        assert config.theme == "light"
        assert config.android_capture_method == "scrcpy"
        assert config.android_input_method == "adb"
        assert config.desktop_background_input is True
        assert config.desktop_window_title == ""
        assert config.reference_grid.rows == 3
        assert config.reference_grid.cols == 6
        assert config.reference_grid.height == 122
        assert config.reference_grid.width == 122
        assert config.input_sim.click_random_offset == 3
        assert config.delay_params == {}

    def test_hotkeys_only_allow_f7_through_f12(self, session_env, monkeypatch):
        """F1~F6 与现有菜单功能键隔离，非法值按动作回退默认。"""
        monkeypatch.setattr(
            "lvjiang.core.config.load_app_config", lambda: {})
        session_env.write_text(json.dumps({
            "settings": {"hotkeys": {
                "start": "F1", "pause": "F6", "stop": "F11", "record": "F7",
            }}
        }), encoding="utf-8")
        reset_session_store()

        config = load_user_config()

        assert config.hotkeys.start == "F9"
        assert config.hotkeys.pause == "F8"
        assert config.hotkeys.stop == "F11"
        assert config.hotkeys.record == "F7"

    def test_duplicate_hotkeys_fall_back_as_a_complete_set(
            self, session_env, monkeypatch):
        """手工配置的重复键不能在监听字典中覆盖其他动作。"""
        monkeypatch.setattr(
            "lvjiang.core.config.load_app_config", lambda: {})
        session_env.write_text(json.dumps({
            "settings": {"hotkeys": {
                "start": "F7", "pause": "F7", "stop": "F11", "record": "F12",
            }}
        }), encoding="utf-8")
        reset_session_store()

        config = load_user_config()

        assert config.hotkeys.start == "F9"
        assert config.hotkeys.pause == "F8"
        assert config.hotkeys.stop == "F10"
        assert config.hotkeys.record == "F12"

    def test_session_settings_override(self, session_env, monkeypatch):
        """session.json 的 settings 节点覆盖基础配置"""
        monkeypatch.setattr(
            "lvjiang.core.config.load_app_config", lambda: {})
        session_env.write_text(json.dumps({
            "settings": {
                "android_capture_method": "screencap",
                "android_input_method": "device_gesture",
                "desktop_window_title": "手机投屏",
                "theme": "dark",
            }
        }), encoding="utf-8")
        reset_session_store()
        config = load_user_config()
        assert config.android_capture_method == "screencap"
        assert config.android_input_method == "device_gesture"
        assert config.desktop_window_title == "手机投屏"
        assert config.theme == "dark"
        assert config.desktop_background_input is True  # 未配置项保持默认

    def test_legacy_material_grid_override_migrates_on_read(self, session_env, monkeypatch):
        """旧 settings.material_grid 仍能覆盖参考图网格常量。"""
        monkeypatch.setattr(
            "lvjiang.core.config.load_app_config", lambda: {})
        session_env.write_text(json.dumps({
            "settings": {"material_grid": {"rows": 4, "cols": 5, "height": 100}}
        }), encoding="utf-8")
        reset_session_store()
        config = load_user_config()
        assert config.reference_grid.rows == 4
        assert config.reference_grid.cols == 5
        assert config.reference_grid.height == 100
        assert config.reference_grid.width == 122  # 未配置项保持默认

    def test_app_yaml_input_sim_override(self, session_env, monkeypatch):
        """app.yaml 的 input_simulation 节点覆盖输入模拟默认值"""
        monkeypatch.setattr(
            "lvjiang.core.config.load_app_config", lambda: {
                "input_simulation": {
                    "click_random_offset": 9,
                    "before_click_wait": [0.5, 0.9],
                },
            })
        config = load_user_config()
        assert config.input_sim.click_random_offset == 9
        assert config.input_sim.before_click_wait == (0.5, 0.9)
        assert config.input_sim.region_jitter_ratio == 0.25  # 未配置项保持默认

    def test_app_yaml_delay_params_override(self, session_env, monkeypatch):
        """app.yaml 的 delay_params 节点加载为 DelayParam 字典"""
        monkeypatch.setattr(
            "lvjiang.core.config.load_app_config", lambda: {
                "delay_params": {
                    "step_interval": {"label": "步骤间等待", "range": [1.0, 1.2]},
                    "my_wait": {"label": "自定义等待", "range": [1.5, 2.5]},
                },
            })
        config = load_user_config()
        assert config.delay_params["step_interval"].range == (1.0, 1.2)
        assert config.delay_params["my_wait"].label == "自定义等待"
        assert config.delay_params["my_wait"].range == (1.5, 2.5)


class TestSaveSessionNodes:
    def test_save_settings_preserves_other_fields(self, session_env):
        """save_settings 只更新 settings 节点，保留其他字段"""
        session_env.write_text(json.dumps({"active_user": "张三", "ui_state": {"a": 1}}),
                               encoding="utf-8")
        reset_session_store()
        save_settings({"android_capture_method": "screencap"})
        data = json.loads(session_env.read_text(encoding="utf-8"))
        assert data["settings"] == {"android_capture_method": "screencap"}
        assert data["active_user"] == "张三"
        assert data["ui_state"] == {"a": 1}

    def test_save_reference_grid_creates_file(self, session_env):
        """文件不存在时 save_reference_grid 自动创建"""
        grid = {"rows": 2, "cols": 3, "gap": 1, "height": 80, "width": 90}
        save_reference_grid(grid)
        data = json.loads(session_env.read_text(encoding="utf-8"))
        assert data == {"settings": {"reference_grid": grid}}


class TestSaveAppConfig:
    INPUT_SIM = {"before_click_wait": [0.1, 0.3], "click_random_offset": 5,
                 "region_jitter_ratio": 0.2, "after_click_wait": [0.1, 0.2],
                 "mouse_move_duration": [0.3, 0.6]}
    DELAY_PARAMS = {"my_wait": {"label": "自定义", "range": [1.0, 2.0]}}

    def test_save_writes_via_resolver(self, session_env, monkeypatch):
        """save_app_config 经 core.config 写入，且可被重新加载"""
        captured = {}

        def fake_save_merged(self, rel, data):
            captured["rel"] = rel
            captured["data"] = data

        monkeypatch.setattr(
            "lvjiang.core.config.load_app_config", lambda: {
                "input_simulation": self.INPUT_SIM,
                "delay_params": self.DELAY_PARAMS,
            })
        monkeypatch.setattr(
            "lvjiang.core.config.resolver.get_resolver",
            lambda: type("R", (), {"save_merged": fake_save_merged})())

        save_app_config(self.INPUT_SIM, self.DELAY_PARAMS)
        assert captured["rel"] == "app.yaml"
        assert captured["data"]["input_simulation"] == self.INPUT_SIM
        assert captured["data"]["delay_params"] == self.DELAY_PARAMS

        # 重新加载验证 round-trip
        config = load_user_config()
        assert config.input_sim.click_random_offset == 5
        assert config.input_sim.region_jitter_ratio == 0.2
        assert config.delay_params["my_wait"].range == (1.0, 2.0)
