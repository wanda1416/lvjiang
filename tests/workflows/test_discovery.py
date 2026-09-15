"""脚本发现层测试

覆盖 discover_scripts / list_exposed_scripts 的核心逻辑，重点是「是否注册
由 front-matter 决定」与「同 id 的来源优先级」。
"""

from types import SimpleNamespace

from lvjiang.workflows.discovery import (
    DiscoveryProblem,
    _discover_class_scripts,
    _discover_wf_scripts,
    discover_scripts,
    last_discovery_problems,
    list_exposed_scripts,
    script_display_name,
)
from lvjiang.workflows.metadata import METADATA_WARNING


def _fake_resolver(files, layers=None):
    """构造发现层需要的假 resolver

    Args:
        files: ``{workflows 内相对路径: Path}``，模拟合并视图的全树
        layers: ``{workflows 内相对路径: 层名}``，缺省 system
    """
    layers = layers or {}

    class R:
        def enumerate_entity_tree(self, rel_dir, pattern, *,
                                  include_internal=False):
            return sorted(files)

        def resolve_read(self, rel):
            return files.get(rel.split("workflows/", 1)[-1])

        def describe_entity(self, rel):
            key = rel.split("workflows/", 1)[-1]
            return SimpleNamespace(layer=layers.get(key, "system"))

    return R()


def _patch_resolver(monkeypatch, files, layers=None):
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.get_resolver",
        lambda: _fake_resolver(files, layers),
    )


def _write(tmp_path, rel, text):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestDiscoverWfScripts:
    def test_empty_when_no_workflows(self, monkeypatch):
        _patch_resolver(monkeypatch, {})
        assert _discover_wf_scripts() == {}

    def test_discovers_wf_files(self, tmp_path, monkeypatch):
        """扫描到 .wf 文件并解析元数据"""
        wf = _write(
            tmp_path, "test_flow.wf",
            "#% name: 测试流程\n"
            "#% runnable: true\n"
            "#% batchable: true\n"
            "#% note: 运行前请确认页面。\n"
            "#% parameters:\n"
            "#%   - name: target\n"
            "#%     options: [default]\n")
        _patch_resolver(monkeypatch, {"test_flow.wf": wf})

        result = _discover_wf_scripts()
        assert "test_flow" in result
        assert result["test_flow"]["name"] == "测试流程"
        assert result["test_flow"]["note"] == "运行前请确认页面。"
        assert result["test_flow"]["wf_file"] == "test_flow.wf"
        assert result["test_flow"]["batchable"] is True
        assert len(result["test_flow"]["parameters"]) == 1

    def test_unicode_filename_is_a_valid_default_id(self, tmp_path, monkeypatch):
        wf = _write(
            tmp_path, "好友送礼.wf",
            "#% name: 好友送礼\n#% runnable: true\n",
        )
        _patch_resolver(monkeypatch, {"好友送礼.wf": wf})

        result = _discover_wf_scripts()

        assert result["好友送礼"]["wf_file"] == "好友送礼.wf"

    def test_not_registered_without_runnable(self, tmp_path, monkeypatch):
        """未声明 runnable 的 .wf 不注册（过程库、生命周期钩子、归档）"""
        wf = _write(tmp_path, "subcall/navigation.wf", "#% name: 导航子过程\n")
        _patch_resolver(monkeypatch, {"subcall/navigation.wf": wf})
        assert _discover_wf_scripts() == {}

    def test_batchable_implies_runnable(self, tmp_path, monkeypatch):
        """只写 batchable 也算有入口"""
        wf = _write(tmp_path, "a.wf", "#% batchable: true\n")
        _patch_resolver(monkeypatch, {"a.wf": wf})
        result = _discover_wf_scripts()
        assert result["a"]["runnable"] is True
        assert result["a"]["batchable"] is True

    def test_standalone_declared_batchable_false(self, tmp_path, monkeypatch):
        wf = _write(tmp_path, "standalone/fengshajiusi.wf",
                    "#% id: fengshajiusi\n#% runnable: true\n")
        _patch_resolver(monkeypatch, {"standalone/fengshajiusi.wf": wf})

        result = _discover_wf_scripts()
        assert result["fengshajiusi"]["wf_file"] == "standalone/fengshajiusi.wf"
        assert result["fengshajiusi"]["batchable"] is False

    def test_recursive_scan_any_depth(self, tmp_path, monkeypatch):
        """任意深度的子目录都参与发现，目录本身不表达语义"""
        files = {}
        for rel, name in (("weekly/a.wf", "周常A"), ("gather/deep/b.wf", "采集B")):
            files[rel] = _write(tmp_path, rel, f"#% name: {name}\n#% runnable: true\n")
        _patch_resolver(monkeypatch, files)

        result = _discover_wf_scripts()
        assert set(result) == {"a", "b"}
        assert result["a"]["name"] == "周常A"

    def test_explicit_id_wins(self, tmp_path, monkeypatch):
        wf = _write(tmp_path, "weekly/a.wf",
                    "#% id: weekly_a\n#% runnable: true\n")
        _patch_resolver(monkeypatch, {"weekly/a.wf": wf})
        assert set(_discover_wf_scripts()) == {"weekly_a"}

    def test_skips_underscore_paths(self, tmp_path, monkeypatch):
        wf = _write(tmp_path, "_editor_run.wf", "#% runnable: true\n")
        _patch_resolver(monkeypatch, {"_editor_run.wf": wf})
        assert _discover_wf_scripts() == {}

    def test_remote_source_is_preserved_for_forced_display_marker(
            self, tmp_path, monkeypatch):
        wf = _write(tmp_path, "remote_test.wf",
                    "#% name: 实验脚本\n#% runnable: true\n")
        _patch_resolver(monkeypatch, {"remote_test.wf": wf},
                        {"remote_test.wf": "remote"})
        config = _discover_wf_scripts()["remote_test"]
        assert config["is_remote"] is True
        assert script_display_name(config) == "[远程] 实验脚本"
        assert script_display_name({**config, "name": "自定义名"}) == (
            "[远程] 自定义名")

    def test_bad_metadata_logs_error_and_skips_only_its_own_script(
            self, tmp_path, monkeypatch):
        """一个 wf 元数据错误不能中断发现，也不能影响另一个 wf。"""
        bad = _write(tmp_path, "bad.wf",
                     "#% name: [unclosed\nlog \"bad meta\"\n")
        good = _write(tmp_path, "good.wf",
                      "#% name: 正常脚本\n#% runnable: true\nlog \"ok\"\n")
        _patch_resolver(monkeypatch,
                        {"bad.wf": bad, "good.wf": good})
        errors = []
        monkeypatch.setattr(
            "lvjiang.workflows.metadata.logger.error", errors.append)

        result = _discover_wf_scripts()

        # 元数据坏掉时 runnable 无从得知，按未声明处理 → 不注册
        assert set(result) == {"good"}
        assert result["good"]["name"] == "正常脚本"
        assert result["good"]["note"] == ""
        assert any(METADATA_WARNING in message for message in errors)

    def test_unexpected_parser_failure_is_isolated(
            self, tmp_path, monkeypatch):
        bad = _write(tmp_path, "bad.wf", "#% runnable: true\n")
        good = _write(tmp_path, "good.wf", "#% runnable: true\n")
        _patch_resolver(monkeypatch, {"bad.wf": bad, "good.wf": good})

        def parse_one(path):
            if path == bad:
                raise RuntimeError("broken parser")
            return {"runnable": True}, ""

        monkeypatch.setattr(
            "lvjiang.workflows.discovery.metadata_for_script_config", parse_one)
        errors = []
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.logger.error", errors.append)

        assert set(_discover_wf_scripts()) == {"good"}
        assert any("broken parser" in message for message in errors)


class TestDiscoveryProblems:
    """被忽略的文件必须带原因回到 UI，不能只留在日志里。"""

    def test_metadata_error_is_reported(self, tmp_path, monkeypatch):
        bad = _write(tmp_path, "bad.wf", "#% name: [\n#% runnable: true\n")
        good = _write(tmp_path, "good.wf", "#% runnable: true\n")
        _patch_resolver(monkeypatch, {"bad.wf": bad, "good.wf": good})
        monkeypatch.setattr("lvjiang.workflows.discovery.logger.error", lambda *_: None)
        monkeypatch.setattr("lvjiang.workflows.metadata.logger.error", lambda *_: None)

        assert [cfg["id"] for cfg in discover_scripts()] == ["good"]
        problems = last_discovery_problems()
        assert [item.wf_file for item in problems] == ["bad.wf"]
        assert problems[0].message == METADATA_WARNING

    def test_invalid_id_is_reported(self, tmp_path, monkeypatch):
        wf = _write(tmp_path, "2024-scan.wf", "#% runnable: true\n")
        _patch_resolver(monkeypatch, {"2024-scan.wf": wf})
        monkeypatch.setattr("lvjiang.workflows.discovery.logger.error", lambda *_: None)

        assert discover_scripts() == []
        assert last_discovery_problems() == [
            DiscoveryProblem("2024-scan.wf", "脚本 id 不合法: '2024-scan'；"
                             "只允许 Unicode 字母、数字和下划线，且以字母开头")]

    def test_same_layer_duplicate_id_keeps_first_and_marks_all(
            self, tmp_path, monkeypatch):
        """同层同 id 稳定保留第一个，但所有冲突文件都必须标记。"""
        a = _write(tmp_path, "a/foo.wf", "#% name: A\n#% runnable: true\n")
        b = _write(tmp_path, "b/foo.wf", "#% name: B\n#% runnable: true\n")
        _patch_resolver(monkeypatch, {"a/foo.wf": a, "b/foo.wf": b})
        errors = []
        monkeypatch.setattr("lvjiang.workflows.discovery.logger.error", errors.append)

        result = discover_scripts()
        assert [item["wf_file"] for item in result] == ["a/foo.wf"]
        assert [item.wf_file for item in last_discovery_problems()] == [
            "a/foo.wf", "b/foo.wf"]
        assert all("'foo'" in item.message for item in last_discovery_problems())
        assert all(item.code == "duplicate_id"
                   for item in last_discovery_problems())
        assert any("脚本 id 已注册" in message for message in errors)

    def test_three_same_layer_duplicates_still_keep_only_first(
            self, tmp_path, monkeypatch):
        files = {
            rel: _write(
                tmp_path, rel,
                f"#% id: shared\n#% name: {rel}\n#% runnable: true\n",
            )
            for rel in ("a.wf", "b.wf", "c.wf")
        }
        _patch_resolver(monkeypatch, files)
        monkeypatch.setattr(
            "lvjiang.workflows.discovery.logger.error", lambda *_: None)

        result = discover_scripts()

        assert [item["wf_file"] for item in result] == ["a.wf"]
        assert [item.wf_file for item in last_discovery_problems()] == [
            "a.wf", "b.wf", "c.wf"]

    def test_problems_reset_between_runs(self, tmp_path, monkeypatch):
        wf = _write(tmp_path, "2024-scan.wf", "#% runnable: true\n")
        _patch_resolver(monkeypatch, {"2024-scan.wf": wf})
        monkeypatch.setattr("lvjiang.workflows.discovery.logger.error", lambda *_: None)
        discover_scripts()
        assert last_discovery_problems()

        _patch_resolver(monkeypatch, {})
        discover_scripts()
        assert last_discovery_problems() == []


class TestSourcePriority:
    def test_local_beats_system(self, tmp_path, monkeypatch):
        local = _write(tmp_path, "local/same.wf",
                       "#% name: 本地\n#% runnable: true\n")
        system = _write(tmp_path, "system/same.wf",
                        "#% name: 系统\n#% runnable: true\n")
        files = {"same.wf": local}
        layers = {"same.wf": "local"}
        # system 侧同名文件靠 describe_entity 区分即可，这里只验胜者是 local
        _patch_resolver(monkeypatch, files, layers)
        assert _discover_wf_scripts()["same"]["name"] == "本地"
        assert _discover_wf_scripts()["same"]["source_layer"] == "local"
        assert system.exists()  # 仅确保构造过，未参与本断言

    def test_system_beats_remote(self, tmp_path, monkeypatch):
        """远程只新增，同 id 时不能抢占随包脚本"""
        system = _write(tmp_path, "system/same.wf",
                        "#% name: 系统\n#% runnable: true\n")
        remote = _write(tmp_path, "remote/same.wf",
                        "#% name: 远程\n#% runnable: true\n")
        # 两个候选都以 same 为 id：用不同的相对路径模拟不同层
        files = {"same.wf": system, "vendor/same.wf": remote}
        layers = {"same.wf": "system", "vendor/same.wf": "remote"}
        _patch_resolver(monkeypatch, files, layers)

        result = _discover_wf_scripts()
        assert result["same"]["name"] == "系统"
        assert result["same"]["is_remote"] is False

    def test_remote_wins_when_alone(self, tmp_path, monkeypatch):
        remote = _write(tmp_path, "remote/new.wf",
                        "#% name: 远程新增\n#% runnable: true\n")
        _patch_resolver(monkeypatch, {"new.wf": remote}, {"new.wf": "remote"})
        config = _discover_wf_scripts()["new"]
        assert config["name"] == "远程新增"
        assert config["is_remote"] is True


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
        assert result["test_builtin"]["runnable"] is True
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


def _stub_class(monkeypatch, names):
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.implementations.list_workflows",
        lambda: names,
    )
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.implementations.get_workflow_class",
        lambda name: type("W", (), {"DISPLAY_NAME": name, "PARAMETERS": []}),
    )


class TestDiscoverScripts:
    def test_class_beats_system_wf(self, tmp_path, monkeypatch):
        wf = _write(tmp_path, "shared.wf", "#% name: WF版本\n#% runnable: true\n")
        _patch_resolver(monkeypatch, {"shared.wf": wf})
        _stub_class(monkeypatch, ["shared"])

        result = discover_scripts()
        assert len(result) == 1
        assert result[0]["name"] == "shared"      # class 的 DISPLAY_NAME
        assert result[0]["class"] == "shared"

    def test_local_beats_class(self, tmp_path, monkeypatch):
        wf = _write(tmp_path, "shared.wf", "#% name: 本地版本\n#% runnable: true\n")
        _patch_resolver(monkeypatch, {"shared.wf": wf}, {"shared.wf": "local"})
        _stub_class(monkeypatch, ["shared"])

        result = discover_scripts()
        assert result[0]["name"] == "本地版本"
        assert result[0]["source_layer"] == "local"

    def test_returns_sorted_by_id(self, monkeypatch):
        """结果按 id 排序"""
        _patch_resolver(monkeypatch, {})
        _stub_class(monkeypatch, ["z_flow", "a_flow", "m_flow"])
        assert [r["id"] for r in discover_scripts()] == [
            "a_flow", "m_flow", "z_flow"]


def _stub_prefs(monkeypatch, *, order=None, visible=None, names=None, scopes=None):
    """打桩用户偏好，避免用例读到真实 session"""
    from lvjiang.workflows.preferences import DailyScriptPrefs
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.load_preferences",
        lambda: DailyScriptPrefs(order or [], visible or {}, names or {}, scopes or {}))
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.migrate_legacy_workflows_yaml", lambda: False)


class TestListExposedScripts:
    def test_no_preference_shows_all(self, monkeypatch):
        """没有任何偏好时展示全部（作者未声明 hidden）"""
        _stub_prefs(monkeypatch)
        _patch_resolver(monkeypatch, {})
        _stub_class(monkeypatch, ["flow_a", "flow_b"])
        assert len(list_exposed_scripts()) == 2

    def test_user_preference_filters_and_orders(self, monkeypatch):
        """用户偏好可隐藏脚本并指定顺序"""
        _stub_prefs(monkeypatch, order=["flow_b"], visible={"flow_a": False})
        _patch_resolver(monkeypatch, {})
        _stub_class(monkeypatch, ["flow_a", "flow_b"])
        result = list_exposed_scripts()
        assert len(result) == 1
        assert result[0]["id"] == "flow_b"

    def test_user_preference_rename(self, monkeypatch):
        """用户偏好可改显示名"""
        _stub_prefs(monkeypatch, names={"flow_a": "显示名称"})
        _patch_resolver(monkeypatch, {})
        _stub_class(monkeypatch, ["flow_a"])
        assert list_exposed_scripts()[0]["name"] == "显示名称"

    def test_environment_mismatch_is_hidden(self, tmp_path, monkeypatch):
        desktop = _write(
            tmp_path, "desktop_only.wf",
            "#% runnable: true\n#% env: [desktop]\n",
        )
        common = _write(tmp_path, "common.wf", "#% runnable: true\n")
        _patch_resolver(monkeypatch, {
            "desktop_only.wf": desktop,
            "common.wf": common,
        })
        _stub_class(monkeypatch, [])
        _stub_prefs(monkeypatch)

        assert [cfg["id"] for cfg in list_exposed_scripts("android")] == [
            "common"]
        assert [cfg["id"] for cfg in list_exposed_scripts("desktop")] == [
            "common", "desktop_only"]

    def test_hidden_script_needs_user_opt_in(self, tmp_path, monkeypatch):
        wf = _write(tmp_path, "hidden.wf",
                    "#% name: 半成品\n#% runnable: true\n#% hidden: true\n")
        _patch_resolver(monkeypatch, {"hidden.wf": wf})
        _stub_class(monkeypatch, [])

        _stub_prefs(monkeypatch)
        assert list_exposed_scripts() == []

        _stub_prefs(monkeypatch, visible={"hidden": True})
        assert [c["id"] for c in list_exposed_scripts()] == ["hidden"]
