"""用户配置加载链与保存函数测试

覆盖链路：代码默认值 ← session.json（settings/material_grid）← workflows.yaml（input_delay）
保存函数：save_settings / save_material_grid（读改写 session.json 节点）、
save_input_delay（workflows.yaml 顶层块文本级替换/追加，不破坏 flows 区注释）。
"""

import json

import pytest

from src.config import (
    load_user_config,
    save_input_delay,
    save_material_grid,
    save_settings,
)

FLOWS_TEXT = """# 工作流注册配置
#
# 简单工作流只需声明 wf_file 即可。

flows:
  - id: demo
    name: 演示
    wf_file: demo.wf
    required_scenes:
      - game_main_page
"""


@pytest.fixture
def paths(tmp_path):
    """独立的 session.json / workflows.yaml 路径（均不存在）"""
    return tmp_path / "session.json", tmp_path / "workflows.yaml"


class TestLoadUserConfig:
    def test_defaults_without_files(self, paths):
        """无任何配置文件时使用代码默认值"""
        session, workflows = paths
        config = load_user_config(session, workflows)
        assert config.adb_capture_streaming is True
        assert config.desktop_background_input is True
        assert config.desktop_window_title == ""
        assert config.material_grid.rows == 3
        assert config.material_grid.cols == 6
        assert config.material_grid.height == 122
        assert config.material_grid.width == 122
        assert config.input_delay.click_random_offset == 3

    def test_session_settings_override(self, paths):
        """session.json 的 settings 节点覆盖基础配置"""
        session, workflows = paths
        session.write_text(json.dumps({
            "settings": {
                "adb_capture_streaming": False,
                "desktop_window_title": "手机投屏",
            }
        }), encoding="utf-8")
        config = load_user_config(session, workflows)
        assert config.adb_capture_streaming is False
        assert config.desktop_window_title == "手机投屏"
        assert config.desktop_background_input is True  # 未配置项保持默认

    def test_session_material_grid_override(self, paths):
        """session.json 的 material_grid 节点覆盖网格常量"""
        session, workflows = paths
        session.write_text(json.dumps({
            "material_grid": {"rows": 4, "cols": 5, "height": 100}
        }), encoding="utf-8")
        config = load_user_config(session, workflows)
        assert config.material_grid.rows == 4
        assert config.material_grid.cols == 5
        assert config.material_grid.height == 100
        assert config.material_grid.width == 122  # 未配置项保持默认

    def test_workflows_input_delay_override(self, paths):
        """workflows.yaml 顶层 input_delay 覆盖延迟默认值"""
        session, workflows = paths
        workflows.write_text(
            FLOWS_TEXT + "\ninput_delay:\n  click_random_offset: 9\n"
                         "  step_interval: [0.5, 0.9]\n",
            encoding="utf-8")
        config = load_user_config(session, workflows)
        assert config.input_delay.click_random_offset == 9
        assert config.input_delay.step_interval == (0.5, 0.9)
        assert config.input_delay.region_jitter_ratio == 0.25  # 未配置项保持默认


class TestSaveSessionNodes:
    def test_save_settings_preserves_other_fields(self, paths):
        """save_settings 只更新 settings 节点，保留其他字段"""
        session, _ = paths
        session.write_text(json.dumps({"active_user": "张三", "ui_state": {"a": 1}}),
                           encoding="utf-8")
        save_settings({"adb_capture_streaming": False}, session)
        data = json.loads(session.read_text(encoding="utf-8"))
        assert data["settings"] == {"adb_capture_streaming": False}
        assert data["active_user"] == "张三"
        assert data["ui_state"] == {"a": 1}

    def test_save_material_grid_creates_file(self, paths):
        """文件不存在时 save_material_grid 自动创建"""
        session, _ = paths
        grid = {"rows": 2, "cols": 3, "gap": 1, "height": 80, "width": 90}
        save_material_grid(grid, session)
        data = json.loads(session.read_text(encoding="utf-8"))
        assert data == {"material_grid": grid}


class TestSaveInputDelay:
    DELAY = {"before_click_wait": [0.1, 0.3], "click_random_offset": 5,
             "region_jitter_ratio": 0.2}

    def test_append_when_no_block(self, paths):
        """无 input_delay 块时末尾追加，flows 区原样保留"""
        _, workflows = paths
        workflows.write_text(FLOWS_TEXT, encoding="utf-8")
        save_input_delay(self.DELAY, workflows)
        text = workflows.read_text(encoding="utf-8")
        assert FLOWS_TEXT.rstrip("\n") in text  # flows 区（含注释）未被改动
        assert "input_delay:\n  before_click_wait: [0.1, 0.3]" in text
        # 追加结果可被正常解析且与保存值一致
        config = load_user_config(paths[0], workflows)
        assert config.input_delay.click_random_offset == 5

    def test_replace_existing_block(self, paths):
        """已有 input_delay 块整块替换（含旧注释行），不产生重复块"""
        _, workflows = paths
        workflows.write_text(
            FLOWS_TEXT + "\n# 旧注释\ninput_delay:\n  click_random_offset: 99\n",
            encoding="utf-8")
        save_input_delay(self.DELAY, workflows)
        text = workflows.read_text(encoding="utf-8")
        assert text.count("input_delay:") == 1
        assert "99" not in text
        assert "# 旧注释" not in text
        config = load_user_config(paths[0], workflows)
        assert config.input_delay.click_random_offset == 5
        assert config.input_delay.region_jitter_ratio == 0.2

    def test_replace_block_in_middle_keeps_tail(self, paths):
        """input_delay 块在文件中部时，其后的顶层内容原样保留"""
        _, workflows = paths
        workflows.write_text(
            "input_delay:\n  click_random_offset: 99\n\n" + FLOWS_TEXT,
            encoding="utf-8")
        save_input_delay(self.DELAY, workflows)
        text = workflows.read_text(encoding="utf-8")
        assert text.count("input_delay:") == 1
        assert "wf_file: demo.wf" in text
        config = load_user_config(paths[0], workflows)
        assert config.input_delay.click_random_offset == 5
