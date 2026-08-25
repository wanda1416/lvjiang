"""系统内置函数测试

覆盖 _build_text / _save / _panel_rows / _panel_cols / _input / _check_env 的可测路径。
confirm / pause / notify 依赖平台原生弹窗，不纳入。
"""


import pytest

from lvjiang.workflows.builtins import get_function
from lvjiang.workflows.engine.signals import WorkflowUserError


def _fn(name):
    fn = get_function(name)
    assert fn is not None, f"内置函数 {name} 未注册"
    return fn


class MockEngine:
    """模拟引擎"""
    def __init__(self, ui_callback=None, save_callback=None, panel_alignments=None,
                 run_env="desktop"):
        self._ui_callback = ui_callback
        self._save_callback = save_callback
        self._panel_alignments = panel_alignments or {}
        self.run_env = run_env


class TestBuildText:
    """测试消息拼接辅助函数（通过 confirm 等间接测试，但 _build_text 是模块私有的）

    由于 _build_text 是私有的，我们通过调用公开函数的行为来验证。
    """
    def test_concat_via_confirm(self):
        """通过 confirm 验证消息拼接（使用 ui_callback 捕获参数）"""
        captured = {}

        def mock_callback(kind, message=""):
            captured["kind"] = kind
            captured["message"] = message
            return True

        engine = MockEngine(ui_callback=mock_callback)
        _fn("confirm")(engine, "消息", "额外1", "额外2")
        assert captured["kind"] == "confirm"
        # confirm 将 _build_text 结果传给 callback
        assert captured["message"] == "消息额外1 额外2"


class TestSave:
    def test_save_with_callback(self):
        """有 save_callback 时调用"""
        called = []
        engine = MockEngine(save_callback=lambda: called.append(True))
        _fn("save")(engine)
        assert called == [True]

    def test_save_without_callback(self):
        """无 save_callback 时不报错"""
        engine = MockEngine(save_callback=None)
        result = _fn("save")(engine)
        assert result == ""

    def test_save_without_engine(self):
        """无 engine 时不报错"""
        result = _fn("save")(None)
        assert result == ""


class TestPanelRows:
    def test_with_alignment(self):
        """有 alignment 时返回行数"""
        cal = type("C", (), {"n_rows": 5, "n_cols": 3})()
        engine = MockEngine(panel_alignments={("scene1", "panel1"): cal})
        result = _fn("panel_rows")(engine, "scene1", "panel1")
        assert result == 5

    def test_without_alignment(self):
        """无 alignment 时返回 0"""
        engine = MockEngine(panel_alignments={})
        result = _fn("panel_rows")(engine, "scene1", "panel1")
        assert result == 0


class TestPanelCols:
    def test_with_alignment(self):
        """有 alignment 时返回列数"""
        cal = type("C", (), {"n_rows": 5, "n_cols": 3})()
        engine = MockEngine(panel_alignments={("scene1", "panel1"): cal})
        result = _fn("panel_cols")(engine, "scene1", "panel1")
        assert result == 3

    def test_without_alignment(self):
        """无 alignment 时返回 0"""
        engine = MockEngine(panel_alignments={})
        result = _fn("panel_cols")(engine, "scene1", "panel1")
        assert result == 0


class TestInput:
    def test_without_engine_returns_none(self):
        """无 engine 时返回 None"""
        result = _fn("input")(None, "请输入:")
        assert result is None

    def test_without_callback_returns_none(self):
        """无 ui_callback 时返回 None"""
        engine = MockEngine(ui_callback=None)
        result = _fn("input")(engine, "请输入:")
        assert result is None

    def test_with_callback_returns_value(self):
        """有 ui_callback 时返回回调结果"""
        engine = MockEngine(ui_callback=lambda kind, prompt="": "用户输入")
        result = _fn("input")(engine, "请输入:")
        assert result == "用户输入"


class TestCheckEnv:
    @pytest.mark.parametrize("allowed", [["android"], "android"])
    def test_matching_env_returns_true(self, allowed):
        """允许列表和单个环境名均可匹配当前环境。"""
        engine = MockEngine(run_env="android")

        assert _fn("check_env")(engine, allowed) is True

    def test_mismatching_env_raises_workflow_user_error(self):
        """环境不匹配时抛出可由工作流引擎处理的用户错误。"""
        engine = MockEngine(run_env="android")

        with pytest.raises(
            WorkflowUserError,
            match=r"当前环境 'android' 不在允许列表 \['desktop'\]",
        ):
            _fn("check_env")(engine, ["desktop"])

    def test_env_reads_engine_snapshot(self):
        engine = MockEngine(run_env="android")

        assert _fn("env")(engine) == "android"
        assert _fn("env")(engine, "android") is True
        assert _fn("env")(engine, "desktop") is False
