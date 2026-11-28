"""Linux 与 macOS 发行包构建脚本（PyInstaller onedir → tar.gz / .app + .dmg）。

Windows 仍使用 packaging/package.bat（Inno Setup），本脚本仅用于 Unix 平台，
避免在 Windows 打包链路上引入任何变更风险。

用法（在仓库根目录执行）：
    python packaging/package_unix.py [--adb-bin /path/to/adb] [--skip-codesign]
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"

#: 安装到当前用户应用菜单的桌面入口。Exec 与 Icon 需要解压后的绝对路径，
#: 因此由随包脚本在安装时生成，包里不放写死路径的 .desktop。
#: 文件名必须与 src/lvjiang/ui/widgets/app_icon.py 的 DESKTOP_FILE_NAME 一致。
_LINUX_DESKTOP_INSTALLER = """#!/bin/sh
# 把律匠注册到当前用户的应用菜单，并让任务栏 / Dock 使用随包图标。
# Wayland 下窗口图标只由桌面入口决定，因此需要运行一次本脚本。
set -eu

APP_DIR=$(cd "$(dirname "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
APPS_DIR=$DATA_HOME/applications
ICON_DIR=$DATA_HOME/icons/hicolor/256x256/apps

if [ ! -x "$APP_DIR/lvjiang" ]; then
    echo "未在 $APP_DIR 找到 lvjiang，请在解压后的目录中运行本脚本。" >&2
    exit 1
fi

mkdir -p "$APPS_DIR" "$ICON_DIR"
cp "$APP_DIR/_internal/lvjiang/assets/icon.png" "$ICON_DIR/lvjiang.png"

cat > "$APPS_DIR/lvjiang.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=律匠
Name[en_US]=Lvjiang
Comment=律匠桌面客户端
Comment[en_US]=Lvjiang desktop client
Exec="$APP_DIR/lvjiang"
Icon=lvjiang
Terminal=false
Categories=Utility;
StartupWMClass=lvjiang
EOF

chmod 644 "$APPS_DIR/lvjiang.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi
echo "已安装桌面入口：$APPS_DIR/lvjiang.desktop"
echo "若应用菜单没有立即刷新，请重新登录桌面会话。"
"""


def _read_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(data["project"]["version"]).strip()
    if not version:
        raise RuntimeError("无法从 pyproject.toml 读取版本号")
    return version


def _normalized_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "arm64" if sys.platform == "darwin" else "aarch64"
    return machine or "unknown"


def _run(cmd: list[str], *, cwd: Path = ROOT) -> None:
    print(f"[package_unix] $ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _populate_factory_assets(target_root: Path, version: str, adb_bin: Path | None) -> None:
    """将出厂只读配置、i18n、文档、许可证与平台 adb 放入打包目录。"""
    # 1. 与 Windows / Android 共用同一套 canonical system baseline
    _run([
        sys.executable,
        str(ROOT / "scripts/ci/build_system_baseline.py"),
        str(target_root / "config"),
    ])

    # 2. UI 翻译表（缺了会导致语言选择器退化为仅中文）
    _copy_tree(ROOT / "config/i18n", target_root / "config/i18n")

    # 3. 外部插件目录（公开构建无 plugins/ 时跳过）
    plugins_dir = ROOT / "plugins"
    if plugins_dir.is_dir():
        _copy_tree(plugins_dir, target_root / "plugins")

    # 4. 内置 data/adb（scrcpy-server.jar + 当前平台 adb 可执行文件）
    adb_dst_dir = target_root / "data/adb"
    adb_dst_dir.mkdir(parents=True, exist_ok=True)
    scrcpy_jar = ROOT / "data/adb/scrcpy-server.jar"
    if scrcpy_jar.is_file():
        shutil.copy2(scrcpy_jar, adb_dst_dir / "scrcpy-server.jar")

    resolved_adb = adb_bin
    if resolved_adb is None:
        found = shutil.which("adb")
        if found:
            resolved_adb = Path(found)
    if resolved_adb is not None and resolved_adb.is_file():
        dst_adb = adb_dst_dir / "adb"
        shutil.copy2(resolved_adb, dst_adb)
        mode = dst_adb.stat().st_mode
        dst_adb.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    else:
        print("[package_unix] 警告：未提供平台 adb，运行时将回退到系统 PATH / SDK 路径", flush=True)

    # 5. 文档包
    _run([
        sys.executable,
        str(ROOT / "scripts/ci/build_agent_bundle.py"),
        "--output",
        str(target_root / "docs"),
    ])

    # 6. 许可证随二进制分发
    shutil.copy2(ROOT / "LICENSE", target_root / "LICENSE.txt")

    # 7. 标记本次打包版本（供只读 Bundle 启动时增量同步只读资产到用户目录）
    (target_root / ".bundle_version").write_text(f"{version}\n", encoding="utf-8")


def _write_linux_readme(target_root: Path, version: str) -> None:
    text = (
        f"律匠 (lvjiang) v{version} — Linux 发行版\n"
        "========================================\n\n"
        "1. 启动方式：\n"
        "   - 图形界面：./lvjiang\n"
        "   - 命令行客户端：./lvjiang-cli --help\n\n"
        "2. 平台说明：\n"
        "   - Linux 端支持 ADB 控机、离线配置同步、账号云同步与数据分析。\n"
        "   - 本机桌面窗口自动化仅支持 Windows。\n\n"
        "3. 常见依赖提示（Ubuntu / Debian）：\n"
        "   若首次启动报 Qt 平台插件缺少库，请安装：\n"
        "   sudo apt-get install -y libxcb-cursor0 libgl1 libegl1 libxkbcommon-x11-0\n\n"
        "4. 桌面图标（可选）：\n"
        "   解压后运行一次 ./install-desktop.sh，把应用加入菜单并在任务栏 / Dock 显示图标；\n"
        "   Wayland 下窗口图标由桌面入口决定，不运行该脚本会显示通用图标。\n"
    )
    (target_root / "README-linux.txt").write_text(text, encoding="utf-8")


def _write_linux_desktop_installer(target_root: Path) -> None:
    script = target_root / "install-desktop.sh"
    script.write_text(_LINUX_DESKTOP_INSTALLER, encoding="utf-8", newline="\n")
    script.chmod(0o755)


def _build_linux(version: str, adb_bin: Path | None) -> Path:
    target_dir = DIST_DIR / "lvjiang"
    if not target_dir.is_dir():
        raise RuntimeError(f"PyInstaller 未生成目录: {target_dir}")

    _populate_factory_assets(target_dir, version, adb_bin)
    _write_linux_readme(target_dir, version)
    _write_linux_desktop_installer(target_dir)

    arch = _normalized_arch()
    archive = DIST_DIR / f"lvjiang-v{version}-linux-{arch}.tar.gz"
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(target_dir, arcname="lvjiang")
    print(f"[package_unix] Output: {archive}", flush=True)
    return archive


def _build_macos(version: str, adb_bin: Path | None, *, skip_codesign: bool) -> Path:
    app_bundle = DIST_DIR / "Lvjiang.app"
    if not app_bundle.is_dir():
        raise RuntimeError(f"PyInstaller 未生成 macOS App Bundle: {app_bundle}")

    macos_dir = app_bundle / "Contents/MacOS"
    _populate_factory_assets(macos_dir, version, adb_bin)

    if not skip_codesign:
        identity = os.environ.get("APPLE_SIGN_IDENTITY", "").strip() or "-"
        cmd = ["codesign", "--force", "--deep", "--sign", identity]
        if identity != "-":
            cmd += ["--options", "runtime", "--timestamp"]
        cmd.append(str(app_bundle))
        _run(cmd)

    arch = _normalized_arch()
    dmg_path = DIST_DIR / f"lvjiang-v{version}-macos-{arch}.dmg"
    if dmg_path.exists():
        dmg_path.unlink()

    with tempfile.TemporaryDirectory(prefix="lvjiang-dmg-") as tmp:
        stage = Path(tmp)
        shutil.copytree(app_bundle, stage / "Lvjiang.app", symlinks=True)
        os.symlink("/Applications", stage / "Applications")
        _run([
            "hdiutil",
            "create",
            "-volname",
            f"Lvjiang {version}",
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ])
    print(f"[package_unix] Output: {dmg_path}", flush=True)
    return dmg_path


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        raise SystemExit("Windows 请继续使用 packaging/package.bat")

    parser = argparse.ArgumentParser(description="构建 Linux / macOS 发行包")
    parser.add_argument("--adb-bin", type=Path, default=None, help="要内置到 data/adb/adb 的平台 adb 二进制路径")
    parser.add_argument("--skip-codesign", action="store_true", help="macOS 跳过 codesign（仅调试用）")
    args = parser.parse_args(argv)

    # 1. 注入版本号到 _version.py
    _run([sys.executable, str(ROOT / "packaging/inject_version.py")])
    version = _read_version()
    print(f"[package_unix] Version: {version} ({sys.platform}/{_normalized_arch()})", flush=True)

    # 2. 清理旧产物
    for stale in (DIST_DIR / "lvjiang", DIST_DIR / "Lvjiang.app"):
        if stale.exists():
            shutil.rmtree(stale)

    # 3. 执行 PyInstaller
    _run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging/lvjiang.spec"), "--noconfirm"])

    # 4. 组装平台发行包
    if sys.platform == "darwin":
        _build_macos(version, args.adb_bin, skip_codesign=args.skip_codesign)
    else:
        _build_linux(version, args.adb_bin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
