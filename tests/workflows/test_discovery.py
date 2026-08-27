"""脚本发现层测试

覆盖 discover_scripts / list_exposed_scripts 的核心逻辑。
"""

from lvjiang.workflows.discovery import (
    _discover_class_scripts,
    _discover_wf_scripts,
    discover_scripts,
    list_exposed_scripts,
)
from lvjiang.workflows.metadata import METADATA_WARNING


class TestDiscoverWfScripts:
    def test_empty_when_no_workflows(self, tmp_path, monkeypatch):
        """无 .wf 文件时返回空 dict"""
        fake_resolver = type("R", (), {
            "enumerate_entities": lambda self, d, p: [],
        })()
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: fake_resolver,
        )
        result = _discover_wf_scripts()
        assert result == {}

    def test_discovers_wf_files(self, tmp_path, monkeypatch):
        """扫描到 .wf 文件并解析元数据"""
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        wf_file = wf_dir / "test_flow.wf"
        wf_file.write_text(
            "#% name: 测试流程\n"
            "#% note: 运行前请确认页面。\n"
            "#% parameters:\n"
            "#%   - name: target\n"
            "#%     options: [default]\n",
            encoding="utf-8",
        )

        fake_resolver = type("R", (), {
            "enumerate_entities": lambda self, d, p: (
                ["test_flow.wf"] if d == "workflows" else []),
            "resolve_read": lambda self, rel: wf_file,
        })()
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: fake_resolver,
        )
        result = _discover_wf_scripts()
        assert "test_flow" in result
        assert result["test_flow"]["name"] == "测试流程"
        assert result["test_flow"]["note"] == "运行前请确认页面。"
        assert result["test_flow"]["wf_file"] == "test_flow.wf"
        assert len(result["test_flow"]["parameters"]) == 1

    def test_discovers_standalone_subdirectory(self, tmp_path, monkeypatch):
        """standalone 下脚本以文件 stem 注册，并保留相对路径。"""
        wf_file = tmp_path / "workflows" / "standalone" / "fengshajiusi.wf"
        wf_file.parent.mkdir(parents=True)
        wf_file.write_text("#% name: 风沙酒肆\n", encoding="utf-8")

        fake_resolver = type("R", (), {
            "enumerate_entities": lambda self, d, p: (
                ["fengshajiusi.wf"]
                if d == "workflows/standalone" else []
            ),
            "resolve_read": lambda self, rel: wf_file,
        })()
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: fake_resolver,
        )

        result = _discover_wf_scripts()

        assert result["fengshajiusi"]["name"] == "风沙酒肆"
        assert result["fengshajiusi"]["wf_file"] == (
            "standalone/fengshajiusi.wf")
        assert result["fengshajiusi"]["batchable"] is False

    def test_bad_metadata_warns_only_its_own_script(self, tmp_path, monkeypatch):
        """一个 wf 元数据错误不能中断发现，也不能影响另一个 wf。"""
        bad = tmp_path / "bad.wf"
        good = tmp_path / "good.wf"
        bad.write_text("#% name: [unclosed\nlog \"bad meta\"\n", encoding="utf-8")
        good.write_text("#% name: 正常脚本\nlog \"ok\"\n", encoding="utf-8")
        paths = {"workflows/bad.wf": bad, "workflows/good.wf": good}
        fake_resolver = type("R", (), {
            "enumerate_entities": lambda self, d, p: (
                ["bad.wf", "good.wf"] if d == "workflows" else []
            ),
            "resolve_read": lambda self, rel: paths[rel],
        })()
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: fake_resolver,
        )

        result = _discover_wf_scripts()

        assert set(result) == {"bad", "good"}
        assert result["bad"]["note"] == METADATA_WARNING
        assert result["bad"]["parameters"] == []
        assert result["good"]["name"] == "正常脚本"
        assert result["good"]["note"] == ""


class TestDiscoverClassScripts:
    def test_discovers_registered_classes(self, monkeypatch):
        """从注册表发现内置类实现"""
        class FakeWorkflow:
            DISPLAY_NAME = "测试内置"
            PARAMETERS = [{"name": "param1"}]

        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.list_workflows",
            lambda: ["test_builtin"],
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.get_workflow_class",
            lambda name: FakeWorkflow,
        )
        result = _discover_class_scripts()
        assert "test_builtin" in result
        assert result["test_builtin"]["name"] == "测试内置"
        assert result["test_builtin"]["class"] == "test_builtin"
        assert len(result["test_builtin"]["parameters"]) == 1

    def test_skips_failed_import(self, monkeypatch):
        """类导入失败时跳过，不影响其他"""
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.list_workflows",
            lambda: ["good", "bad"],
        )

        def fake_get(name):
            if name == "bad":
                raise ImportError("no module")
            return type("W", (), {"DISPLAY_NAME": "好", "PARAMETERS": []})

        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.get_workflow_class",
            fake_get,
        )
        result = _discover_class_scripts()
        assert "good" in result
        assert "bad" not in result


class TestDiscoverScripts:
    def test_class_overrides_wf(self, tmp_path, monkeypatch):
        """同 id 时 class 覆盖 .wf"""
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        wf_file = wf_dir / "shared.wf"
        wf_file.write_text("#% name: WF版本\n", encoding="utf-8")

        fake_resolver = type("R", (), {
            "enumerate_entities": lambda self, d, p: (
                ["shared.wf"] if d == "workflows" else []),
            "resolve_read": lambda self, rel: wf_file,
        })()
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: fake_resolver,
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.list_workflows",
            lambda: ["shared"],
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.get_workflow_class",
            lambda name: type("W", (), {"DISPLAY_NAME": "Class版本", "PARAMETERS": []}),
        )
        result = discover_scripts()
        assert len(result) == 1
        assert result[0]["id"] == "shared"
        assert result[0]["name"] == "Class版本"
        assert result[0]["class"] == "shared"

    def test_returns_sorted_by_id(self, tmp_path, monkeypatch):
        """结果按 id 排序"""
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: type("R", (), {
                "enumerate_entities": lambda self, d, p: [],
            })(),
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.list_workflows",
            lambda: ["z_flow", "a_flow", "m_flow"],
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.get_workflow_class",
            lambda name: type("W", (), {"DISPLAY_NAME": name, "PARAMETERS": []}),
        )
        result = discover_scripts()
        ids = [r["id"] for r in result]
        assert ids == ["a_flow", "m_flow", "z_flow"]


def _stub_prefs(monkeypatch, *, order=None, visible=None, names=None, scopes=None):
    """打桩用户偏好，避免用例读到真实 session"""
    from lvjiang.workflows.preferences import DailyScriptPrefs
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.load_preferences",
        lambda: DailyScriptPrefs(order or [], visible or {}, names or {}, scopes or {}))
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.migrate_legacy_workflows_yaml", lambda: False)


class TestListExposedScripts:
    def test_no_preference_shows_all(self, tmp_path, monkeypatch):
        """没有任何偏好时展示全部（作者未声明 hidden）"""
        _stub_prefs(monkeypatch)
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: type("R", (), {
                "enumerate_entities": lambda self, d, p: [],
                "load_merged": lambda self, rel: {},
            })(),
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.list_workflows",
            lambda: ["flow_a", "flow_b"],
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.get_workflow_class",
            lambda name: type("W", (), {"DISPLAY_NAME": name, "PARAMETERS": []}),
        )
        result = list_exposed_scripts()
        assert len(result) == 2

    def test_user_preference_filters_and_orders(self, tmp_path, monkeypatch):
        """用户偏好可隐藏脚本并指定顺序"""
        _stub_prefs(monkeypatch, order=["flow_b"], visible={"flow_a": False})
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: type("R", (), {
                "enumerate_entities": lambda self, d, p: [],
                "load_merged": lambda self, rel: {},
            })(),
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.list_workflows",
            lambda: ["flow_a", "flow_b"],
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.get_workflow_class",
            lambda name: type("W", (), {"DISPLAY_NAME": name, "PARAMETERS": []}),
        )
        result = list_exposed_scripts()
        assert len(result) == 1
        assert result[0]["id"] == "flow_b"

    def test_user_preference_rename(self, tmp_path, monkeypatch):
        """用户偏好可改显示名"""
        _stub_prefs(monkeypatch, names={"flow_a": "显示名称"})
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: type("R", (), {
                "enumerate_entities": lambda self, d, p: [],
                "load_merged": lambda self, rel: {},
            })(),
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.list_workflows",
            lambda: ["flow_a"],
        )
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.implementations.get_workflow_class",
            lambda name: type("W", (), {"DISPLAY_NAME": "原名", "PARAMETERS": []}),
        )
        result = list_exposed_scripts()
        assert result[0]["name"] == "显示名称"
