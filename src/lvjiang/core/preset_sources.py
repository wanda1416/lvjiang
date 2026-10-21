"""官方预置的输入清单与字节归一：只认 git 索引里的文件，文本统一 LF。

``config/system`` 在开发模式下也是编辑器的可写目录，运行时会留下
``_editor_run.wf``、``_testwf/`` 之类的草稿；``.gitignore`` 已经忽略它们，但按
磁盘整树打包会把它们带进预置——同一个提交在不同机器上就会得到不同摘要。
这里把「什么算官方配置」交给版本控制回答。

行尾同理：仓库与 ``.gitattributes`` 约定 LF，工作区却可能被外部工具或历史遗留
写成 CRLF，而 ``.gitattributes`` 的归一化让它在 git 状态里隐身。按磁盘字节算
摘要会随机器漂移，手机装预置时报「无法确认原始预置基底」。打包前把文本归一成
LF，摘要才只由提交内容决定。
"""
from __future__ import annotations

import subprocess
from collections.abc import Collection, Iterable
from pathlib import Path

# 与 .gitattributes 的 eol=lf 规则配套；tests/core/test_preset_sources.py 校验
# 两处不漂移——漏一个后缀，跨机器摘要就又开始飘。
TEXT_SUFFIXES = frozenset({".json", ".yaml", ".yml", ".wf", ".sh", ".md", ".txt"})

_EXCLUDED_PREVIEW = 10


def tracked_files(root: Path, relative: str) -> list[str] | None:
    """返回 ``relative/`` 下纳入 git 索引的相对路径（POSIX 分隔）。

    不是 git 工作区（没有 ``.git``、没有 git 命令或命令失败）时返回 ``None``，
    由调用方决定回退策略；返回空列表表示该目录确实没有跟踪文件。
    """
    try:
        result = subprocess.run(
            # -z：路径按 NUL 分隔，既免了中文文件名被 C-引号包成 "xxx.png"，
            # 也免了引号里的反斜杠转义。
            ["git", "-C", str(root), "ls-files", "-z", "--", relative],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    prefix = relative.rstrip("/") + "/"
    paths: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        if path.startswith(prefix):
            paths.append(path[len(prefix):])
    return paths


def present_files(root: Path) -> list[str]:
    """列出现存文件（POSIX 相对路径），只做轻量遍历。

    与 ``read_tree`` 不同，这里不读字节、不校验名字、不计体积：它只用来打印
    「排除了什么」。待排除的草稿即便名字不合规或体积超限，也不该让构建在
    排除它之前就失败。
    """
    if not root.is_dir():
        return []
    return [path.relative_to(root).as_posix() for path in sorted(root.rglob("*"))
            if path.is_file()]


def normalize_text(name: str, data: bytes) -> bytes:
    """文本文件的行尾归一为 LF；其余字节原样返回。

    只按后缀判断，绝不对未知类型做替换：PNG 签名 ``\\x89PNG\\r\\n\\x1a\\n``
    自带 CRLF，盲目替换会直接毁图。
    """
    if Path(name).suffix.lower() not in TEXT_SUFFIXES:
        return data
    return data.replace(b"\r\n", b"\n")


def normalize_tree(contents: dict[str, bytes]) -> dict[str, bytes]:
    """对整棵树的字节做行尾归一，返回新字典（不改入参）。"""
    return {name: normalize_text(name, data) for name, data in contents.items()}


def excluded_files(present: Iterable[str], include: Collection[str]) -> list[str]:
    """现存但不在 git 索引里的文件（即未跟踪草稿），按路径排序。"""
    return sorted(set(present) - set(include))


def format_excluded(relative: str, excluded: Collection[str]) -> str:
    """把排除清单拼成一行提示；没有排除项时返回空串。"""
    if not excluded:
        return ""
    listed = sorted(excluded)
    preview = "、".join(listed[:_EXCLUDED_PREVIEW])
    if len(listed) > _EXCLUDED_PREVIEW:
        return f"已排除 {relative} 下未纳入 git 的文件：{preview}（共 {len(listed)} 个）"
    return f"已排除 {relative} 下未纳入 git 的文件：{preview}"


__all__ = [
    "TEXT_SUFFIXES",
    "excluded_files",
    "format_excluded",
    "normalize_text",
    "normalize_tree",
    "present_files",
    "tracked_files",
]
