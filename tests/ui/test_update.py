"""版本检查与跳过版本逻辑测试"""

import json
from unittest.mock import patch

import pytest

from lvjiang.core.update import (
    get_skip_version,
    is_newer_version,
    parse_version,
    set_skip_version,
    should_prompt_update,
)


@pytest.fixture(autouse=True)
def cleanup_server_config(tmp_path, monkeypatch):
    """每个测试前后清理 session.json 的 server_config 节点"""
    import lvjiang.constants as constants_mod
    import lvjiang.core.config.session as store_mod
    path = tmp_path / "session.json"
    monkeypatch.setattr(constants_mod, "SESSION_PATH", path)
    store_mod.reset_session_store()
    yield
    store_mod.reset_session_store()


class TestParseVersion:
    def test_normal_version(self):
        assert parse_version("1.2.3") == [1, 2, 3]

    def test_two_part_version(self):
        assert parse_version("0.1") == [0, 1]

    def test_invalid_version(self):
        assert parse_version("invalid") == [0]

    def test_empty_string(self):
        assert parse_version("") == [0]


class TestIsNewerVersion:
    def test_newer_patch(self):
        assert is_newer_version("0.1.2", "0.1.0") is True

    def test_newer_minor(self):
        assert is_newer_version("0.2.0", "0.1.0") is True

    def test_newer_major(self):
        assert is_newer_version("1.0.0", "0.1.0") is True

    def test_same_version(self):
        assert is_newer_version("0.1.0", "0.1.0") is False

    def test_older_version(self):
        assert is_newer_version("0.1.0", "0.1.2") is False


class TestSkipVersion:
    def test_no_skip_version_initially(self):
        assert get_skip_version() == ""

    def test_set_and_get_skip_version(self):
        set_skip_version("0.1.2")
        assert get_skip_version() == "0.1.2"

    def test_config_stored_in_session(self, tmp_path, monkeypatch):
        """验证 skip_version 存储在 session.json 的 server_config 节点"""
        from lvjiang.core.config.session import get_session_store
        set_skip_version("0.1.2")
        store = get_session_store()
        node = store.get_node("server_config")
        assert node is not None
        assert node["skip_version"] == "0.1.2"


class TestShouldPromptUpdate:
    """should_prompt_update 需要同时考虑当前版本和跳过版本"""

    @patch("lvjiang.core.update.get_version", return_value="0.1.0")
    def test_should_prompt_when_newer(self, mock_ver):
        assert should_prompt_update("0.1.2") is True

    @patch("lvjiang.core.update.get_version", return_value="0.1.1")
    def test_should_not_prompt_when_same(self, mock_ver):
        # 当前 0.1.1，最新 0.1.1 → 不提示
        assert should_prompt_update("0.1.1") is False

    @patch("lvjiang.core.update.get_version", return_value="0.1.2")
    def test_should_not_prompt_when_older(self, mock_ver):
        # 当前 0.1.2，最新 0.1.1 → 不提示
        assert should_prompt_update("0.1.1") is False

    @patch("lvjiang.core.update.get_version", return_value="0.1.0")
    def test_should_not_prompt_when_skipped(self, mock_ver):
        # 当前 0.1.0，最新 0.1.2，已跳过 0.1.2 → 不提示
        set_skip_version("0.1.2")
        assert should_prompt_update("0.1.2") is False

    @patch("lvjiang.core.update.get_version", return_value="0.1.0")
    def test_should_prompt_when_newer_than_skip(self, mock_ver):
        # 当前 0.1.0，已跳过 0.1.2，最新 0.1.3 → 提示
        set_skip_version("0.1.2")
        assert should_prompt_update("0.1.3") is True
