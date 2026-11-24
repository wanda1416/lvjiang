"""发布类型不能绕过正式 master 门禁，测试包不能破坏 APK 升级顺序。"""
import subprocess

import pytest
from scripts.ci import check_release


def source(root, version):
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    fields = {"src/lvjiang/_version.py": f'__version__ = "{version}"\n',
              "android/app/build.gradle.kts": f'versionName = "{version}"\nversionCode = 10\n',
              f"docs/50-releases/v{version}.md": f"# v{version} 测试说明\n"}
    for name, content in fields.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_release_notes_path_resolution(tmp_path):
    # 新稳定路径：docs/50-releases/v<tag>.md
    flat = tmp_path / "docs/50-releases/v1.2.3.md"
    flat.parent.mkdir(parents=True)
    flat.write_text("# v1.2.3\n", encoding="utf-8")
    assert check_release.release_notes_path(tmp_path, "1.2.3") == "docs/50-releases/v1.2.3.md"
    # 新系列子目录：docs/50-releases/<系列>/v<tag>.md
    (tmp_path / "docs/50-releases/v1.2").mkdir()
    (flat).unlink()
    series = tmp_path / "docs/50-releases/v1.2/v1.2.3.md"
    series.write_text("# v1.2.3\n", encoding="utf-8")
    assert check_release.release_notes_path(tmp_path, "1.2.3") == "docs/50-releases/v1.2/v1.2.3.md"
    # 旧位置兼容：docs/releases/[<系列>/]v<tag>.md
    (series).unlink()
    legacy = tmp_path / "docs/releases/v1.2/v1.2.3.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# v1.2.3\n", encoding="utf-8")
    assert check_release.release_notes_path(tmp_path, "1.2.3") == "docs/releases/v1.2/v1.2.3.md"
    # 缺失时报错
    legacy.unlink()
    with pytest.raises(ValueError, match="缺少发布说明"):
        check_release.release_notes_path(tmp_path, "1.2.3")


def test_release_notes_path_rejects_double_body(tmp_path):
    new = tmp_path / "docs/50-releases/v1.2.3.md"
    new.parent.mkdir(parents=True)
    new.write_text("# v1.2.3\n", encoding="utf-8")
    legacy = tmp_path / "docs/releases/v1.2.3.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# v1.2.3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="双正文"):
        check_release.release_notes_path(tmp_path, "1.2.3")


def test_test_entry_and_final_master_gate(tmp_path, monkeypatch):
    calls = []
    def git(root, *args):
        calls.append(args)
        if args[0] == "merge-base":
            raise subprocess.CalledProcessError(1, "git merge-base")
        return "" if args[0] in {"fetch", "tag"} else "frozen-commit"
    monkeypatch.setattr(check_release, "git", git)
    source(tmp_path, "0.15.0rc1")
    assert check_release.validate_release(tmp_path, "0.15.0rc1", test_only=True)["prerelease"] == "true"
    assert not any(call[0] == "fetch" for call in calls)
    source(tmp_path, "0.15.0")
    with pytest.raises(ValueError, match="测试发布入口"):
        check_release.validate_release(tmp_path, "0.15.0", test_only=True)
    with pytest.raises(subprocess.CalledProcessError):
        check_release.validate_release(tmp_path, "0.15.0")


def test_android_code_must_exceed_test_tags_and_version_fields_match(tmp_path, monkeypatch):
    source(tmp_path, "0.15.0rc1")
    def git(root, *args):
        if args[0] == "tag":
            return "0.15.0b10\n0.13.14"
        if args[0] == "show":
            return "versionCode = 11" if "b10" in args[1] else "versionCode = 9"
        return "frozen-commit"
    monkeypatch.setattr(check_release, "git", git)
    with pytest.raises(ValueError, match="versionCode"):
        check_release.validate_release(tmp_path, "0.15.0rc1")
    (tmp_path / "src/lvjiang/_version.py").write_text('__version__ = "0.15.0"', encoding="utf-8")
    with pytest.raises(ValueError, match="完全一致"):
        check_release.validate_release(tmp_path, "0.15.0rc1")
