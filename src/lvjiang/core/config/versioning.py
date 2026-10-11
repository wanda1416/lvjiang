"""已知配置类型登记与暂留的 content_version 读写工具。

登记用于已有消费者校验、编辑器及字段维护，不限制在线包可携带的文件。
在线更新按应用系列及 rN 修订安装，content_version 不再参与来源选择。
此字段暂留待后续格式清理；结构 schema_version 不受影响。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

#: 顶层字段名
CONTENT_VERSION_KEY = "content_version"


@dataclass(frozen=True)
class VersionedDir:
    """已知实体的路径、深度及是否保留内容版本字段；不是分发白名单。"""

    rel_dir: str
    pattern: str
    depth: int = 1
    has_content_version: bool = True


#: rel_dir → VersionedDir。core 只放自己的；插件经 register_versioned_dir 注册。
VERSIONED_DIRS: dict[str, VersionedDir] = {}
#: 不适合用目录 glob 表达的单文件配置。键是相对配置层根的完整路径。
VERSIONED_FILES: dict[str, VersionedDir] = {}


def register_versioned_dir(rel_dir: str, pattern: str, *, depth: int = 1,
                           has_content_version: bool = True) -> None:
    """登记已有加载器认识的配置类型；插件在配置策略模块导入时注册。"""
    VERSIONED_DIRS[rel_dir] = VersionedDir(rel_dir, pattern, depth, has_content_version)


def register_versioned_file(rel_path: str) -> None:
    """登记一个已有加载器认识的聚合配置文件。"""
    path = Path(rel_path)
    VERSIONED_FILES[rel_path] = VersionedDir(
        rel_dir=path.parent.as_posix(), pattern=path.name, depth=1)


# core 自己的几类：跨插件通用，不属于任何游戏领域
register_versioned_dir("scenes", "*.yaml", depth=1)
register_versioned_dir("layouts", "*.json", depth=2)
register_versioned_dir("maps", "map.yaml", depth=2)
register_versioned_dir("workflows", "*.wf", depth=0, has_content_version=False)
register_versioned_file("ocr.yaml")


def spec_for(rel_path: str) -> VersionedDir | None:
    """rel_path 属于哪一类带版本实体；不属于任何一类返回 None。

    按 rel_dir 前缀 + 层数 + 扩展名三者同时匹配——只匹配前缀会把
    ``layouts.yaml``（聚合注册表文件，不带版本）也算进 ``layouts/``。
    """
    exact = VERSIONED_FILES.get(rel_path)
    if exact is not None:
        return exact
    parts = rel_path.split("/")
    for spec in VERSIONED_DIRS.values():
        prefix = spec.rel_dir.split("/")
        if parts[:len(prefix)] != prefix:
            continue
        child_depth = len(parts) - len(prefix)
        if child_depth < 1 or (spec.depth and child_depth != spec.depth):
            continue
        if Path(rel_path).match(spec.pattern):
            return spec
    return None


# ─── 读写 content_version ────────────────────────────────

def _version_from_json(text: str) -> int | None:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(CONTENT_VERSION_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _version_from_yaml(text: str) -> int | None:
    """只扫顶层的那一行，不整份 yaml.safe_load。

    编辑器展示来源时只需读取这个字段；顶层键必然顶格，无需解析全文。
    """
    for line in text.splitlines():
        if not line.startswith(f"{CONTENT_VERSION_KEY}:"):
            continue
        raw = line.split(":", 1)[1].split("#", 1)[0].strip()
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def version_from_text(text: str, suffix: str) -> int | None:
    """从文件内容取 content_version；缺失或非法返回 None。"""
    if suffix == ".json":
        return _version_from_json(text)
    return _version_from_yaml(text)


def read_text_preserving_eol(path: Path) -> str:
    """按原样读出文本，**不做换行转换**（CRLF 原封保留）。

    不能用 ``path.read_text(newline="")``：``read_text`` 的 ``newline``
    参数是 Python 3.13 才加的，而本仓库 requires-python 为 >=3.10、
    Android 侧 Chaquopy 固定跑 3.10，在那里会直接 TypeError。

    换行必须原样保留：``_with_version_json`` 靠 ``"\r\n" in text`` 判断原
    文件的行尾，若被默认的通用换行模式翻成 ``\n``，CRLF 的系统配置每次盖
    版本号都会被改写成 LF，产生整文件 diff。
    """
    return path.read_bytes().decode("utf-8")


def read_version(path: Path) -> int | None:
    """从文件读 content_version；读不到（不存在/损坏/缺字段）返回 None。"""
    try:
        text = read_text_preserving_eol(path)
    except (OSError, UnicodeDecodeError):
        return None
    return version_from_text(text, path.suffix)


def _with_version_json(text: str, version: int) -> str:
    data = json.loads(text)
    eol = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(("\n", "\r"))
    merged = {CONTENT_VERSION_KEY: version,
              **{k: v for k, v in data.items() if k != CONTENT_VERSION_KEY}}
    out = json.dumps(merged, ensure_ascii=False, indent=2)
    if trailing:
        out += "\n"
    return out.replace("\n", eol) if eol != "\n" else out


def _with_version_yaml(text: str, version: int) -> str:
    eol = "\r\n" if "\r\n" in text else "\n"
    new_line = f"{CONTENT_VERSION_KEY}: {version}"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{CONTENT_VERSION_KEY}:"):
            lines[index] = new_line
            out = eol.join(lines)
            return out + eol if text.endswith(("\n", "\r")) else out
    return f"{new_line}{eol}{text}"


def with_version(text: str, suffix: str, version: int) -> str:
    """把 content_version 写进内容，尽量不动其余格式（行尾/键序/注释）。"""
    if suffix == ".json":
        return _with_version_json(text, version)
    return _with_version_yaml(text, version)


def preserve_version_for_write(rel_path: str, new_text: str,
                               current_path: Path | None) -> str:
    """普通开发保存：保留 system 当前版本，新文件从 v1 起步。

    编辑器序列化场景、布局和插件配置时通常不会把 ``content_version`` 放进
    业务模型，因此不能简单删除旧的自动 bump 逻辑，否则第一次保存就会把
    顶层版本字段写没。这里把版本字段视为存储元数据：普通保存沿用旧号，
    只有编辑器显式传入目标版本时才提升。
    """
    suffix = Path(rel_path).suffix
    current_version = read_version(current_path) if current_path is not None else None
    version = current_version if current_version is not None else 1
    try:
        return with_version(new_text, suffix, version)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning(f"content_version 写入跳过（内容无法解析）{rel_path}: {exc}")
        return new_text


def iter_versioned_files(layer_root: Path) -> Iterator[Path]:
    """遍历某个配置层根目录下所有参与版本管理的实体文件。

    供 `scripts/add_content_version.py` 补齐存量文件、以及测试做全量校验。
    """
    yielded: set[Path] = set()
    for rel_path in VERSIONED_FILES:
        path = layer_root / rel_path
        if path.is_file():
            yielded.add(path)
            yield path
    for spec in VERSIONED_DIRS.values():
        if not spec.has_content_version:
            continue
        base = layer_root / spec.rel_dir
        if not base.is_dir():
            continue
        glob = "/".join(["*"] * (spec.depth - 1) + [spec.pattern])
        for path in sorted(base.glob(glob)):
            if (path.is_file() and path not in yielded
                    and not path.name.startswith("_")):
                yield path
