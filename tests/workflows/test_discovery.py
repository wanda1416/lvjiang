"""脚本发现层测试

覆盖 discover_scripts / list_exposed_scripts 的核心逻辑。
"""


from lvjiang.workflows.discovery import (
    _discover_class_scripts,
    _discover_wf_scripts,
    discover_scripts,
    list_exposed_scripts,
)


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
        wf_file.write_text("#% name: 测试流程\n#% parameters:\n#%   - name: target\n", encoding="utf-8")

        fake_resolver = type("R", (), {
            "enumerate_entities": lambda self, d, p: ["test_flow.wf"],
            "resolve_read": lambda self, rel: wf_file,
        })()
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: fake_resolver,
        )
        result = _discover_wf_scripts()
        assert "test_flow" in result
        assert result["test_flow"]["name"] == "测试流程"
        assert result["test_flow"]["wf_file"] == "test_flow.wf"
        assert len(result["test_flow"]["parameters"]) == 1


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
            "enumerate_entities": lambda self, d, p: ["shared.wf"],
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


class TestListExposedScripts:
    def test_exposed_empty_shows_all(self, tmp_path, monkeypatch):
        """exposed 为空时展示全部"""
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

    def test_exposed_filters_and_orders(self, tmp_path, monkeypatch):
        """exposed 非空时按序过滤"""
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: type("R", (), {
                "enumerate_entities": lambda self, d, p: [],
                "load_merged": lambda self, rel: {"exposed": ["flow_b"]},
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

    def test_overrides_rename(self, tmp_path, monkeypatch):
        """overrides 可改名"""
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.get_resolver",
            lambda: type("R", (), {
                "enumerate_entities": lambda self, d, p: [],
                "load_merged": lambda self, rel: {
                    "exposed": ["flow_a"],
                    "overrides": {"flow_a": {"name": "显示名称"}},
                },
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
