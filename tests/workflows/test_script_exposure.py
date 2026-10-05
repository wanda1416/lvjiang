"""脚本暴露的三层职责：目录约定 / 作者声明 / 用户偏好

- **全集**由 WorkflowDiscoveryPolicy 的目录约定决定，不可配置
- **默认是否展示**由作者声明（hidden 脚本和 dedicated 专用脚本默认不展示）
- **顺序、启停、显示名、性质**是用户偏好，存 session 的 daily.scripts

关键性质：系统新增的脚本自动出现，不会因为用户存过偏好被冻住——
这正是当初把 exposed 存进 workflows.yaml 的病根。
"""

import pytest

from lvjiang.workflows.policy import WorkflowDiscoveryPolicy as Policy
from lvjiang.workflows.preferences import DailyScriptPrefs


@pytest.fixture
def scripts(monkeypatch):
    """三个内置类脚本，其中 c 被作者标记为默认隐藏"""
    catalog = {
        "a": {"id": "a", "name": "甲", "hidden": False, "scope": "daily"},
        "b": {"id": "b", "name": "乙", "hidden": False, "scope": "dedicated"},
        "c": {"id": "c", "name": "丙", "hidden": True, "scope": "daily"},
    }
    monkeypatch.setattr("lvjiang.workflows.discovery.discover_scripts",
                        lambda: list(catalog.values()))
    return catalog


def _prefs(monkeypatch, **kw):
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.load_preferences",
        lambda: DailyScriptPrefs(kw.get("order", []), kw.get("visible", {}),
                                 kw.get("names", {}), kw.get("scopes", {})))


def _ids(monkeypatch, **kw):
    from lvjiang.workflows.discovery import list_exposed_scripts
    _prefs(monkeypatch, **kw)
    return [c["id"] for c in list_exposed_scripts()]


class TestAuthorDeclaration:
    def test_hidden_script_not_shown_by_default(self, scripts, monkeypatch):
        assert _ids(monkeypatch) == ["a"]

    def test_dedicated_script_not_shown_by_default(self, scripts, monkeypatch):
        assert "b" not in _ids(monkeypatch)

    def test_user_can_reveal_dedicated_script(self, scripts, monkeypatch):
        assert "b" in _ids(monkeypatch, visible={"b": True})

    def test_user_can_reveal_hidden_script(self, scripts, monkeypatch):
        assert "c" in _ids(monkeypatch, visible={"c": True})

    def test_user_can_hide_visible_script(self, scripts, monkeypatch):
        assert "a" not in _ids(monkeypatch, visible={"a": False})


class TestNewScriptsAppearAutomatically:
    def test_new_script_shows_even_with_saved_order(self, scripts, monkeypatch):
        """用户存过顺序后，系统新增的脚本仍要出现——这是本次重构的核心目的。"""
        assert _ids(monkeypatch, order=["b", "a"]) == ["a"]
        scripts["new"] = {"id": "new", "name": "新", "hidden": False}
        assert "new" in _ids(monkeypatch, order=["b", "a"])

    def test_saved_order_respected_new_appended(self, scripts, monkeypatch):
        scripts["new"] = {"id": "new", "name": "新", "hidden": False}
        assert _ids(
            monkeypatch, order=["b", "a"], visible={"b": True}
        ) == ["b", "a", "new"]


class TestPolicy:


    def test_internal_any_path_segment(self):
        assert Policy.is_internal("_recorded.wf")
        assert Policy.is_internal("subcall/_draft/x.wf")
        assert not Policy.is_internal("subcall/navigation.wf")
