"""i18n 核心模块单元测试

测试翻译加载、tr() 查找、fallback 机制和语言列表扫描。
"""
from __future__ import annotations

import pytest

# ── fixture：每个测试前后重置 i18n 全局状态 ──

@pytest.fixture(autouse=True)
def _reset_i18n():
    """避免测试间全局状态污染"""
    import lvjiang.i18n as mod
    original_trans = mod._translations
    original_lang = mod._current_language
    yield
    mod._translations = original_trans
    mod._current_language = original_lang


# ── 基础功能 ──

class TestTrZhCN:
    """zh_CN 模式下 tr() 直接返回原文，零开销"""

    def test_returns_original(self):
        from lvjiang.i18n import init_i18n, tr
        init_i18n("zh_CN")
        assert tr("开始执行") == "开始执行"

    def test_returns_original_for_any_text(self):
        from lvjiang.i18n import init_i18n, tr
        init_i18n("zh_CN")
        assert tr("配置管理") == "配置管理"
        assert tr("任意文本") == "任意文本"
        assert tr("") == ""


class TestTrEnUS:
    """en_US 模式下 tr() 返回英文翻译"""

    def test_known_translation(self):
        from lvjiang.i18n import init_i18n, tr
        init_i18n("en_US")
        # zh_CN.yaml 中 main_window.menu_settings = "配置管理"
        # en_US.yaml 中同路径 = "Settings"
        assert tr("配置管理") == "Settings"

    def test_another_translation(self):
        from lvjiang.i18n import init_i18n, tr
        init_i18n("en_US")
        assert tr("保存") == "Save"

    def test_fallback_to_original(self):
        """未翻译的条目返回原文"""
        from lvjiang.i18n import init_i18n, tr
        init_i18n("en_US")
        assert tr("这是一个未翻译的字符串") == "这是一个未翻译的字符串"


class TestFormatStrings:
    """含变量的翻译条目：先 tr() 再 .format()"""

    def test_format_with_variables(self):
        from lvjiang.i18n import init_i18n, tr
        init_i18n("en_US")
        result = tr("发现新版本 v{latest}\n当前版本: v{current}\n\n是否前往下载？").format(
            latest="2.0", current="1.0"
        )
        assert "2.0" in result
        assert "1.0" in result
        assert "Download" in result


# ── 语言列表 ──

class TestAvailableLanguages:
    def test_contains_zh_cn(self):
        from lvjiang.i18n import available_languages
        langs = available_languages()
        assert any(lang["code"] == "zh_CN" for lang in langs)

    def test_contains_en_us(self):
        from lvjiang.i18n import available_languages
        langs = available_languages()
        assert any(lang["code"] == "en_US" for lang in langs)

    def test_display_names(self):
        from lvjiang.i18n import available_languages
        langs = available_languages()
        names = {lang["code"]: lang["name"] for lang in langs}
        assert names["zh_CN"] == "简体中文"
        assert names["en_US"] == "English"


# ── 翻译映射构建 ──

class TestBuildTranslationMap:
    def test_parallel_traversal(self):
        """验证并行递归正确建立映射"""
        from lvjiang.i18n import _build_translation_map
        zh = {"a": {"x": "中文A", "y": "中文B"}, "b": "中文C"}
        en = {"a": {"x": "EnglishA", "y": "EnglishB"}, "b": "EnglishC"}
        result = {}
        _build_translation_map(zh, en, result)
        assert result == {"中文A": "EnglishA", "中文B": "EnglishB", "中文C": "EnglishC"}

    def test_misaligned_structure(self):
        """结构不对齐时静默跳过"""
        from lvjiang.i18n import _build_translation_map
        zh = {"a": {"x": "中文A"}}
        en = {"a": {}, "b": "EnglishB"}  # a.x 缺失，b 在 zh 中不存在
        result = {}
        _build_translation_map(zh, en, result)
        assert result == {}  # 没有匹配

    def test_meta_skipped(self):
        """meta 节点不参与映射"""
        from lvjiang.i18n import _build_translation_map
        zh = {"meta": {"language": "zh_CN"}, "key": "值"}
        en = {"meta": {"language": "en_US"}, "key": "Value"}
        result = {}
        _build_translation_map(zh, en, result)
        assert "zh_CN" not in result
        assert result == {"值": "Value"}


# ── 初始化 ──

class TestInitI18n:
    def test_returns_language_code(self):
        from lvjiang.i18n import init_i18n
        assert init_i18n("zh_CN") == "zh_CN"
        assert init_i18n("en_US") == "en_US"

    def test_nonexistent_language_fallback(self):
        """不存在的语言文件优雅降级"""
        from lvjiang.i18n import init_i18n, tr
        init_i18n("xx_YY")
        # 翻译表为空，tr() 返回原文
        assert tr("任意文本") == "任意文本"

    def test_current_language(self):
        from lvjiang.i18n import current_language, init_i18n
        init_i18n("en_US")
        assert current_language() == "en_US"
        init_i18n("zh_CN")
        assert current_language() == "zh_CN"
