"""文档健康检查：死链、锚点、孤儿、入口可达、编号冲突与索引登记。

用法：

    python scripts/docs_check.py            # 主工程检查，有问题时退出码 1
    python scripts/docs_check.py --admin    # 独立管理仓库
    python scripts/docs_check.py --bundle PATH  # 生成的发行文档

检查范围是 `docs/` 下全部 `.md`；链接目标可以是仓库内任意文件（源码、配置、
打包手册都允许被引用）。纯静态检查，不联网、不依赖 Qt，可以直接进 CI。

只保「链接和索引不烂」：目录自己表达文档生命周期（20-plans → 30-design → 40-reference），
修改时间由 Git 表达。本脚本不检查日期、基线或状态行，也不再要求任何文档写
`> 最后更新：…`。约定见 `docs/00-meta/01-doc-standard.md`。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

#: 链接与标题；fenced code block 先剥掉，避免把示例里的 `#` 当标题
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+?)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.M)
H1 = re.compile(r"^#\s+(.*)$", re.M)
FENCE_MARK = ("```", "~~~")
#: 编号前缀：`01-`、`03.1-`、`03.01-` 各算一个前缀；`2026-07-14-…` 是日期，不参与
NUMBER_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)[.-](?=\D)")
#: GitHub 生成锚点时删除标点但保留下划线，空格转连字符
ANCHOR_DROP = re.compile(r"[^\w\u4e00-\u9fff\- ]")
#: 每层索引文件必须覆盖同目录全部 .md；开发日志月份目录由 DEV_MONTH 动态发现，
#: releases 系列子目录按递归覆盖，均不在这里硬编码。
INDEX_DIRS = ("50-releases",)
#: 这些目录的索引必须覆盖**递归**全部 .md：发布说明按系列归档到子目录，
#: 每个子目录不再各配一份 README，登记责任仍归顶层索引。
RECURSIVE_INDEX_DIRS = ("50-releases",)
#: 开发日志的月份子目录名（history/02-dev/YYYY-MM），索引覆盖动态发现
DEV_MONTH = re.compile(r"\d{4}-\d{2}")
SKIP_PREFIX = ("http://", "https://", "mailto:", "#")

#: 编号顶层目录：其直接子目录与非 README 文件名必须以数字开头（NN- 或日期前缀），
#: 50-releases 顶层例外允许版本号（v<数字>）排序。
NUMBERED_TOP_DIRS = ("00-meta", "10-domain", "20-plans", "30-design", "40-reference",
                     "50-releases", "60-guide", "70-agent", "80-history")
VERSION_PREFIX = re.compile(r"^v\d")


def slug(title: str) -> str:
    text = re.sub(r"[`*]", "", title.strip().lower())
    return ANCHOR_DROP.sub("", text).replace(" ", "-")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def body_without_code(text: str) -> str:
    """逐行跟踪围栏状态，剥掉代码块（示例里的 `#` 不是标题）。"""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(FENCE_MARK):
            inside = not inside
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def check_links(files: list[pathlib.Path]) -> tuple[list[str], set[pathlib.Path]]:
    problems: list[str] = []
    inbound: set[pathlib.Path] = set()
    headings = {
        path: {slug(m.group(2)) for m in HEADING.finditer(body_without_code(read(path)))}
        for path in files
    }
    for path in files:
        rel = path.relative_to(REPO).as_posix()
        for match in LINK.finditer(read(path)):
            raw = match.group(1)
            if raw.startswith(SKIP_PREFIX):
                continue
            target, _, anchor = raw.partition("#")
            resolved = (path.parent / target).resolve() if target else path
            if not resolved.exists():
                problems.append(f"死链 {rel} -> {raw}")
                continue
            if resolved.is_dir():
                index = resolved / "README.md"
                if index.is_file():
                    inbound.add(index)
                continue
            inbound.add(resolved)
            if anchor and resolved.suffix == ".md" and resolved in headings:
                if anchor not in headings[resolved]:
                    problems.append(f"锚点失效 {rel} -> {raw}")
    return problems, inbound


def reachable_from_entry(files: list[pathlib.Path]) -> set[pathlib.Path]:
    """从 docs/README.md 沿文档链接可达的集合。

    只看「有没有入链」会让互相引用的孤岛通过；真正的要求是从导航入口
    沿链接一步步能走到。链接只算 .md 目标（图片与资源不承载导航）。
    """
    entry = DOCS / "README.md"
    if not entry.is_file():
        return {path.resolve() for path in files}
    seen: set[pathlib.Path] = set()
    queue = [entry]
    while queue:
        path = queue.pop()
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        text = body_without_code(read(path))
        for match in LINK.finditer(text):
            raw = match.group(1)
            if raw.startswith(SKIP_PREFIX):
                continue
            target = raw.partition("#")[0]
            nxt = (path.parent / target).resolve() if target else path
            if nxt.suffix == ".md" and nxt.is_file() and nxt.resolve() not in seen:
                queue.append(nxt)
    return seen


def check_orphans(files: list[pathlib.Path], inbound: set[pathlib.Path]) -> list[str]:
    return [
        f"孤儿文档（无任何入链） {path.relative_to(REPO).as_posix()}"
        for path in files
        if path.name != "README.md" and path.resolve() not in inbound
    ]


def check_unreachable(files: list[pathlib.Path]) -> list[str]:
    reachable = reachable_from_entry(files)
    return [
        f"入口不可达（docs/README.md 链接走不到） {path.relative_to(REPO).as_posix()}"
        for path in files
        if path.resolve() not in reachable
    ]


#: 形如 `# src/lvjiang/apps/base.py` 的“标题”是代码块的文件名标注，不算标题
CODE_CAPTION = re.compile(r"^[^\s]*[./\\][^\s]*$")


def check_titles(files: list[pathlib.Path]) -> list[str]:
    """一篇一个一级标题，且标题下一行必须是空行。

    标题紧贴正文（`# 标题` 直接接 `> 状态：…`）在渲染与纯文本阅读里都挤在一起，
    是零成本的排版门禁；只做结构检查，不检查日期、基线或状态内容。
    """
    problems: list[str] = []
    for path in files:
        rel = path.relative_to(DOCS).as_posix()
        lines = body_without_code(read(path)).splitlines()
        titles = [title for title in H1.findall("\n".join(lines))
                  if not CODE_CAPTION.match(title.strip())]
        if len(titles) != 1:
            problems.append(f"一级标题数量 {len(titles)} 处 {rel}")
        for index, line in enumerate(lines):
            if not line.startswith("# ") or CODE_CAPTION.match(line[2:].strip()):
                continue
            if index + 1 < len(lines) and lines[index + 1].strip():
                problems.append(f"一级标题后缺空行 {rel}")
            break
    return problems


def check_duplicate_numbers(files: list[pathlib.Path]) -> list[str]:
    problems: list[str] = []
    seen: dict[pathlib.Path, dict[str, str]] = collections.defaultdict(dict)
    for path in files:
        match = NUMBER_PREFIX.match(path.name)
        if not match or match.group(1).startswith(("19", "20")) and len(match.group(1)) == 4:
            continue
        used = seen[path.parent]
        if match.group(1) in used:
            rel = path.parent.relative_to(REPO).as_posix()
            problems.append(
                f"目录内编号重复 {rel}: {used[match.group(1)]} 与 {path.name}")
        else:
            used[match.group(1)] = path.name
    return problems




def check_number_prefixes() -> list[str]:
    problems: list[str] = []
    for name in NUMBERED_TOP_DIRS:
        directory = DOCS / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.name == "README.md" or path.name.startswith("."):
                continue
            if name == "50-releases" and VERSION_PREFIX.match(path.name):
                continue
            if not re.match(r"^\d", path.name):
                problems.append(f"缺编号前缀 {path.relative_to(DOCS).as_posix()}")
    return problems


def check_index_coverage() -> list[str]:
    problems: list[str] = []
    names = set(INDEX_DIRS) if DOCS == REPO / "docs" else set()
    names.update(path.parent.relative_to(DOCS).as_posix()
                 for path in DOCS.rglob("README.md"))
    # 开发日志月份目录必须各有一份覆盖当月全部日志的 README，目录名动态发现
    dev_root = DOCS / "80-history" / "02-dev"
    if dev_root.is_dir():
        names.update(path.relative_to(DOCS).as_posix()
                     for path in dev_root.iterdir()
                     if path.is_dir() and DEV_MONTH.fullmatch(path.name))
    for name in sorted(names):
        directory = DOCS / name
        index = directory / "README.md"
        if not index.is_file():
            problems.append(f"索引缺失 {name}/README.md")
            continue
        linked = {
            (directory / match.group(1).partition("#")[0]).resolve()
            for match in LINK.finditer(read(index))
            if not match.group(1).startswith(SKIP_PREFIX)
        }
        pattern = "**/*.md" if name in RECURSIVE_INDEX_DIRS else "*.md"
        for path in sorted(directory.glob(pattern)):
            if path.name != "README.md" and path.resolve() not in linked:
                problems.append(
                    f"索引漏登记 {name}/README.md -> {path.name}")
    return problems


def check_bundle_digests(bundle: pathlib.Path, manifest: dict) -> list[str]:
    """清单闭合与摘要一致：清单是发行包读取的唯一权威。"""
    import hashlib

    problems: list[str] = []
    seen: set[str] = set()
    for entry in manifest["documents"]:
        rel = entry["path"]
        if rel in seen:
            problems.append(f"清单重复条目 {rel}")
            continue
        seen.add(rel)
        path = bundle / rel
        if not path.is_file():
            problems.append(f"清单文件缺失 {rel}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.get("sha256"):
            problems.append(f"摘要与正文不符 {rel}")
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    manifest_rel = "70-agent/00-manifest.json"
    for rel in sorted(actual - seen - {manifest_rel}):
        problems.append(f"清单外文件 {rel}")
    return problems


def check_bundle(bundle: pathlib.Path) -> list[str]:
    bundle = bundle.resolve()
    manifest_path = bundle / "70-agent/00-manifest.json"
    if not manifest_path.is_file():
        return [f"清单缺失 {manifest_path.relative_to(bundle).as_posix()}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [*check_bundle_digests(bundle, manifest), *check_bundle_links(bundle, manifest)]


def check_bundle_links(bundle: pathlib.Path, manifest: dict | None = None) -> list[str]:
    """验证发行文档的内部链接；源码参考必须显式标明且不能掩盖包内缺失。"""
    bundle = bundle.resolve()
    if manifest is None:
        manifest = json.loads((bundle / "70-agent/00-manifest.json").read_text(encoding="utf-8"))
    paths = [bundle / entry["path"] for entry in manifest["documents"]]
    layers = {path.relative_to(bundle).parts[0] for path in paths}
    problems = []
    for path in paths:
        if not path.is_file():
            problems.append(f"清单文件缺失 {path.relative_to(bundle).as_posix()}")
            continue
        if path.suffix != ".md":
            continue
        text = body_without_code(read(path))
        for match in LINK.finditer(text):
            raw = match.group(1)
            if raw.startswith(("http://", "https://", "mailto:")):
                continue
            target, _, anchor = raw.partition("#")
            resolved = (path.parent / target).resolve() if target else path
            internal = any(resolved.is_relative_to(bundle / layer) for layer in layers)
            if not internal:
                if not text[match.end():].startswith("（仅源码检出）"):
                    problems.append(f"发行引用未标明源码范围 {path.relative_to(bundle).as_posix()} -> {raw}")
                continue
            if not resolved.exists():
                problems.append(f"发行链接不可达 {path.relative_to(bundle).as_posix()} -> {raw}")
            elif anchor and resolved.suffix == ".md":
                headings = {slug(m.group(2)) for m in HEADING.finditer(body_without_code(read(resolved)))}
                if anchor not in headings:
                    problems.append(f"发行锚点失效 {path.relative_to(bundle).as_posix()} -> {raw}")
    return problems


def main() -> int:
    global DOCS
    parser = argparse.ArgumentParser(description="docs 静态检查")
    parser.add_argument("--quiet", action="store_true", help="只打印失败项")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--admin", action="store_true", help="检查独立 admin 文档，需检出在主工程 admin 下")
    mode.add_argument("--bundle", type=pathlib.Path, help="检查已生成的发行文档目录")
    args = parser.parse_args()
    if args.bundle is not None:
        problems = check_bundle(args.bundle)
        for problem in problems:
            print(problem)
        print(f"发行文档链接问题 {len(problems)} 处")
        return 1 if problems else 0
    if args.admin:
        DOCS = REPO / "admin/docs"
    if not DOCS.is_dir():
        parser.error(f"文档目录不存在：{DOCS}")

    files = sorted(DOCS.rglob("*.md"))
    link_problems, inbound = check_links(files)
    groups: dict[str, list[str]] = {
        "死链与锚点": link_problems,
        "孤儿文档": check_orphans(files, inbound),
        "入口可达": check_unreachable(files),
        "一级标题": check_titles(files),
        "编号冲突": check_duplicate_numbers(files),
        "编号前缀": check_number_prefixes(),
        "索引覆盖": check_index_coverage(),
    }
    total = 0
    for title, problems in groups.items():
        total += len(problems)
        if problems:
            print(f"== {title}（{len(problems)}）==")
            for item in problems:
                print(f"  {item}")
    if not args.quiet:
        print(f"检查 {len(files)} 个文档，问题 {total} 处")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
