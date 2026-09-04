"""remote 层（在线下发）的三层解析与版本闸门。

这里全部脱网：只验证「三层合并的正确性」，manifest 拉取/校验是另一件事，
见 tests/core/test_remote_config_sync.py。两件事混在一起调试会很痛苦。

核心断言是 **remote 不是无条件优先**：它只在 content_version 严格新于
system 时才生效。用户升了 App、system 带来更新的坐标，而远程还停在给旧
版本热修的旧配置——无脑覆盖会把配置静默回退，这正是要防的事故。
"""
from __future__ import annotations

import json

import pytest
import yaml

from lvjiang.core.config import versioning
from lvjiang.core.config.resolver import ConfigResolver
from tests.case_matrix import case_matrix


@pytest.fixture
def dirs(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    for d in (system, local, remote):
        d.mkdir()
    return system, local, remote


def _resolver(dirs, dev_mode=False) -> ConfigResolver:
    return ConfigResolver(system_dir=dirs[0], local_dir=dirs[1],
                          remote_dir=dirs[2], dev_mode=dev_mode)


def _write_scene(root, name: str, version: int | None, marker: str = "x"):
    """scenes/*.yaml 是注册过的版本化实体（core 自己声明的）"""
    path = root / "scenes" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"key: {path.stem}\nname: {marker}\n"
    if version is not None:
        body = f"content_version: {version}\n" + body
    path.write_text(body, encoding="utf-8")
    return path


def _read_marker(path) -> str:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["name"]


# ─── 版本闸门 ────────────────────────────────────────────

class TestRemoteVersionGate:
    def test_newer_remote_supersedes_system(self, dirs):
        _write_scene(dirs[0], "a.yaml", 1, "系统")
        _write_scene(dirs[2], "a.yaml", 2, "远程")
        r = _resolver(dirs)
        assert _read_marker(r.resolve_read("scenes/a.yaml")) == "远程"

    def test_older_remote_does_not_regress_system(self, dirs):
        """本设计的核心回归点：升级后 system 更新，远程旧配置不得盖回来。"""
        _write_scene(dirs[0], "a.yaml", 5, "系统新版")
        _write_scene(dirs[2], "a.yaml", 3, "远程旧版")
        r = _resolver(dirs)
        assert _read_marker(r.resolve_read("scenes/a.yaml")) == "系统新版"

    def test_equal_version_keeps_system(self, dirs):
        """同版本视为同内容，不换——换了也没意义，只会多一次读盘。"""
        _write_scene(dirs[0], "a.yaml", 4, "系统")
        _write_scene(dirs[2], "a.yaml", 4, "远程")
        r = _resolver(dirs)
        assert _read_marker(r.resolve_read("scenes/a.yaml")) == "系统"

    def test_system_without_version_refuses_remote(self, dirs):
        """fail-safe：漏给某个文件加字段的后果是「收不到在线更新」，
        而不是「被远程悄悄接管」。前者能被发现，后者不能。"""
        _write_scene(dirs[0], "a.yaml", None, "系统无版本号")
        _write_scene(dirs[2], "a.yaml", 99, "远程")
        r = _resolver(dirs)
        assert _read_marker(r.resolve_read("scenes/a.yaml")) == "系统无版本号"

    def test_remote_without_version_ignored(self, dirs):
        _write_scene(dirs[0], "a.yaml", 1, "系统")
        _write_scene(dirs[2], "a.yaml", None, "远程无版本号")
        r = _resolver(dirs)
        assert _read_marker(r.resolve_read("scenes/a.yaml")) == "系统"

    def test_unregistered_path_never_uses_remote(self, dirs):
        """没在 versioning 注册的路径（如 workflows/*.wf）不参与在线下发。"""
        (dirs[0] / "workflows").mkdir()
        (dirs[0] / "workflows" / "a.wf").write_text("system", encoding="utf-8")
        (dirs[2] / "workflows").mkdir()
        (dirs[2] / "workflows" / "a.wf").write_text("remote", encoding="utf-8")
        r = _resolver(dirs)
        assert r.resolve_read("workflows/a.wf").read_text(encoding="utf-8") == "system"


# ─── local 恒为最高优先级 ─────────────────────────────────

class TestLocalStillWins:
    def test_local_beats_newer_remote(self, dirs):
        """用户自己改过的东西，任何在线下发都不该盖掉。"""
        _write_scene(dirs[0], "a.yaml", 1, "系统")
        _write_scene(dirs[2], "a.yaml", 99, "远程")
        _write_scene(dirs[1], "a.yaml", 1, "用户改的")
        r = _resolver(dirs)
        assert _read_marker(r.resolve_read("scenes/a.yaml")) == "用户改的"

    def test_tombstone_still_hides_everything(self, dirs):
        _write_scene(dirs[0], "a.yaml", 1)
        _write_scene(dirs[2], "a.yaml", 99)
        (dirs[1] / "scenes").mkdir(parents=True, exist_ok=True)
        (dirs[1] / "scenes" / "a.yaml.deleted").touch()
        assert _resolver(dirs).resolve_read("scenes/a.yaml") is None


# ─── 远程新增文件 ────────────────────────────────────────

class TestRemoteNewFiles:
    def test_rejected_where_not_allowed(self, dirs):
        """scenes/ 不允许远程新增：新场景要在 scenes.yaml 登记才有意义，
        而注册表走发版；远程凭空多的场景文件是死的。"""
        _write_scene(dirs[2], "新场景.yaml", 1, "远程新增")
        r = _resolver(dirs)
        assert r.resolve_read("scenes/新场景.yaml") is None
        assert "新场景.yaml" not in r.enumerate_entities("scenes", "*.yaml")

    def test_allowed_where_declared(self, dirs, monkeypatch):
        """allow_remote_new=True 的目录（燕云 yysls/tuning_rules）可以
        接收全新文件——远程下发一条新调律规则能直接生效。"""
        monkeypatch.setitem(
            versioning.VERSIONED_DIRS, "demo_rules",
            versioning.VersionedDir("demo_rules", "*.yaml", 1, allow_remote_new=True))
        path = dirs[2] / "demo_rules" / "new.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content_version: 1\nkey: new\n", encoding="utf-8")
        r = _resolver(dirs)
        assert r.resolve_read("demo_rules/new.yaml") == path
        assert r.enumerate_entities("demo_rules", "*.yaml") == ["new.yaml"]


# ─── enumerate 与删除保护 ─────────────────────────────────

class TestEnumerateAndProtection:
    def test_enumerate_omits_remote_that_loses_gate(self, dirs):
        """列表里冒出一个读不到的条目，比少一个条目更难查。"""
        _write_scene(dirs[0], "a.yaml", 5)
        _write_scene(dirs[2], "a.yaml", 1)   # 版本更旧，闸门不过
        _write_scene(dirs[2], "b.yaml", 1)   # system 没有，scenes 不许新增
        r = _resolver(dirs)
        assert r.enumerate_entities("scenes", "*.yaml") == ["a.yaml"]

    def test_remote_entity_counts_as_factory_content(self, dirs, monkeypatch):
        """远程下发的实体也是作者内容，用户模式下不可删。"""
        monkeypatch.setitem(
            versioning.VERSIONED_DIRS, "demo_rules",
            versioning.VersionedDir("demo_rules", "*.yaml", 1, allow_remote_new=True))
        path = dirs[2] / "demo_rules" / "new.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content_version: 1\n", encoding="utf-8")
        assert _resolver(dirs).is_system_entity("demo_rules/new.yaml")


# ─── 开发模式普通保存保留版本 / 显式提升 ───────────────────

class TestWritePreservesVersion:
    def test_content_change_keeps_version(self, dirs):
        _write_scene(dirs[0], "a.yaml", 3, "旧")
        r = _resolver(dirs, dev_mode=True)
        r.write_entity("scenes/a.yaml", "key: a\nname: 新\n")
        assert versioning.read_version(dirs[0] / "scenes" / "a.yaml") == 3

    def test_unchanged_content_keeps_version(self, dirs):
        """打开编辑器又原样关掉不该推高版本，否则远程侧分不清真改动。"""
        _write_scene(dirs[0], "a.yaml", 3, "同样的内容")
        r = _resolver(dirs, dev_mode=True)
        r.write_entity("scenes/a.yaml", "key: a\nname: 同样的内容\n")
        assert versioning.read_version(dirs[0] / "scenes" / "a.yaml") == 3

    def test_new_file_starts_at_one(self, dirs):
        r = _resolver(dirs, dev_mode=True)
        r.write_entity("scenes/新的.yaml", "key: 新的\n")
        assert versioning.read_version(dirs[0] / "scenes" / "新的.yaml") == 1

    def test_user_mode_does_not_touch_version(self, dirs):
        """local 影子恒为最高优先级，不参与 system/remote 的版本比较。"""
        _write_scene(dirs[0], "a.yaml", 3, "系统")
        r = _resolver(dirs, dev_mode=False)
        r.write_entity("scenes/a.yaml", "key: a\nname: 用户改的\n")
        assert versioning.read_version(dirs[1] / "scenes" / "a.yaml") is None

    def test_json_entity_keeps_version_and_format(self, dirs):
        """layouts/{布局}/{场景}.json 是 depth=2 的版本化实体。"""
        path = dirs[0] / "layouts" / "默认布局" / "s.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"content_version": 2, "regions": [{"key": "a"}]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        r = _resolver(dirs, dev_mode=True)
        r.write_entity("layouts/默认布局/s.json",
                       json.dumps({"regions": [{"key": "b"}]},
                                  ensure_ascii=False, indent=2))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["content_version"] == 2
        assert data["regions"] == [{"key": "b"}]

    def test_explicit_version_is_written(self, dirs):
        _write_scene(dirs[0], "a.yaml", 3, "旧")
        r = _resolver(dirs, dev_mode=True)
        r.write_entity(
            "scenes/a.yaml", "key: a\nname: 新\n", content_version=4)
        assert versioning.read_version(dirs[0] / "scenes" / "a.yaml") == 4

    @case_matrix("bad_version", [True, 0, -1, 2.5, "4"])
    def test_explicit_version_requires_positive_integer(
            self, dirs, bad_version):
        _write_scene(dirs[0], "a.yaml", 3, "旧")
        r = _resolver(dirs, dev_mode=True)
        with pytest.raises(ValueError, match="大于 0 的整数"):
            r.write_entity(
                "scenes/a.yaml", "key: a\nname: 新\n",
                content_version=bad_version,
            )

    def test_non_versioned_path_untouched(self, dirs):
        r = _resolver(dirs, dev_mode=True)
        r.write_entity("workflows/a.wf", "loop 3\n")
        assert (dirs[0] / "workflows" / "a.wf").read_text(
            encoding="utf-8") == "loop 3\n"


class TestDevModeSeesRemote:
    """开发模式**照常**参与在线下发。

    禁用是错的：开发者本地跑出来的行为若和用户不一样，用户报"识别坏了"
    时根本复现不出来——而这恰恰是在线下发最需要被排查的一类问题。
    """

    def test_dev_mode_reads_remote_like_users_do(self, dirs):
        _write_scene(dirs[0], "a.yaml", 2, "系统")
        _write_scene(dirs[2], "a.yaml", 3, "远程")
        r = _resolver(dirs, dev_mode=True)
        assert _read_marker(r.resolve_read("scenes/a.yaml")) == "远程"

    def test_supersede_is_logged_once_per_version(self, dirs):
        """静默生效 = 开发者意识不到，所以顶替时必须留痕；但 resolve_read
        是热路径、enumerate 还会逐个调，不去重会刷屏。

        用 loguru 自己的 sink 收——它不走 stdlib logging，caplog 抓不到。
        """
        import io

        from loguru import logger

        _write_scene(dirs[0], "a.yaml", 2, "系统")
        _write_scene(dirs[2], "a.yaml", 3, "远程")
        r = _resolver(dirs)
        buf = io.StringIO()
        sink_id = logger.add(buf, level="INFO")
        try:
            for _ in range(5):
                r.resolve_read("scenes/a.yaml")
            r.enumerate_entities("scenes", "*.yaml")
        finally:
            logger.remove(sink_id)
        output = buf.getvalue()
        assert output.count("[在线配置] 生效") == 1
        assert "系统 v2" in output and "远程 v3" in output

    def test_edit_over_remote_beats_published_version(self, dirs):
        """开发者基于远程 v3 的内容改一版，不能写出同为 v3 的另一份内容
        ——版本号必须唯一标识内容，否则仓库和线上的 v3 是两个东西。"""
        _write_scene(dirs[0], "a.yaml", 2, "系统")
        _write_scene(dirs[2], "a.yaml", 3, "远程")
        r = _resolver(dirs, dev_mode=True)
        target = r.next_entity_version("scenes/a.yaml")
        r.write_entity(
            "scenes/a.yaml", "key: a\nname: 我基于远程改的\n",
            content_version=target)
        assert versioning.read_version(dirs[0] / "scenes" / "a.yaml") == 4
        # 改动立刻生效，不用等"版本号追上线上"
        assert _read_marker(r.resolve_read("scenes/a.yaml")) == "我基于远程改的"

    def test_unchanged_content_still_lifts_over_remote(self, dirs):
        """内容没变但被更新的远程压着时也要抬版本号——否则系统那份永远
        显示不出来，开发者会以为自己的文件没生效。"""
        _write_scene(dirs[0], "a.yaml", 2, "同样的内容")
        _write_scene(dirs[2], "a.yaml", 5, "远程")
        r = _resolver(dirs, dev_mode=True)
        target = r.next_entity_version("scenes/a.yaml")
        r.write_entity(
            "scenes/a.yaml", "key: a\nname: 同样的内容\n",
            content_version=target)
        assert versioning.read_version(dirs[0] / "scenes" / "a.yaml") == 6

class TestDescribeEntity:
    """编辑器把「来源 / 版本号」标给人看，靠的是这个 API。

    日志只在第一次顶替时留一条，没人会盯着日志编辑配置；界面上随时可见
    才是真正能让人"意识到自己在看哪一份"的办法。
    """

    def test_reports_system(self, dirs):
        _write_scene(dirs[0], "a.yaml", 2)
        origin = _resolver(dirs).describe_entity("scenes/a.yaml")
        assert (origin.layer, origin.version) == ("system", 2)

    def test_reports_remote_when_superseding(self, dirs):
        _write_scene(dirs[0], "a.yaml", 2)
        _write_scene(dirs[2], "a.yaml", 7)
        origin = _resolver(dirs).describe_entity("scenes/a.yaml")
        assert (origin.layer, origin.version) == ("remote", 7)

    def test_reports_system_when_remote_loses_gate(self, dirs):
        _write_scene(dirs[0], "a.yaml", 9)
        _write_scene(dirs[2], "a.yaml", 3)
        origin = _resolver(dirs).describe_entity("scenes/a.yaml")
        assert (origin.layer, origin.version) == ("system", 9)

    def test_reports_local(self, dirs):
        _write_scene(dirs[0], "a.yaml", 2)
        _write_scene(dirs[2], "a.yaml", 7)
        _write_scene(dirs[1], "a.yaml", 2)
        assert _resolver(dirs).describe_entity("scenes/a.yaml").layer == "local"

    def test_missing_entity_has_empty_layer(self, dirs):
        origin = _resolver(dirs).describe_entity("scenes/没有的.yaml")
        assert (origin.layer, origin.version) == ("", None)

    def test_dev_mode_reports_remote_too(self, dirs):
        """开发者必须看得到远程顶替，否则复现不出用户的问题。"""
        _write_scene(dirs[0], "a.yaml", 2)
        _write_scene(dirs[2], "a.yaml", 7)
        origin = _resolver(dirs, dev_mode=True).describe_entity("scenes/a.yaml")
        assert origin.layer == "remote"

    def test_lists_every_existing_layer_in_priority_order(self, dirs):
        _write_scene(dirs[0], "a.yaml", 9)
        _write_scene(dirs[2], "a.yaml", 3)
        _write_scene(dirs[1], "a.yaml", 1)
        origins = _resolver(dirs).list_entity_origins("scenes/a.yaml")
        assert [(item.layer, item.version) for item in origins] == [
            ("local", 1), ("remote", 3), ("system", 9),
        ]

    def test_lists_no_origins_for_missing_entity(self, dirs):
        assert _resolver(dirs).list_entity_origins("scenes/missing.yaml") == ()
