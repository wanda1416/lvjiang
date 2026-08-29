"""实体配置的 ``content_version`` —— remote 与 system 谁为主的唯一判据。

## 为什么需要它

`config/remote/`（在线下发层）插在 local 与 system 之间之后，"remote 优先"
是错的：用户升了 App，system 带来 v5 的场景坐标，而远程还停在给旧版本热
修的 v3——无脑覆盖会把配置**静默回退**，没有报错，只表现为"识别又坏了"。
所以 remote 只在 ``remote.content_version > system.content_version`` 时才
生效，见 `resolver.ConfigResolver.resolve_read`。

## 语义

- 整数，从 1 起，**内容变一次加一**（不是 App 版本号，也不是结构版本号）
- system 侧**缺失即拒绝任何 remote 替换**（fail-safe）：将来漏给某个新文件
  加字段时，后果是"这个文件收不到在线更新"，而不是"这个文件被远程悄悄
  接管"。前者能被人发现，后者不能。
- 与 `graduation/*.json` 的 ``schema_version``（**结构**版本，决定怎么解析）
  是两回事，两者可以并存；也和 `references/*.yaml` 那个从未被消费的
  ``version: 1`` 无关——加了不读的字段迟早会烂，这个字段是真的被读的。

## 谁维护它

开发模式普通保存只**保留当前版本号**，版本提升由编辑器中的显式操作请求，
并和其他编辑状态一起在保存时落盘。这样一次完整编辑只产生一个发布版本，
不会把每次自动保存都误算成新的内容代次。新文件仍自动从 1 起步。
`scripts/add_content_version.py` 负责给存量/新增文件补齐初始值。

## 哪些目录参与

core 只声明自己的（`scenes/`、`layouts/`）。插件私有目录由插件经
`AppHooks.config_policy_modules` 自行注册（燕云的 `yysls/tuning_rules/` 见
`apps/yysls/config/merge_policy.py`）——core.config 不认识任何插件领域词汇，
理由同 `resolver.REGISTRY_LIST_PATHS`。
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
    """一类参与 remote 下发的实体文件。

    Attributes:
        rel_dir: 相对 config 层根的目录（如 ``"layouts"``）
        pattern: 文件名 glob（如 ``"*.json"``）
        depth: rel_dir 之下的层数——``scenes/a.yaml`` 是 1，
            ``layouts/{布局}/{场景}.json`` 是 2
        allow_remote_new: remote 是否可以下发 system 里**不存在**的新文件。

            默认 False：新场景/新布局要在 `scenes.yaml` 注册表里登记才有意义，
            而注册表本身走发版不走 remote（改注册表通常伴随代码改动），远程
            凭空多一个场景文件是死的，只会让编辑器列表里冒出一个用不了的条目。
            `yysls/tuning_rules/` 是例外并声明为 True：规则管理器对"未在
            tune_config.tuning_rules 里声明的规则"是追加到末尾而非报错
            （见 `tuning_rules/manager.py` 的 _reload），所以远程新增一条
            调律规则能直接生效——这恰是最有价值的在线下发场景。
    """

    rel_dir: str
    pattern: str
    depth: int = 1
    allow_remote_new: bool = False


#: rel_dir → VersionedDir。core 只放自己的；插件经 register_versioned_dir 注册。
VERSIONED_DIRS: dict[str, VersionedDir] = {}


def register_versioned_dir(rel_dir: str, pattern: str, *, depth: int = 1,
                           allow_remote_new: bool = False) -> None:
    """声明一类参与 remote 下发 / content_version 自动维护的实体文件。

    插件在自己的配置策略模块顶层调用（见模块文档「哪些目录参与」）。
    """
    VERSIONED_DIRS[rel_dir] = VersionedDir(
        rel_dir=rel_dir, pattern=pattern, depth=depth,
        allow_remote_new=allow_remote_new)


# core 自己的两类：跨插件通用，不属于任何游戏领域
register_versioned_dir("scenes", "*.yaml", depth=1)
register_versioned_dir("layouts", "*.json", depth=2)


def spec_for(rel_path: str) -> VersionedDir | None:
    """rel_path 属于哪一类带版本实体；不属于任何一类返回 None。

    按 rel_dir 前缀 + 层数 + 扩展名三者同时匹配——只匹配前缀会把
    ``layouts.yaml``（聚合注册表文件，不带版本）也算进 ``layouts/``。
    """
    parts = rel_path.split("/")
    for spec in VERSIONED_DIRS.values():
        prefix = spec.rel_dir.split("/")
        if parts[:len(prefix)] != prefix:
            continue
        if len(parts) - len(prefix) != spec.depth:
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

    resolve_read 是热路径（每次读实体都要判一次 remote 是否更新），为一个
    整数解析整份规则文件不划算；顶层键必然顶格，扫行足够可靠。
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
    for spec in VERSIONED_DIRS.values():
        base = layer_root / spec.rel_dir
        if not base.is_dir():
            continue
        glob = "/".join(["*"] * (spec.depth - 1) + [spec.pattern])
        for path in sorted(base.glob(glob)):
            if path.is_file() and not path.name.startswith("_"):
                yield path
