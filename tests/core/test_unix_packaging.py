"""Linux / macOS 打包路径隔离、只读 Bundle 同步与更新链接选择回归测试。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from lvjiang import constants
from lvjiang.core.updates.release import _select_download_url


def test_project_root_frozen_platform_isolation(monkeypatch, tmp_path: Path) -> None:
    """Windows frozen 恒用 exe 目录；macOS .app 与 Linux 只读目录切到用户数据目录。"""
    monkeypatch.delenv("LVJIANG_ROOT", raising=False)
    monkeypatch.setattr(constants.sys, "frozen", True, raising=False)

    # 1. Windows: 始终返回 exe 所在目录
    win_exe = tmp_path / "win" / "lvjiang.exe"
    win_exe.parent.mkdir(parents=True)
    monkeypatch.setattr(constants.sys, "platform", "win32")
    monkeypatch.setattr(constants.sys, "executable", str(win_exe))
    assert constants._project_root() == win_exe.parent

    # 2. macOS .app Bundle: 自动切到 ~/Library/Application Support/lvjiang
    mac_exe = tmp_path / "Applications" / "Lvjiang.app" / "Contents" / "MacOS" / "lvjiang"
    mac_exe.parent.mkdir(parents=True)
    fake_home = tmp_path / "home_mac"
    monkeypatch.setattr(constants.sys, "platform", "darwin")
    monkeypatch.setattr(constants.sys, "executable", str(mac_exe))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert constants._project_root() == fake_home / "Library" / "Application Support" / "lvjiang"

    # 3. Linux 可写解压目录（tar.gz 绿色包）: 留在 exe 所在目录
    linux_exe = tmp_path / "opt_user" / "lvjiang" / "lvjiang"
    linux_exe.parent.mkdir(parents=True)
    monkeypatch.setattr(constants.sys, "platform", "linux")
    monkeypatch.setattr(constants.sys, "executable", str(linux_exe))
    monkeypatch.setattr(constants.os, "access", lambda _p, _m: True)
    assert constants._project_root() == linux_exe.parent

    # 4. Linux 只读目录（AppImage / /opt）: 切到 $XDG_DATA_HOME/lvjiang
    xdg = tmp_path / "xdg_data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setattr(constants.os, "access", lambda _p, _m: False)
    assert constants._project_root() == xdg / "lvjiang"


def test_select_download_url_supports_macos_and_linux(monkeypatch) -> None:
    """更新器按平台与架构挑选对应发行包，不影响 Windows 与 Android 原有优先级。"""
    data = {
        "html_url": "https://github.com/wanda1416/lvjiang/releases/tag/0.15.22",
        "assets": [
            {"name": "lvjiang-v0.15.22-win64.zip", "browser_download_url": "https://dl/win64.zip"},
            {"name": "lvjiang-v0.15.22-win64-setup.exe", "browser_download_url": "https://dl/setup.exe"},
            {"name": "lvjiang-v0.15.22.apk", "browser_download_url": "https://dl/app.apk"},
            {"name": "lvjiang-v0.15.22-macos-arm64.dmg", "browser_download_url": "https://dl/mac-arm64.dmg"},
            {"name": "lvjiang-v0.15.22-macos-x86_64.dmg", "browser_download_url": "https://dl/mac-x86.dmg"},
            {"name": "lvjiang-v0.15.22-linux-x86_64.tar.gz", "browser_download_url": "https://dl/linux-x86.tar.gz"},
        ],
    }

    assert _select_download_url(data, "win32") == "https://dl/setup.exe"
    assert _select_download_url(data, "android") == "https://dl/app.apk"

    monkeypatch.setattr("platform.machine", lambda: "arm64")
    assert _select_download_url(data, "darwin") == "https://dl/mac-arm64.dmg"

    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    assert _select_download_url(data, "darwin") == "https://dl/mac-x86.dmg"
    assert _select_download_url(data, "linux") == "https://dl/linux-x86.tar.gz"


def test_launcher_syncs_readonly_bundle_assets_and_preserves_user_data(tmp_path: Path) -> None:
    """只读 Bundle 同步到用户数据目录时更新出厂资产，绝不覆盖 config/local 与 config/session。"""
    spec = importlib.util.spec_from_file_location(
        "lvjiang_launcher_test_mod",
        Path(__file__).resolve().parents[2] / "packaging" / "launcher.py",
    )
    assert spec is not None and spec.loader is not None
    # 仅测试纯函数：用 AST 方式安全加载或直接导入辅助函数
    src = (Path(__file__).resolve().parents[2] / "packaging" / "launcher.py").read_text(encoding="utf-8")
    ns: dict = {}
    import ast
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef)):
            if isinstance(node, ast.FunctionDef) and node.name == "_sync_bundle_assets":
                exec(compile(ast.Module([node], []), "<launcher>", "exec"), ns)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                exec(compile(ast.Module([node], []), "<launcher>", "exec"), ns)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in ("_BUNDLE_SYNC_DIRS", "_BUNDLE_SYNC_FILES"):
                        exec(compile(ast.Module([node], []), "<launcher>", "exec"), ns)

    sync_fn = ns["_sync_bundle_assets"]
    bundle = tmp_path / "Lvjiang.app" / "Contents" / "MacOS"
    user_root = tmp_path / "user_data"
    (bundle / "config/system").mkdir(parents=True)
    (bundle / "config/system/app.yaml").write_text("v: 2\n", encoding="utf-8")
    (bundle / "config/i18n").mkdir(parents=True)
    (bundle / "config/i18n/zh_CN.yaml").write_text("ok: true\n", encoding="utf-8")
    (bundle / ".bundle_version").write_text("0.15.22\n", encoding="utf-8")

    # 预置用户已有数据（必须完好保留）
    (user_root / "config/local").mkdir(parents=True)
    (user_root / "config/local/custom.yaml").write_text("user: keep\n", encoding="utf-8")
    (user_root / "config/session").mkdir(parents=True)
    (user_root / "config/session/session.json").write_text('{"keep": true}', encoding="utf-8")

    sync_fn(bundle, user_root)

    assert (user_root / "config/system/app.yaml").read_text(encoding="utf-8") == "v: 2\n"
    assert (user_root / "config/i18n/zh_CN.yaml").read_text(encoding="utf-8") == "ok: true\n"
    assert (user_root / ".bundle_version").read_text(encoding="utf-8").strip() == "0.15.22"
    assert (user_root / "config/local/custom.yaml").read_text(encoding="utf-8") == "user: keep\n"
    assert (user_root / "config/session/session.json").read_text(encoding="utf-8") == '{"keep": true}'
