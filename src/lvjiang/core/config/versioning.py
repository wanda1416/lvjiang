"""实体配置的 ``content_version`` —— remote 与 system 谁为主的唯一判据。

## 为什么需要它

`config/remote/`（在线下发层）插在 local 与 system 之间之后，"remote 优先"
是错的：用户升了 App，system 带来 v5 的场景坐标，而远端还停在给旧版本热
修的 v3——无脑覆盖会把配置**静默回退**，没有报错，只表现为"识别又坏了"。
所以 remote 只在 ``remote.content_version > system.content_version`` 时才
生效，见 `resolver.ConfigResolver.resolve_read`。

## 语义

- 整数，从 1 起，**内容变一次加一**（不是 App 版本号，也不是结构版本号）
- system 侧**缺失即拒绝任何 remote 替换**（fail-safe）：将来漏给某个新文件
  加字段时，后果是"这个文件收不到在线更新"，而不是"这个文件被远端悄悄
  接管"。前者能被人发现，后者不能。
- 与 `graduation/*.json` 的 ``schema_version``（**结构**版本，决定怎么解析）
  是两回事，两者可以并存；也和 `references/*.yaml` 那个从未被消费的
  ``version: 1`` 无关——加了不读的字段迟早会烂，这个字段是真的被读的。

## 谁维护它

开发模式经 `resolver.write_entity` 落盘时**自动 +1**（内容真的变了才加），
不靠人手改——这三类文件全都是 UI 编辑器写出来的，手写的版本号存一次就没。
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
            而注册表本身走发版不走 remote（改注册表通常伴随代码改动），远端
            凭空多一个场景文件是死的，只会让编辑器列表里冒出一个用不了的条目。
            `yysls/tuning_rules/` 是例外并声明为 True：规则管理器对"未在
            tune_config.tuning_rules 里声明的规则"是追加到末尾而非报错
            （见 `tuning_rules/manager.py` 的 _reload），所以远端新增一条
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
    文件的行尾，若被默认的通用换行模式翻成 ``\n``，CRLF 的出厂配置每次盖
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


def strip_version(text: str, suffix: str) -> str:
    """去掉 content_version 后的内容，用于「内容是否真的变了」的比对。"""
    if suffix == ".json":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return text
        if not isinstance(data, dict):
            return text
        rest = {k: v for k, v in data.items() if k != CONTENT_VERSION_KEY}
        return json.dumps(rest, ensure_ascii=False, indent=2, sort_keys=True)
    return "\n".join(line for line in text.splitlines()
                     if not line.startswith(f"{CONTENT_VERSION_KEY}:"))


def next_version_for_write(rel_path: str, new_text: str,
                           current_path: Path | None,
                           floor_version: int | None = None) -> str:
    """开发模式落盘前给内容盖上正确的 content_version。

    内容（去掉版本号后）与盘上一致就保持原版本号，真的变了才 +1——每次
    保存都 +1 会让"打开编辑器又原样关掉"也推高版本，remote 侧就分不清
    哪次是真改动。盘上没有旧文件（新建）时从 1 起。

    ``floor_version`` 是**当前实际被读到的那一层**的版本号（开发模式下
    远端可能正顶替着出厂文件）。不传这个的话会出一个坏状态：开发者在
    编辑器里看到的是远端 v3 的内容，改完保存却拿出厂 v2 做基线写出
    v3——于是 v3 有两份不同内容，一份在仓库、一份在线上，版本号不再唯一
    标识内容，作者提交后自己都分不清。传了之后新版本恒大于已发布的那份，
    作者的改动确定性地胜出。

    Returns:
        盖好版本号的内容文本。
    """
    suffix = Path(rel_path).suffix
    old_text = ""
    if current_path is not None and current_path.exists():
        try:
            old_text = read_text_preserving_eol(current_path)
        except (OSError, UnicodeDecodeError):
            old_text = ""

    old_version = version_from_text(old_text, suffix) if old_text else None
    unchanged = (old_text
                 and strip_version(old_text, suffix) == strip_version(new_text, suffix))
    if unchanged and (floor_version is None
                      or (old_version is not None and old_version > floor_version)):
        # 内容没变且没被更新的远端版本压着：保持原版本号
        return with_version(new_text, suffix, old_version) if old_version else new_text

    base = max(old_version or 0, floor_version or 0)
    try:
        return with_version(new_text, suffix, base + 1)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # 内容不是合法 JSON 就原样落盘——写入方自己会因为格式错误报错，
        # 这里不该抢先抛一个"版本号写不进去"的次要错误盖住真正的原因。
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
