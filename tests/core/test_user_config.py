"""用户配置加载链与保存函数测试

覆盖链路：
- 代码默认值 ← session.json（settings/material_grid）
- 代码默认值 ← app.yaml（input_simulation/delay_params，经 core.config 合并）

保存函数：
- save_settings / save_material_grid（读改写 session.json 对应顶层节点）
- save_app_config（经 core.config 写入 app.yaml）
"""

import json

import pytest

from lvjiang.config import (
    load_user_config,
    save_app_config,
    save_material_grid,
    save_settings,
)


@pytest.fixture
def session(tmp_path):
    """独立的 session.json 路径（不存在）"""
    return tmp_path / "session.json"


class TestLoadUserConfig:
    def test_defaults_without_files(self, session, monkeypatch):
        """无任何配置文件时使用代码默认值"""
        monkeypatch.setattr(
            "lvjiang.config._load_app_config", lambda: {})
        config = load_user_config(session)
        assert config.adb_capture_streaming is True
        assert config.desktop_background_input is True
        assert config.desktop_window_title == ""
        assert config.material_grid.rows == 3
        assert config.material_grid.cols == 6
        assert config.material_grid.height == 122
        assert config.material_grid.width == 122
        assert config.input_sim.click_random_offset == 3
        assert config.delay_params == {}

    def test_session_settings_override(self, session, monkeypatch):
        """session.json 的 settings 节点覆盖基础配置"""
        monkeypatch.setattr(
            "lvjiang.config._load_app_config", lambda: {})
        session.write_text(json.dumps({
            "settings": {
                "adb_capture_streaming": False,
                "desktop_window_title": "手机投屏",
            }
        }), encoding="utf-8")
        config = load_user_config(session)
        assert config.adb_capture_streaming is False
        assert config.desktop_window_title == "手机投屏"
        assert config.desktop_background_input is True  # 未配置项保持默认

    def test_session_material_grid_override(self, session, monkeypatch):
        """session.json 的 material_grid 节点覆盖网格常量"""
        monkeypatch.setattr(
            "lvjiang.config._load_app_config", lambda: {})
        session.write_text(json.dumps({
            "material_grid": {"rows": 4, "cols": 5, "height": 100}
        }), encoding="utf-8")
        config = load_user_config(session)
        assert config.material_grid.rows == 4
        assert config.material_grid.cols == 5
        assert config.material_grid.height == 100
        assert config.material_grid.width == 122  # 未配置项保持默认

    def test_app_yaml_input_sim_override(self, session, monkeypatch):
        """app.yaml 的 input_simulation 节点覆盖输入模拟默认值"""
        monkeypatch.setattr(
            "lvjiang.config._load_app_config", lambda: {
                "input_simulation": {
                    "click_random_offset": 9,
                    "before_click_wait": [0.5, 0.9],
                },
            })
        config = load_user_config(session)
        assert config.input_sim.click_random_offset == 9
        assert config.input_sim.before_click_wait == (0.5, 0.9)
        assert config.input_sim.region_jitter_ratio == 0.25  # 未配置项保持默认

    def test_app_yaml_delay_params_override(self, session, monkeypatch):
        """app.yaml 的 delay_params 节点加载为 DelayParam 字典"""
        monkeypatch.setattr(
            "lvjiang.config._load_app_config", lambda: {
                "delay_params": {
                    "step_interval": {"label": "步骤间等待", "range": [1.0, 1.2]},
                    "my_wait": {"label": "自定义等待", "range": [1.5, 2.5]},
                },
            })
        config = load_user_config(session)
        assert config.delay_params["step_interval"].range == (1.0, 1.2)
        assert config.delay_params["my_wait"].label == "自定义等待"
        assert config.delay_params["my_wait"].range == (1.5, 2.5)


class TestSaveSessionNodes:
    def test_save_settings_preserves_other_fields(self, session):
        """save_settings 只更新 settings 节点，保留其他字段"""
        session.write_text(json.dumps({"active_user": "张三", "ui_state": {"a": 1}}),
                           encoding="utf-8")
        save_settings({"adb_capture_streaming": False}, session)
        data = json.loads(session.read_text(encoding="utf-8"))
        assert data["settings"] == {"adb_capture_streaming": False}
        assert data["active_user"] == "张三"
        assert data["ui_state"] == {"a": 1}

    def test_save_material_grid_creates_file(self, session):
        """文件不存在时 save_material_grid 自动创建"""
        grid = {"rows": 2, "cols": 3, "gap": 1, "height": 80, "width": 90}
        save_material_grid(grid, session)
        data = json.loads(session.read_text(encoding="utf-8"))
        assert data == {"material_grid": grid}


class TestSaveAppConfig:
    INPUT_SIM = {"before_click_wait": [0.1, 0.3], "click_random_offset": 5,
                 "region_jitter_ratio": 0.2, "after_click_wait": [0.1, 0.2],
                 "mouse_move_duration": [0.3, 0.6]}
    DELAY_PARAMS = {"my_wait": {"label": "自定义", "range": [1.0, 2.0]}}

    def test_save_writes_via_resolver(self, tmp_path, monkeypatch):
        """save_app_config 经 core.config 写入，且可被重新加载"""
        captured = {}

        def fake_save_merged(self, rel, data):
            captured["rel"] = rel
            captured["data"] = data

        monkeypatch.setattr(
            "lvjiang.config._load_app_config", lambda: {
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
        session = tmp_path / "session.json"
        config = load_user_config(session)
        assert config.input_sim.click_random_offset == 5
        assert config.input_sim.region_jitter_ratio == 0.2
        assert config.delay_params["my_wait"].range == (1.0, 2.0)
