"""文档健康检查：死链、锚点、孤儿、编号冲突、一级标题、时效锚与状态行。

用法：

    python scripts/docs_check.py            # 全量检查，有问题时退出码 1

检查范围是 `docs/` 下全部 `.md`；链接目标可以是仓库内任意文件（源码、配置、
打包手册都允许被引用）。纯静态检查，不联网、不依赖 Qt，可以直接进 CI。

约定见 `docs/00-meta/00-doc-standard.md`：层索引写明基线，会被实现进度推翻的
文档（20-requirements、平台进度）写明状态，归档文档写明「状态止于」。
日期与基线记录实际内容核对的时间和版本，不要求跟随应用发版更新；本脚本只读检查。
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
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
#: 时效锚：正文前 20 行里出现版本号或日期即可
FRESH = re.compile(r"v?\d+\.\d+\.\d+|20\d\d-\d\d-\d\d|20\d\d\s*年")
#: 每层索引文件必须覆盖同目录全部 .md
INDEX_DIRS = ("40-development/2026-07", "40-development/2026-08",
              "40-development/2026-09", "50-releases")
SKIP_PREFIX = ("http://", "https://", "mailto:", "#")

#: 状态行：会被实现进度推翻的文档必须写明状态与基线（约定见 00-doc-standard.md）
STATUS_SCOPES = ("20-requirements", "00-meta/platforms")
STATUS_INDEXES = ("20-requirements/README.md", "00-meta/platforms/README.md")
STATUS_LINE = re.compile(
    r"^>\s*状态\s*[：:]\s*(已实现|部分实现|规划)"
    r"\s*（(\d{4}-\d{2}-\d{2})，基线\s*v?(\d+\.\d+(?:\.\d+)?)）", re.M)
ARCHIVE_LINE = re.compile(r"^>\s*状态止于\s*\d{4}-\d{2}-\d{2}", re.M)
#: 状态行与时效锚都只看正文最前面这一段
HEAD_LINES = 15


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


def check_orphans(files: list[pathlib.Path], inbound: set[pathlib.Path]) -> list[str]:
    return [
        f"孤儿文档（无任何入链） {path.relative_to(REPO).as_posix()}"
        for path in files
        if path.name != "README.md" and path.resolve() not in inbound
    ]


#: 形如 `# src/lvjiang/apps/base.py` 的“标题”是代码块的文件名标注，不算标题
CODE_CAPTION = re.compile(r"^[^\s]*[./\\][^\s]*$")


def check_titles(files: list[pathlib.Path]) -> list[str]:
    problems: list[str] = []
    for path in files:
        titles = [title for title in H1.findall(body_without_code(read(path)))
                  if not CODE_CAPTION.match(title.strip())]
        if len(titles) != 1:
            rel = path.relative_to(REPO).as_posix()
            problems.append(f"一级标题数量 {len(titles)} 处 {rel}")
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


def check_freshness(files: list[pathlib.Path]) -> list[str]:
    """各层 README 必须写明基线版本或最后更新日期。

    页面级不强制——单个页面里的版本号会随内容变化，只有层索引承担
    “当前基线在哪”的责任；约定见 docs/00-meta/00-doc-standard.md。
    """
    problems: list[str] = []
    for path in files:
        if path.name != "README.md":
            continue
        head = "\n".join(read(path).splitlines()[:20])
        if not FRESH.search(head):
            problems.append(
                f"层索引缺时效锚（前 20 行无版本号或日期）"
                f" {path.relative_to(DOCS).as_posix()}")
    return problems


def status_docs(files: list[pathlib.Path]) -> list[pathlib.Path]:
    """需要状态行的文档：20-requirements 全部与平台进度文档，层索引除外。"""
    out: list[pathlib.Path] = []
    for path in files:
        rel = path.relative_to(DOCS).as_posix()
        if rel in STATUS_INDEXES or "/archive/" in rel:
            continue
        if any(rel.startswith(scope + "/") for scope in STATUS_SCOPES):
            out.append(path)
    return out


def head_of(path: pathlib.Path) -> str:
    return "\n".join(read(path).splitlines()[:HEAD_LINES])


def check_status(files: list[pathlib.Path]) -> list[str]:
    """活文档写状态行（含基线版本），归档文档写「状态止于」。

    状态行把「已实现 / 部分实现 / 规划」与基线版本钉在文档开头，避免再出现
    “文档写着待实现、代码早已上线”这类腐烂。基线是文档实际核对版本，
    允许早于应用版本，不以应用发版要求重写未修改文档。
    """
    problems: list[str] = []
    for path in status_docs(files):
        rel = path.relative_to(DOCS).as_posix()
        match = STATUS_LINE.search(head_of(path))
        if not match:
            problems.append(
                "缺状态行（前 15 行需 `> 状态：已实现|部分实现|规划（YYYY-MM-DD，基线 vX.Y.Z）`）"
                f" {rel}")
    for path in files:
        rel = path.relative_to(DOCS).as_posix()
        if "/archive/" not in rel or path.name == "README.md":
            continue
        if not ARCHIVE_LINE.search(head_of(path)):
            problems.append(f"归档缺「状态止于」行（前 15 行） {rel}")
    return problems


def check_index_status(files: list[pathlib.Path]) -> list[str]:
    """20-requirements 层索引的状态列必须与目标文档的状态行一致。

    索引给人快速扫的，文档首行是权威；两边不一致就是有一处已经腐烂。
    """
    problems: list[str] = []
    index = DOCS / "20-requirements/README.md"
    if not index.is_file():
        return problems
    for line in read(index).splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        link = re.search(r"\]\(([^)#]+\.md)\)", cells[0])
        if not link:
            continue
        target = (index.parent / link.group(1)).resolve()
        if not target.is_file():
            continue
        rel = target.relative_to(DOCS).as_posix()
        doc = STATUS_LINE.search(head_of(target))
        cell = cells[-1].lstrip("✅").strip()
        if not doc:
            problems.append(f"索引状态无法比对（目标缺状态行） {rel}")
        elif not cell.startswith(doc.group(1)):
            problems.append(
                f"索引状态与文档不一致 {rel}: 索引「{cells[-1]}」/ 文档「{doc.group(1)}」")
    return problems


def check_index_coverage() -> list[str]:
    problems: list[str] = []
    for name in INDEX_DIRS:
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
        for path in sorted(directory.glob("*.md")):
            if path.name != "README.md" and path.resolve() not in linked:
                problems.append(
                    f"索引漏登记 {name}/README.md -> {path.name}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="docs 静态检查")
    parser.add_argument("--quiet", action="store_true", help="只打印失败项")
    args = parser.parse_args()

    files = sorted(DOCS.rglob("*.md"))
    link_problems, inbound = check_links(files)
    groups: dict[str, list[str]] = {
        "死链与锚点": link_problems,
        "孤儿文档": check_orphans(files, inbound),
        "一级标题": check_titles(files),
        "编号冲突": check_duplicate_numbers(files),
        "时效锚": check_freshness(files),
        "状态行": check_status(files),
        "索引状态": check_index_status(files),
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
