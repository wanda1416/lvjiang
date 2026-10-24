"""官方预置的持续契约：可复核、无个人配置、升级只改 system、失败回滚。"""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from lvjiang.core.system_preset import build_system_preset, install_system_preset


@pytest.fixture
def official(tmp_path):
    system = tmp_path / "official/system"
    system.mkdir(parents=True)
    for name in ("app.yaml", "ocr.yaml", "layouts.yaml"):
        (system / name).write_text("{}", encoding="utf-8")
    (system / "workflows").mkdir()
    (system / "workflows/task.wf").write_text("wait 0.01", encoding="utf-8")
    (system.parent / "local").mkdir()
    (system.parent / "local/private.yaml").write_text("not bundled", encoding="utf-8")
    return system


def test_package_is_deterministic_and_only_contains_official_configuration(official, tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    build_system_preset(official, first)
    build_system_preset(official, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as package:
        assert set(package.namelist()) == {"system/app.yaml", "system/ocr.yaml", "system/layouts.yaml", "system/workflows/task.wf", "manifest.json"}


def test_include_excludes_untracked_drafts_and_rejects_missing_files(official, tmp_path):
    """构建只打包 git 跟踪的官方文件；声明了却缺失的文件视为构建错误。"""
    (official / "workflows/_draft.wf").write_text("wait 0.02", encoding="utf-8")
    included = ["app.yaml", "ocr.yaml", "layouts.yaml", "workflows/task.wf"]
    archive = tmp_path / "preset.zip"
    manifest = build_system_preset(official, archive, include=included)
    assert sorted(manifest["files"]) == sorted(included)
    with zipfile.ZipFile(archive) as package:
        assert "system/workflows/_draft.wf" not in package.namelist()
    with pytest.raises(ValueError, match="官方预置缺少文件"):
        build_system_preset(official, tmp_path / "bad.zip",
                            include=["app.yaml", "workflows/missing.wf"])


def _official_tree(root: Path, newline: str) -> Path:
    system = root / "system"
    (system / "workflows").mkdir(parents=True)
    for name in ("app.yaml", "ocr.yaml", "layouts.yaml"):
        (system / name).write_text("{}" + newline, encoding="utf-8", newline="")
    (system / "workflows/task.wf").write_text("wait 0.01" + newline, encoding="utf-8", newline="")
    return system


def test_crlf_workspace_yields_same_preset_id(tmp_path):
    """工作区被外部工具写成 CRLF 时摘要不变：行尾不属于提交内容。

    ``.gitattributes`` 的归一化让 CRLF 在 git 状态里隐身，按磁盘字节打包却会让
    摘要漂移，手机侧报「无法确认原始预置基底」。
    """
    lf = _official_tree(tmp_path / "lf", "\n")
    crlf = _official_tree(tmp_path / "crlf", "\r\n")
    lf_manifest = build_system_preset(lf, tmp_path / "lf.zip")
    crlf_manifest = build_system_preset(crlf, tmp_path / "crlf.zip")
    assert lf_manifest["preset_id"] == crlf_manifest["preset_id"]
    assert (tmp_path / "lf.zip").read_bytes() == (tmp_path / "crlf.zip").read_bytes()


def test_read_tree_only_skips_unlisted_entries_entirely(official):
    """include 之外的条目连名字校验和体积统计都不做，草稿不参与构建。"""
    from lvjiang.core.update_bundle import read_tree
    (official / "workflows/_draft.wf").write_text("wait 0.02", encoding="utf-8")
    files = read_tree(official, only={"app.yaml", "workflows/task.wf"})
    assert sorted(files) == ["app.yaml", "workflows/task.wf"]


def test_packaging_only_writes_identity_and_first_install_creates_r0(official, monkeypatch):
    from lvjiang.core.config import remote
    from lvjiang.core.config.update_store import UpdateStore
    from lvjiang.core.system_preset import write_baseline_identity
    from lvjiang.core.update_bundle import make_manifest, read_tree, unpack
    identity = write_baseline_identity(official, "0.15.0", config_dir=official.parent)
    store = UpdateStore(official.parent, "0.15.0", identity["sha256"])
    baseline = store.root / "r0.zip"
    assert not baseline.exists()
    monkeypatch.setattr(remote, "_installed_store", lambda: store)
    # 没有待恢复事务时启动不碰配置，也不提前生成 r0。
    remote.recover_installation()
    assert not baseline.exists()
    files = read_tree(official)
    manifest, archive = make_manifest("0.15.0", 1, files, {})
    store.install(manifest, archive, activate=lambda *args: None)
    assert unpack(baseline.read_bytes()) == files
    assert store._state()["revision"] == 1


def test_upgrade_preserves_parameters_database_and_pc_sync_until_new_preset(official, tmp_path, monkeypatch):
    archive = tmp_path / "preset.zip"
    build_system_preset(official, archive)
    phone = tmp_path / "phone"
    assert install_system_preset(archive, phone)
    session = phone / "config/session"
    (session / "session.json").write_text('{"wf_configs":{"task":{"count":5}}}', encoding="utf-8")
    (session / "profile.db").write_bytes(b"unchanged database")
    (phone / "config/local").mkdir()
    (phone / "config/local/override.yaml").write_text("local", encoding="utf-8")
    (phone / "config/system/workflows/task.wf").write_text("PC synced task", encoding="utf-8")
    assert not install_system_preset(archive, phone)
    assert (phone / "config/system/workflows/task.wf").read_text(encoding="utf-8") == "PC synced task"
    (official / "workflows/task.wf").unlink()
    (official / "workflows/new.wf").write_text("wait 0.02", encoding="utf-8")
    monkeypatch.setattr("lvjiang.core.system_preset.__version__", "9.1.0")
    build_system_preset(official, archive)
    assert install_system_preset(archive, phone)
    assert not (phone / "config/system/workflows/task.wf").exists()
    assert (phone / "config/system/workflows/new.wf").is_file()
    assert json.loads((session / "session.json").read_text(encoding="utf-8"))["wf_configs"]["task"]["count"] == 5
    assert (session / "profile.db").read_bytes() == b"unchanged database"
    assert (phone / "config/local/override.yaml").read_text(encoding="utf-8") == "local"


def test_failed_reload_restores_previous_system_and_install_marker(official, tmp_path):
    archive = tmp_path / "preset.zip"
    build_system_preset(official, archive)
    phone = tmp_path / "phone"
    install_system_preset(archive, phone)
    marker = (phone / "config/session/preset.json").read_bytes()
    (official / "workflows/task.wf").write_text("wait 2", encoding="utf-8")
    build_system_preset(official, archive)
    def fail():
        raise RuntimeError("reload failed")
    with pytest.raises(RuntimeError, match="reload failed"):
        install_system_preset(archive, phone, on_applied=fail)
    assert (phone / "config/system/workflows/task.wf").read_text(encoding="utf-8") == "wait 0.01"
    assert (phone / "config/session/preset.json").read_bytes() == marker


def test_build_host_does_not_require_authorization_or_application_runtime(official, tmp_path):
    # APK 构建宿主只有 packaging；不能因分发过滤而要求完整客户端依赖。
    source = Path(__file__).resolve().parents[2] / "src"
    script = """
import importlib.abc
import sys
from pathlib import Path
class BuildOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'loguru', 'yaml', 'cryptography', 'PyQt6'} or fullname == 'lvjiang.core.license' or fullname.startswith(('lvjiang.core.license.', 'lvjiang.core.config')):
            raise ImportError('Build host must not import runtime: ' + fullname)
sys.meta_path.insert(0, BuildOnly())
sys.path.insert(0, sys.argv[1])
from lvjiang.core.system_preset import build_system_preset, write_baseline_identity
build_system_preset(Path(sys.argv[2]), Path(sys.argv[3]))
write_baseline_identity(Path(sys.argv[2]), '0.15.0', config_dir=Path(sys.argv[2]).parent)
"""
    output = tmp_path / "host-preset.zip"
    subprocess.run([sys.executable, "-c", script, str(source), str(official), str(output)],
                   check=True, capture_output=True, text=True, encoding="utf-8")
    with zipfile.ZipFile(output) as package:
        assert "system/workflows/task.wf" in package.namelist()
