"""测试用户可升级到同系列正式版，普通用户不会收到测试包提示。"""
import pytest
from packaging.version import InvalidVersion

from lvjiang.core import update
from lvjiang.core.versioning import (
    application_version,
    config_version,
    update_namespace,
)


def test_standard_order_and_invalid_versions_do_not_prompt(monkeypatch):
    versions = ["0.13.14", "0.15.0b2", "0.15.0b10", "0.15.0rc1", "0.15.0", "0.15.0.1"]
    assert all(update.is_newer_version(right, left) for left, right in zip(versions, versions[1:], strict=False))
    monkeypatch.setattr(update, "get_version", lambda: "0.15.0rc1")
    monkeypatch.setattr(update, "get_skip_version", lambda: "")
    assert update.should_prompt_update("0.15.0")
    assert not update.is_newer_version("unknown", "0.15.0")
    assert not update.is_newer_version("0.15.0", "unknown")
    with pytest.raises(InvalidVersion):
        application_version("0.15.0-beta.1")


def test_release_response_excludes_test_packages():
    assert update.parse_release_info({"tag_name": "0.15.0", "prerelease": True}) is None
    assert update.parse_release_info({"tag_name": "0.15.0", "draft": True}) is None
    assert update.parse_release_info({"tag_name": "0.15.0rc1", "prerelease": False}) is None
    release = update.parse_release_info({"tag_name": "v0.15.0"})
    assert release is not None and release.version == "0.15.0"


def test_config_range_and_archive_namespace_have_distinct_semantics():
    assert config_version("0.15.0b1") == application_version("0.15.0")
    assert config_version("0.15.0.1") == application_version("0.15.0")
    assert update_namespace("0.15.0b1") != update_namespace("0.15.0")
    assert update_namespace("0.15.0.1") == update_namespace("0.15.0")
