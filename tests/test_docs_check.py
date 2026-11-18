"""应用升级不能要求未修改的文档随之刷新日期和版本。"""
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


def test_document_baseline_can_remain_at_its_last_review(tmp_path, monkeypatch):
    directory = tmp_path / "docs"
    document = directory / "20-requirements" / "example.md"
    document.parent.mkdir(parents=True)
    text = "# 示例需求\n\n> 状态：已实现（2025-01-01，基线 v0.1.0）。\n"
    document.write_text(text, encoding="utf-8")
    monkeypatch.setattr(docs_check, "DOCS", directory)
    assert docs_check.check_status([document]) == []
    assert document.read_text(encoding="utf-8") == text


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
