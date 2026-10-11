"""发布类型不能绕过正式 master 门禁，测试包不能破坏 APK 升级顺序。"""
import subprocess

import pytest
from scripts import check_release


def source(root, version):
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    fields = {"src/lvjiang/_version.py": f'__version__ = "{version}"\n',
              "android/app/build.gradle.kts": f'versionName = "{version}"\nversionCode = 10\n',
              f"docs/50-releases/v{version}.md": f"# v{version} 测试说明\n"}
    for name, content in fields.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


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
