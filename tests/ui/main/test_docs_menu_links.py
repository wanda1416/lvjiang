"""帮助/反馈入口的公开仓文档 URL 契约：真实实现生成 URL，只拦截最终 openUrl。"""
from __future__ import annotations

import pytest

from lvjiang.core.about.links import GITHUB_REPO
from lvjiang.ui.main.menu_ops import MenuOpsMixin
from lvjiang.ui.notices.feedback_dialog import FeedbackDialog


@pytest.fixture
def opened_urls(monkeypatch):
    urls: list[str] = []

    def fake_open_url(url):
        urls.append(url.toString())
        return True

    monkeypatch.setattr("PyQt6.QtGui.QDesktopServices.openUrl", fake_open_url)
    return urls


def test_open_docs_targets_the_public_guide_entry(qtbot, opened_urls):
    """主窗口「帮助 → 文档」必须打开公开仓的新用户指南入口 docs/60-guide/README.md。"""

    class _Stub(MenuOpsMixin):
        pass

    _Stub()._open_docs()
    assert opened_urls == [
        f"https://github.com/{GITHUB_REPO}/blob/master/docs/60-guide/README.md"]


def test_feedback_dialog_issue_guide_targets_the_faq_page(qtbot, opened_urls):
    """反馈对话框「提交规范」必须指向迁移后的常见问题与反馈页，旧路径不得复现。"""
    FeedbackDialog._open_issue_guide()
    assert opened_urls == [
        f"https://github.com/{GITHUB_REPO}/blob/master/docs/60-guide/desktop/09-faq-and-feedback.md"]
