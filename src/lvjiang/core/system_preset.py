"""官方 system 预置包：标准库构建、校验、整目录升级；不访问个人配置。"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .._version import __version__

PRESET_FORMAT = 1
MAX_PRESET_BYTES = 256 << 20


def build_system_preset(system_dir: Path, destination: Path) -> dict:
    """输入仅为官方 system 根；文件顺序和 ZIP 时间固定，便于复核内容摘要。"""
    files = {}
    contents = {}
    for path in sorted(system_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError("官方预置包不允许符号链接")
        rel = path.relative_to(system_dir)
        if not path.is_file() or any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        if path.suffix in {".db", ".lock", ".tmp", ".py", ".pyc"}:
            raise ValueError(f"官方预置目录包含非配置文件：{rel}")
        data = path.read_bytes()
        contents[rel.as_posix()] = data
        files[rel.as_posix()] = hashlib.sha256(data).hexdigest()
    if not {"app.yaml", "ocr.yaml", "layouts.yaml"} <= files.keys():
        raise ValueError("官方预置包缺少应用、OCR 或布局定义")
    if sum(map(len, contents.values())) > MAX_PRESET_BYTES:
        raise ValueError("官方预置包展开后超过限制")
    preset_id = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    manifest = {"format": PRESET_FORMAT, "app_version": __version__, "preset_id": preset_id, "files": files}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in {**contents, "manifest.json": json.dumps(manifest, ensure_ascii=False).encode()}.items():
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return manifest


def install_system_preset(archive_path: Path, root: Path, *, on_applied: Callable[[], None] | None = None) -> bool:
    """只交换 system；按预置内容摘要去重，PC 同步后重启不反向覆盖。"""
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    marker = config / "session/preset.json"
    target = config / "system"
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        files = manifest["files"]
        if manifest.get("format") != PRESET_FORMAT or manifest.get("app_version") != __version__:
            raise ValueError("预置包格式或应用版本不一致")
        digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        if digest != manifest.get("preset_id"):
            raise ValueError("预置清单摘要不一致")
        if marker.exists() and all((target / name).is_file() for name in ("app.yaml", "ocr.yaml", "layouts.yaml")):
            saved = json.loads(marker.read_text(encoding="utf-8"))
            if saved.get("preset_id") == digest:
                return False
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != {*files, "manifest.json"}:
            raise ValueError("预置包文件清单不一致")
        if sum(info.file_size for info in archive.infolist()) > MAX_PRESET_BYTES:
            raise ValueError("预置包展开后超过限制")
        if not {"app.yaml", "ocr.yaml", "layouts.yaml"} <= files.keys():
            raise ValueError("预置包缺少基础定义")
        with tempfile.TemporaryDirectory(prefix=".preset-stage-", dir=config) as temporary:
            stage = Path(temporary)
            new = stage / "new"
            new.mkdir()
            for name, expected in files.items():
                rel = PurePosixPath(name)
                if rel.is_absolute() or ".." in rel.parts or "\\" in name or rel.as_posix() != name:
                    raise ValueError("预置包包含非法路径")
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("预置文件校验失败")
                path = new.joinpath(*rel.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            old = stage / "old"
            if target.exists():
                target.rename(old)
            try:
                new.rename(target)
                if on_applied:
                    on_applied()
                marker.parent.mkdir(parents=True, exist_ok=True)
                # 只记录最后安装的官方代次，不代表当前 system 来自 PC 还是 APK。
                pending = stage / "preset.json"
                pending.write_text(json.dumps({"preset_id": digest, "app_version": __version__}), encoding="utf-8")
                pending.replace(marker)
            except Exception:
                if target.exists():
                    target.rename(stage / "failed")
                if old.exists():
                    old.rename(target)
                raise
            if old.exists():
                backup = root / "preset-backup/system"
                backup.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists():
                    shutil.rmtree(backup)
                old.rename(backup)
    return True
