# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir 模式，见 package.bat）

关键点：
- rapidocr_onnxruntime 的 ONNX 模型与 config.yaml 藏在包内 data files，
  PyInstaller 静态分析收不到，缺了 OCR 会静默降级 —— collect_all 整包收集；
- lvjiang 的 .lark 语法文件是 package-data，同样需显式声明；
- 插件（load_app("yysls")）与工作流实现均为运行时动态 import，
  collect_submodules 全量收集 lvjiang 子模块兜底；
- config/system 与 data/scrcpy 不进 _internal，由 package.bat 拷到 exe 旁
  （用户可见，配合 config/local 覆盖机制）。
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = collect_all("rapidocr_onnxruntime")
datas += [("src/lvjiang/workflows/grammar", "lvjiang/workflows/grammar")]
hiddenimports += collect_submodules("lvjiang")

a = Analysis(
    ["packaging/launcher.py"],
    pathex=["src"],
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="lvjiang",
)
