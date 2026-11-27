# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir 模式，见 package.bat）

关键点：
- rapidocr_onnxruntime 的 ONNX 模型与 config.yaml 藏在包内 data files，
  PyInstaller 静态分析收不到，缺了 OCR 会静默降级 —— collect_all 整包收集；
- lvjiang 的 .lark 语法文件是 package-data，同样需显式声明；
- 插件（load_app("yysls")）与工作流实现均为运行时动态 import，
  collect_submodules 全量收集 lvjiang 子模块兜底；
- config/system 与 data/adb 不进 _internal，由 package.bat 拷到 exe 旁
  （用户可见，配合 config/local 覆盖机制；内置 adb 使用户免装 platform-tools）；
- windows_capture（后台截图）只在用到时才 import，静态分析收不到，且它带一个原生
  .pyd —— collect_all 整包收集。没装时跳过：非 Windows 本来就不会打包。
"""
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

# 图标资产与 exe 图标同源（src/lvjiang/assets，由 scripts/one_off/make_app_icon.py 生成）。
# 运行时按包目录加载，所以 datas 的落点是 lvjiang/assets。
_ASSETS = Path(SPECPATH) / ".." / "src" / "lvjiang" / "assets"
if sys.platform == "win32":
    _APP_ICON = str(_ASSETS / "icon.ico")
elif sys.platform == "darwin":
    _icns = _ASSETS / "icon.icns"
    _APP_ICON = str(_icns if _icns.is_file() else _ASSETS / "icon.png")
else:
    _APP_ICON = str(_ASSETS / "icon.png")

datas, binaries, hiddenimports = collect_all("rapidocr_onnxruntime")
datas += [("../src/lvjiang/workflows/grammar", "lvjiang/workflows/grammar")]
datas += [(str(_ASSETS / "icon.png"), "lvjiang/assets")]
hiddenimports += collect_submodules("lvjiang")
# keyring 通过运行时发现加载各平台凭据后端。
if sys.platform == "win32":
    hiddenimports += ["keyring.backends.Windows"]
elif sys.platform == "darwin":
    hiddenimports += ["keyring.backends.macOS"]
else:
    hiddenimports += ["keyring.backends.SecretService", "keyring.backends.kwallet"]
hiddenimports += collect_submodules("uvicorn")
datas += copy_metadata("keyring")
datas += copy_metadata("mcp")
datas += collect_data_files("jsonschema_specifications")

if sys.platform == "win32":
    try:
        wc_datas, wc_binaries, wc_hidden = collect_all("windows_capture")
    except Exception as exc:  # noqa: BLE001 - 打包脚本，缺包只需跳过并提示
        print(f"[spec] 未收集 windows_capture（后台截图将不可用）: {exc}")
    else:
        datas += wc_datas
        binaries += wc_binaries
        hiddenimports += wc_hidden

a = Analysis(
    ["launcher.py"],
    pathex=[str((Path(SPECPATH) / ".." / "src").resolve())],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_qt", "ruff", "mypy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lvjiang",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 压缩会显著抬高杀软误报率，不启用
    console=False,  # GUI 应用；崩溃日志由 crash_handler 落盘 logs/crashes/
    disable_windowed_traceback=False,
    icon=_APP_ICON,
)

# 独立薄壳 Analysis：lvjiang-cli 只打包纯标准库客户端 cli_client.py，
# 不再复用主程序 PYZ（彻底剔除 lvjiang 业务字节码、PyQt6、OCR、OpenCV 等重依赖），
# 仅通过 127.0.0.1 本地控制面与运行中的 lvjiang 主实例（或自动拉起的无头实例）通信。
cli_a = Analysis(
    ["cli_launcher.py"],
    pathex=[str((Path(SPECPATH) / ".." / "src" / "lvjiang").resolve())],
    binaries=[],
    datas=[],
    hiddenimports=["cli_client"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "lvjiang",
        "PyQt6",
        "cv2",
        "numpy",
        "onnxruntime",
        "rapidocr_onnxruntime",
        "PIL",
        "scipy",
        "uvicorn",
        "mcp",
        "lark",
        "yaml",
        "loguru",
        "fasteners",
        "keyring",
        "pytest",
        "pytest_qt",
        "ruff",
        "mypy",
    ],
    noarchive=False,
)
cli_pyz = PYZ(cli_a.pure)

cli = EXE(
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="lvjiang-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=_APP_ICON,
)

coll = COLLECT(
    exe,
    cli,
    a.binaries,
    cli_a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="lvjiang",
)

if sys.platform == "darwin":
    _app_version = tomllib.loads(
        (Path(SPECPATH) / ".." / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    app = BUNDLE(
        coll,
        name="Lvjiang.app",
        icon=_APP_ICON,
        bundle_identifier="com.lvjiang.app",
        info_plist={
            "CFBundleDisplayName": "律匠",
            "CFBundleShortVersionString": _app_version,
            "CFBundleVersion": _app_version,
            "NSHighResolutionCapable": True,
        },
    )
