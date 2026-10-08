"""应用升级不能要求未修改的文档随之刷新日期和版本。"""
from scripts import docs_check


def test_document_baseline_can_remain_at_its_last_review(tmp_path, monkeypatch):
    directory = tmp_path / "docs"
    document = directory / "20-requirements" / "example.md"
    document.parent.mkdir(parents=True)
    text = "# 示例需求\n\n> 状态：已实现（2025-01-01，基线 v0.1.0）。\n"
    document.write_text(text, encoding="utf-8")
    monkeypatch.setattr(docs_check, "DOCS", directory)
    assert docs_check.check_status([document]) == []
    assert document.read_text(encoding="utf-8") == text
