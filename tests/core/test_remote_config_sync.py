"""在线配置的 manifest 校验与落盘同步。

全部脱网：伪造 ``_fetch_bytes`` 而不碰真实 HTTP。三层合并的正确性是另一
件事，见 tests/core/test_config_remote_layer.py。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from lvjiang.core.config import remote, versioning


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _scene_bytes(version: int, marker: str = "x") -> bytes:
    return f"content_version: {version}\nkey: a\nname: {marker}\n".encode("utf-8")


def _entry(rel_path: str, payload: bytes, version: int, **kw) -> dict:
    return {
        "rel_path": rel_path,
        "url": f"https://wanda1416.github.io/lvjiang/config/remote/{rel_path}",
        "sha256": _sha(payload),
        "content_version": version,
        **kw,
    }


def _manifest(files: list[dict], config_version: int = 1) -> dict:
    return {
        "schema_version": 1,
        "config_version": config_version,
        "updated_at": "2026-08-27T00:00:00Z",
        "files": files,
    }


@pytest.fixture
def fake_net(monkeypatch):
    """url → payload 的假网络。返回可变 dict，测试里自行填。"""
    store: dict[str, bytes] = {}

    def fake_fetch(url, *, max_bytes, timeout, etag=""):
        if url not in store:
            raise remote.RemoteConfigError(f"请求失败 HTTP 404: {url}")
        return store[url], "etag-1", False

    monkeypatch.setattr(remote, "_fetch_bytes", fake_fetch)
    return store


# ─── manifest 校验 ───────────────────────────────────────

class TestParseManifest:
    def test_rejects_wrong_schema_version(self):
        with pytest.raises(remote.RemoteConfigError, match="协议版本"):
            remote.parse_manifest({"schema_version": 99, "config_version": 1,
                                   "files": []})

    def test_rejects_non_https_url(self):
        payload = _scene_bytes(2)
        entry = _entry("scenes/a.yaml", payload, 2)
        entry["url"] = "http://example.com/a.yaml"
        with pytest.raises(remote.RemoteConfigError, match="HTTPS"):
            remote.parse_manifest(_manifest([entry]))

    def test_rejects_bad_sha256(self):
        entry = _entry("scenes/a.yaml", _scene_bytes(2), 2)
        entry["sha256"] = "not-a-hash"
        with pytest.raises(remote.RemoteConfigError, match="sha256"):
            remote.parse_manifest(_manifest([entry]))

    def test_rejects_duplicate_rel_path(self):
        payload = _scene_bytes(2)
        files = [_entry("scenes/a.yaml", payload, 2),
                 _entry("scenes/a.yaml", payload, 3)]
        with pytest.raises(remote.RemoteConfigError, match="重复"):
            remote.parse_manifest(_manifest(files))

    def test_parses_valid_manifest(self):
        payload = _scene_bytes(2)
        manifest = remote.parse_manifest(_manifest([_entry("scenes/a.yaml", payload, 2)]))
        assert manifest.config_version == 1
        assert manifest.entries[0].rel_path == "scenes/a.yaml"


class TestPathSafety:
    """manifest 是远端内容，绝不能让它决定往哪写盘。"""

    @pytest.mark.parametrize("rel_path", [
        "../../../etc/passwd",
        "/etc/passwd",
        "scenes/../../escape.yaml",
        "C:/windows/system32/x.yaml",
        "scenes\\..\\..\\escape.yaml",
        "",
    ])
    def test_rejects_escaping_paths(self, rel_path):
        assert not remote.is_safe_rel_path(rel_path)

    def test_rejects_unregistered_dir(self):
        """没参与在线下发的目录，远端往那儿放文件没有正当理由。"""
        assert not remote.is_safe_rel_path("workflows/evil.wf")
        assert not remote.is_safe_rel_path("app.yaml")

    def test_accepts_registered_paths(self):
        assert remote.is_safe_rel_path("scenes/a.yaml")
        assert remote.is_safe_rel_path("layouts/默认布局/a.json")

    def test_unsafe_entry_filtered_out_of_applicable(self):
        payload = _scene_bytes(2)
        manifest = remote.parse_manifest(_manifest([
            _entry("scenes/a.yaml", payload, 2),
            _entry("workflows/evil.wf", payload, 2),
        ]))
        applicable = remote.applicable_entries(manifest, app_version="0.7.1")
        assert [e.rel_path for e in applicable] == ["scenes/a.yaml"]


# ─── 客户端版本区间 ──────────────────────────────────────

class TestAppVersionGate:
    """远端配置与本地代码对不上的唯一防线。"""

    def test_below_min_version_skipped(self):
        payload = _scene_bytes(2)
        manifest = remote.parse_manifest(_manifest([
            _entry("scenes/a.yaml", payload, 2, min_app_version="0.9.0")]))
        assert remote.applicable_entries(manifest, app_version="0.7.1") == ()

    def test_at_or_above_max_exclusive_skipped(self):
        payload = _scene_bytes(2)
        manifest = remote.parse_manifest(_manifest([
            _entry("scenes/a.yaml", payload, 2,
                   max_app_version_exclusive="0.7.0")]))
        assert remote.applicable_entries(manifest, app_version="0.7.1") == ()

    def test_inside_range_applies(self):
        payload = _scene_bytes(2)
        manifest = remote.parse_manifest(_manifest([
            _entry("scenes/a.yaml", payload, 2, min_app_version="0.7.0",
                   max_app_version_exclusive="0.8.0")]))
        assert len(remote.applicable_entries(manifest, app_version="0.7.1")) == 1


# ─── 落盘同步 ────────────────────────────────────────────

class TestSyncToDir:
    def _run(self, tmp_path, fake_net, files, config_version=1,
             app_version="0.7.1"):
        manifest = remote.parse_manifest(_manifest(files, config_version))
        return remote.sync_to_dir(manifest, tmp_path / "remote",
                                  app_version=app_version)

    def test_downloads_and_writes(self, tmp_path, fake_net):
        payload = _scene_bytes(2, "远端")
        entry = _entry("scenes/a.yaml", payload, 2)
        fake_net[entry["url"]] = payload
        result = self._run(tmp_path, fake_net, [entry])
        assert result.updated == ("scenes/a.yaml",)
        assert (tmp_path / "remote" / "scenes" / "a.yaml").read_bytes() == payload

    def test_sha256_mismatch_rejected(self, tmp_path, fake_net):
        payload = _scene_bytes(2)
        entry = _entry("scenes/a.yaml", payload, 2)
        fake_net[entry["url"]] = _scene_bytes(2, "被掉包的内容")
        result = self._run(tmp_path, fake_net, [entry])
        assert result.updated == ()
        assert result.skipped == ("scenes/a.yaml",)
        assert not (tmp_path / "remote" / "scenes" / "a.yaml").exists()

    def test_content_version_mismatch_rejected(self, tmp_path, fake_net):
        """清单说 v2 但文件里写着 v9——作者发布时漏了一步，拒绝更干脆。"""
        payload = _scene_bytes(9)
        entry = _entry("scenes/a.yaml", payload, 2)
        fake_net[entry["url"]] = payload
        result = self._run(tmp_path, fake_net, [entry])
        assert result.skipped == ("scenes/a.yaml",)

    def test_one_failure_does_not_block_others(self, tmp_path, fake_net):
        """一份配置拉不下来，不该让另外几十份也停在旧版本。"""
        good = _scene_bytes(2, "好的")
        good_entry = _entry("scenes/a.yaml", good, 2)
        fake_net[good_entry["url"]] = good
        bad_entry = _entry("scenes/b.yaml", _scene_bytes(2), 2)  # 不放进 fake_net → 404
        result = self._run(tmp_path, fake_net, [good_entry, bad_entry])
        assert result.updated == ("scenes/a.yaml",)
        assert result.skipped == ("scenes/b.yaml",)

    def test_skips_download_when_already_at_version(self, tmp_path, fake_net):
        payload = _scene_bytes(2)
        entry = _entry("scenes/a.yaml", payload, 2)
        target = tmp_path / "remote" / "scenes" / "a.yaml"
        target.parent.mkdir(parents=True)
        target.write_bytes(payload)
        # 不放进 fake_net：真去下载就会 404 失败，以此证明没重复下载
        result = self._run(tmp_path, fake_net, [entry])
        assert result.updated == ()
        assert result.skipped == ()

    def test_manifest_is_authoritative_removal(self, tmp_path, fake_net):
        """撤回机制：manifest 全量声明，没列的一律删。"""
        stale = tmp_path / "remote" / "scenes" / "撤回的.yaml"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(_scene_bytes(1))
        payload = _scene_bytes(2)
        entry = _entry("scenes/a.yaml", payload, 2)
        fake_net[entry["url"]] = payload
        result = self._run(tmp_path, fake_net, [entry])
        assert result.removed == ("scenes/撤回的.yaml",)
        assert not stale.exists()

    def test_version_gated_out_entry_is_removed_locally(self, tmp_path, fake_net):
        """降级 App 后，之前拉下来的不适用配置要清掉，不能继续生效。"""
        stale = tmp_path / "remote" / "scenes" / "a.yaml"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(_scene_bytes(2))
        entry = _entry("scenes/a.yaml", _scene_bytes(2), 2, min_app_version="9.0.0")
        result = self._run(tmp_path, fake_net, [entry], app_version="0.7.1")
        assert result.removed == ("scenes/a.yaml",)
        assert not stale.exists()

    def test_empty_dirs_pruned(self, tmp_path, fake_net):
        stale = tmp_path / "remote" / "layouts" / "默认布局" / "a.json"
        stale.parent.mkdir(parents=True)
        stale.write_text(json.dumps({"content_version": 1}), encoding="utf-8")
        self._run(tmp_path, fake_net, [])
        assert not (tmp_path / "remote" / "layouts").exists()

    def test_no_part_files_left_behind(self, tmp_path, fake_net):
        payload = _scene_bytes(2)
        entry = _entry("scenes/a.yaml", payload, 2)
        fake_net[entry["url"]] = payload
        self._run(tmp_path, fake_net, [entry])
        assert list((tmp_path / "remote").rglob("*.part")) == []


# ─── 与 resolver 端到端 ──────────────────────────────────

class TestEndToEndWithResolver:
    def test_synced_file_takes_effect_only_when_newer(self, tmp_path, fake_net):
        from lvjiang.core.config.resolver import ConfigResolver

        system = tmp_path / "system"
        (system / "scenes").mkdir(parents=True)
        (system / "scenes" / "a.yaml").write_bytes(_scene_bytes(1, "出厂"))

        payload = _scene_bytes(2, "远端")
        entry = _entry("scenes/a.yaml", payload, 2)
        fake_net[entry["url"]] = payload
        manifest = remote.parse_manifest(_manifest([entry]))
        remote.sync_to_dir(manifest, tmp_path / "remote", app_version="0.7.1")

        r = ConfigResolver(system_dir=system, local_dir=tmp_path / "local",
                           remote_dir=tmp_path / "remote", dev_mode=False)
        got = r.resolve_read("scenes/a.yaml").read_text(encoding="utf-8")
        assert "远端" in got

        # 出厂随新版本推到 v5：远端 v2 不得盖回来
        (system / "scenes" / "a.yaml").write_bytes(_scene_bytes(5, "出厂新版"))
        got = r.resolve_read("scenes/a.yaml").read_text(encoding="utf-8")
        assert "出厂新版" in got


class TestStateTransition:
    """状态迁移必须由主线程做——worker 里写 SessionStore 会从非主线程弹
    原生模态框（见 core/telemetry/reporter.py 的约束 2）。这里验证的是
    「run_sync 不碰 SessionStore」和「关闭时不清状态」两条。
    """

    def test_run_sync_never_touches_session(self, tmp_path, fake_net, monkeypatch):
        def boom(*_a, **_kw):
            raise AssertionError("run_sync 不该写 SessionStore")

        monkeypatch.setattr(remote, "_update_state", boom)
        payload = _scene_bytes(2)
        entry = _entry("scenes/a.yaml", payload, 2)
        fake_net[entry["url"]] = payload
        monkeypatch.setattr(remote, "fetch_manifest",
                            lambda **_kw: (remote.parse_manifest(_manifest([entry])), "e"))
        result = remote.run_sync(remote.SyncJob(app_version="0.7.1"),
                                 remote_dir=tmp_path / "remote")
        assert result.updated == ("scenes/a.yaml",)

    def test_disabled_job_writes_nothing(self, monkeypatch):
        """关掉在线配置后 apply_outcome 一个字段都不该写，否则会把已有的
        etag / config_version 清零，重新打开要白拉一整轮。"""
        written: list[dict] = []
        monkeypatch.setattr(remote, "_update_state", written.append)
        result = remote.run_sync(remote.SyncJob(enabled=False))
        assert result.performed is False
        remote.apply_outcome(result)
        assert written == []

    def test_not_modified_keeps_etag_and_version(self, monkeypatch):
        written: list[dict] = []
        monkeypatch.setattr(remote, "_update_state", written.append)
        remote.apply_outcome(remote.SyncResult(not_modified=True, etag="old"))
        assert written and "etag" not in written[0]
        assert "config_version" not in written[0]

    def test_successful_sync_records_etag_and_version(self, monkeypatch):
        written: list[dict] = []
        monkeypatch.setattr(remote, "_update_state", written.append)
        remote.apply_outcome(remote.SyncResult(config_version=7, etag="e7"))
        assert written[0]["etag"] == "e7"
        assert written[0]["config_version"] == 7


def test_versioned_dirs_registered_for_core():
    """core 自己的两类必须在注册表里，否则整个下发机制形同虚设。"""
    assert "scenes" in versioning.VERSIONED_DIRS
    assert "layouts" in versioning.VERSIONED_DIRS


# ─── Review 回归：以下每条对应一个真实缺陷 ─────────────────

class TestNoDowngrade:
    """清单声称的版本比本地旧 = 拿到了过期清单，不能照做把配置降级。"""

    def _sync(self, tmp_path, files, config_version=1, app_version="0.7.1"):
        manifest = remote.parse_manifest(_manifest(files, config_version))
        return remote.sync_to_dir(manifest, tmp_path / "remote",
                                  app_version=app_version)

    def test_older_entry_does_not_overwrite_newer_local(self, tmp_path, fake_net):
        local = tmp_path / "remote" / "scenes" / "a.yaml"
        local.parent.mkdir(parents=True)
        local.write_bytes(_scene_bytes(5, "本地较新"))
        entry = _entry("scenes/a.yaml", _scene_bytes(3), 3)
        # 不放进 fake_net：真去下载就会 404，以此证明压根没尝试下载
        result = self._sync(tmp_path, [entry])
        assert result.updated == ()
        assert versioning.read_version(local) == 5

    def test_manifest_version_rollback_ignored(self, monkeypatch, tmp_path):
        """整份清单倒退（CDN 缓存/回滚/重放）时整轮不采信。"""
        payload = _scene_bytes(2)
        entry = _entry("scenes/a.yaml", payload, 2)
        monkeypatch.setattr(
            remote, "fetch_manifest",
            lambda **_kw: (remote.parse_manifest(_manifest([entry], 3)), "e"))
        job = remote.SyncJob(app_version="0.7.1", config_version=9)
        result = remote.run_sync(job, remote_dir=tmp_path / "remote")
        assert result.performed is False
        assert result.config_version == 9   # 保持本地已同步的版本


class TestFailedDownloadRetries:
    """有文件没拿到就不记 etag，否则下次 304 会让它永远补不回来。"""

    def test_etag_withheld_when_something_skipped(self, monkeypatch, tmp_path,
                                                  fake_net):
        entry = _entry("scenes/a.yaml", _scene_bytes(2), 2)  # 不放进 fake_net → 404
        monkeypatch.setattr(
            remote, "fetch_manifest",
            lambda **_kw: (remote.parse_manifest(_manifest([entry])), "新etag"))
        result = remote.run_sync(remote.SyncJob(app_version="0.7.1"),
                                 remote_dir=tmp_path / "remote")
        assert result.skipped == ("scenes/a.yaml",)
        assert result.etag == ""

    def test_etag_kept_when_all_succeed(self, monkeypatch, tmp_path, fake_net):
        payload = _scene_bytes(2)
        entry = _entry("scenes/a.yaml", payload, 2)
        fake_net[entry["url"]] = payload
        monkeypatch.setattr(
            remote, "fetch_manifest",
            lambda **_kw: (remote.parse_manifest(_manifest([entry])), "新etag"))
        result = remote.run_sync(remote.SyncJob(app_version="0.7.1"),
                                 remote_dir=tmp_path / "remote")
        assert result.etag == "新etag"


class TestStagingTakesEffectNextLaunch:
    """本次会话下载的内容不得立刻生效。

    工作流每次启动都会重新 load_layout（ui/main/run_control.py），而场景
    注册表只在进程启动时加载一次——直接写生效层会让两者一新一旧配在一起。
    """

    def _dirs(self, tmp_path, monkeypatch):
        import lvjiang.core.config.resolver as R
        active, stage = tmp_path / "remote", tmp_path / "remote.staging"
        monkeypatch.setattr(R, "REMOTE_CONFIG_DIR", active)
        monkeypatch.setattr(R, "REMOTE_STAGE_DIR", stage)
        return active, stage

    def test_download_goes_to_stage_not_active(self, tmp_path, monkeypatch,
                                               fake_net):
        active, stage = self._dirs(tmp_path, monkeypatch)
        payload = _scene_bytes(2, "新配置")
        entry = _entry("scenes/a.yaml", payload, 2)
        fake_net[entry["url"]] = payload
        monkeypatch.setattr(
            remote, "fetch_manifest",
            lambda **_kw: (remote.parse_manifest(_manifest([entry])), "e"))
        remote.run_sync(remote.SyncJob(app_version="0.7.1"))
        assert (stage / "scenes" / "a.yaml").exists()
        assert not (active / "scenes" / "a.yaml").exists()   # 本次会话读不到

    def test_promote_applies_it_next_launch(self, tmp_path, monkeypatch):
        active, stage = self._dirs(tmp_path, monkeypatch)
        (stage / "scenes").mkdir(parents=True)
        (stage / "scenes" / "a.yaml").write_bytes(_scene_bytes(2, "新配置"))
        assert remote.promote_pending() is True
        assert "新配置" in (active / "scenes" / "a.yaml").read_text(encoding="utf-8")
        assert not stage.exists()

    def test_promote_is_noop_without_stage(self, tmp_path, monkeypatch):
        self._dirs(tmp_path, monkeypatch)
        assert remote.promote_pending() is False

    def test_stage_starts_from_active_so_unchanged_files_survive(
            self, tmp_path, monkeypatch):
        """暂存层要以生效层为基线，否则提升上去等于删掉所有没变的文件。"""
        active, stage = self._dirs(tmp_path, monkeypatch)
        (active / "scenes").mkdir(parents=True)
        (active / "scenes" / "旧的.yaml").write_bytes(_scene_bytes(1))
        remote.prepare_stage()
        assert (stage / "scenes" / "旧的.yaml").exists()

    def test_disabling_clears_delivered_config(self, tmp_path, monkeypatch):
        """关闭开关必须能退回出厂配置。

        只停止下载是不够的：已下发的配置会一直顶替出厂内容，用户遇到一份
        有问题的远端配置时就没有退路了。
        """
        active, stage = self._dirs(tmp_path, monkeypatch)
        for d in (active, stage):
            (d / "scenes").mkdir(parents=True)
            (d / "scenes" / "a.yaml").write_bytes(_scene_bytes(2))
        monkeypatch.setattr(remote, "is_enabled", lambda: False)
        assert remote.promote_pending() is False
        assert not active.exists()
        assert not stage.exists()

    def test_enabled_promote_untouched_by_the_guard(self, tmp_path, monkeypatch):
        active, stage = self._dirs(tmp_path, monkeypatch)
        (stage / "scenes").mkdir(parents=True)
        (stage / "scenes" / "a.yaml").write_bytes(_scene_bytes(2))
        monkeypatch.setattr(remote, "is_enabled", lambda: True)
        assert remote.promote_pending() is True
        assert (active / "scenes" / "a.yaml").exists()
