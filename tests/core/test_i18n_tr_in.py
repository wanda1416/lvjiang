"""``tr_in()`` 按指定语言查表，且不改变进程语言。

它专门服务「已保存但尚未重启」的提示：此刻进程仍跑在旧语言下，用 ``tr()``
查出来的会是用户刚换掉的那种语言。
"""

import pytest

from lvjiang import i18n


@pytest.fixture(autouse=True)
def _restore_i18n_state():
    """init_i18n 会改模块级全局，用例结束后还原，避免污染其它用例。"""
    saved_language = i18n._current_language
    saved_translations = dict(i18n._translations)
    try:
        yield
    finally:
        i18n._current_language = saved_language
        i18n._translations.clear()
        i18n._translations.update(saved_translations)


def test_looks_up_target_language_without_switching_process_language():
    i18n.init_i18n("zh_CN")
    assert i18n.tr_in("重启应用后生效", "en_US") == (
        "Takes effect after restarting the app")
    # 进程语言与全局表都不能被带动
    assert i18n.current_language() == "zh_CN"
    assert i18n.tr("重启应用后生效") == "重启应用后生效"


def test_chinese_returns_source_text():
    i18n.init_i18n("en_US")
    assert i18n.tr_in("重启应用后生效", "zh_CN") == "重启应用后生效"


def test_missing_entry_falls_back_to_source():
    """词表没这条时回退中文原文，不能返回空串。"""
    assert i18n.tr_in("词表里没有这句", "en_US") == "词表里没有这句"
