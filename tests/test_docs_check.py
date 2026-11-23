"""文档门禁只保链接与索引：生命周期看目录，修改时间看 Git。"""
from scripts import docs_check


def test_live_index_reports_unlisted_chapter(tmp_path, monkeypatch):
    directory = tmp_path / "docs" / "design" / "architecture"
    directory.mkdir(parents=True)
    (directory / "README.md").write_text("# 架构\n", encoding="utf-8")
    (directory / "01-example.md").write_text("# 示例\n", encoding="utf-8")
    monkeypatch.setattr(docs_check, "DOCS", directory.parent.parent)
    assert docs_check.check_index_coverage() == [
        "索引漏登记 design/architecture/README.md -> 01-example.md"]


def test_bundle_checks_internal_links_even_when_marked_source_only(tmp_path):
    import json

    directory = tmp_path / "70-agent"
    directory.mkdir()
    (directory / "00-manifest.json").write_text(json.dumps({
        "documents": [{"path": "70-agent/README.md"}],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    page = directory / "README.md"
    page.write_text(
        "# 示例\n[源码](../../src/example.py)（仅源码检出）\n"
        "[缺失章节](missing.md)（仅源码检出）\n", encoding="utf-8")
    assert docs_check.check_bundle_links(tmp_path) == [
        "发行链接不可达 70-agent/README.md -> missing.md"]


def test_titles_gate_requires_blank_line_after_heading(tmp_path, monkeypatch):
    """标题紧贴正文是最容易复发的排版退化：新文档一律要在标题后留空行。"""
    docs = tmp_path / "docs"
    docs.mkdir()
    good = docs / "01-good.md"
    good.write_text("# 标题\n\n> 状态：已实现。\n", encoding="utf-8")
    tight = docs / "02-tight.md"
    tight.write_text("# 标题\n> 状态：已实现。\n", encoding="utf-8")
    monkeypatch.setattr(docs_check, "DOCS", docs)
    assert docs_check.check_titles([good, tight]) == ["一级标题后缺空行 02-tight.md"]


def test_number_prefixes_gate_with_releases_exemption(tmp_path, monkeypatch):
    """编号顶层目录的子目录与非 README 文件必须带数字前缀；50-releases 顶层
    允许版本号（v<数字>）排序——这是目录编号化迁移后的常驻门禁。"""
    docs = tmp_path / "docs"
    for rel in (
        "10-domain/01-yysls",          # 已编号目录：放行
        "10-domain/legacy",            # 未编号子目录：拦截
        "20-plans",                    # 未编号文件：拦截
        "50-releases",                 # 版本号顶层条目：豁免
    ):
        (docs / rel).mkdir(parents=True)
    (docs / "10-domain/01-yysls/README.md").write_text("# x\n", encoding="utf-8")
    (docs / "20-plans/README.md").write_text("# x\n", encoding="utf-8")
    (docs / "20-plans/backlog.md").write_text("# x\n", encoding="utf-8")
    (docs / "50-releases/v0.15.17.md").write_text("# x\n", encoding="utf-8")
    (docs / "50-releases/v-notes.md").write_text("# x\n", encoding="utf-8")
    monkeypatch.setattr(docs_check, "DOCS", docs)
    assert docs_check.check_number_prefixes() == [
        "缺编号前缀 10-domain/legacy",
        "缺编号前缀 20-plans/backlog.md",
        "缺编号前缀 50-releases/v-notes.md",
    ]
