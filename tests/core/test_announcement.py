"""GitHub Pages 公告协议、版本筛选与 Session 状态测试。"""
from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from lvjiang.core.announcement import (
    Announcement,
    AnnouncementError,
    AnnouncementManifest,
    applicable_notices,
    cache_manifest,
    fetch_announcement_manifest,
    get_last_notice_version,
    load_cached_manifest,
    manifest_to_dict,
    mark_notice_version,
    notice_applies,
    parse_announcement_manifest,
    should_prompt_manifest,
)
from lvjiang.core.config.session import reset_session_store


@pytest.fixture(autouse=True)
def session_env(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    yield
    reset_session_store()


def manifest(version: int = 3) -> AnnouncementManifest:
    return AnnouncementManifest(
        schema_version=1,
        notice_version=version,
        updated_at="2026-08-24T10:00:00Z",
        notices=(
            Announcement(
                id="critical-1",
                level="critical",
                title="严重问题",
                body="请暂停使用相关功能。",
                min_app_version="0.5.1",
                max_app_version_exclusive="0.5.2",
                url="https://github.com/wanda1416/lvjiang/issues/1",
            ),
        ),
    )


def test_parse_manifest_and_roundtrip():
    parsed = parse_announcement_manifest(manifest_to_dict(manifest()))

    assert parsed.notice_version == 3
    assert parsed.notices[0].level == "critical"
    assert parsed.notices[0].max_app_version_exclusive == "0.5.2"


@pytest.mark.parametrize("patch", [
    {"schema_version": 2},
    {"notice_version": "3"},
    {"notices": "bad"},
])
def test_invalid_manifest_is_rejected(patch):
    data = manifest_to_dict(manifest())
    data.update(patch)
    with pytest.raises(AnnouncementError):
        parse_announcement_manifest(data)


def test_duplicate_ids_and_non_https_urls_are_rejected():
    data = manifest_to_dict(manifest())
    data["notices"].append(dict(data["notices"][0]))
    with pytest.raises(AnnouncementError, match="id 重复"):
        parse_announcement_manifest(data)

    data = manifest_to_dict(manifest())
    data["notices"][0]["url"] = "http://example.test/unsafe"
    with pytest.raises(AnnouncementError, match="HTTPS"):
        parse_announcement_manifest(data)


def test_version_range_is_min_inclusive_max_exclusive():
    notice = manifest().notices[0]

    assert notice_applies(notice, "0.5.1")
    assert notice_applies(notice, "0.5.1.1")
    assert not notice_applies(notice, "0.5.0")
    assert not notice_applies(notice, "0.5.2")


def test_inactive_notice_is_not_applicable():
    inactive = Announcement(
        id="old", level="info", title="旧公告", body="内容", active=False)
    data = AnnouncementManifest(1, 1, "", (inactive,))

    assert applicable_notices(data, "0.5.1") == ()


def test_cache_does_not_mark_prompted_and_preserves_server_config():
    from lvjiang.core.config.session import get_session_store
    store = get_session_store()
    store.update_node("server_config", {"skip_version": "0.6.0"})

    cache_manifest(manifest(), etag='"abc"')

    assert load_cached_manifest() == manifest()
    assert get_last_notice_version() == 0
    server = store.get_node("server_config")
    assert server["skip_version"] == "0.6.0"
    assert server["announcement"]["etag"] == '"abc"'


def test_prompt_only_once_after_marking():
    data = manifest()

    assert should_prompt_manifest(data, "0.5.1")
    mark_notice_version(3)
    assert get_last_notice_version() == 3
    assert not should_prompt_manifest(data, "0.5.1")
    assert should_prompt_manifest(manifest(4), "0.5.1")


def test_mark_notice_version_never_moves_backwards():
    mark_notice_version(5)
    mark_notice_version(3)
    assert get_last_notice_version() == 5


def test_new_manifest_without_applicable_notice_does_not_prompt():
    assert not should_prompt_manifest(manifest(), "0.6.0")


class FakeResponse:
    def __init__(self, payload: bytes, etag: str = '"v3"'):
        self._stream = BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload)), "ETag": etag}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


def test_fetch_manifest_uses_static_json_and_etag(monkeypatch):
    payload = json.dumps(manifest_to_dict(manifest())).encode()
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["etag"] = request.get_header("If-none-match")
        captured["timeout"] = timeout
        return FakeResponse(payload)

    monkeypatch.setattr("lvjiang.core.announcement.urlopen", fake_urlopen)

    result = fetch_announcement_manifest(etag='"old"', timeout=2.5)

    assert captured == {
        "url": "https://wanda1416.github.io/lvjiang/notices.json",
        "etag": '"old"',
        "timeout": 2.5,
    }
    assert result.manifest == manifest()
    assert result.etag == '"v3"'
    assert result.not_modified is False


def test_fetch_304_returns_cached_manifest(monkeypatch):
    cached = manifest()

    def not_modified(*args, **kwargs):
        raise HTTPError("url", 304, "Not Modified", {}, None)

    monkeypatch.setattr("lvjiang.core.announcement.urlopen", not_modified)

    result = fetch_announcement_manifest(etag='"v3"', cached=cached)

    assert result.manifest is cached
    assert result.not_modified is True


def test_fetch_rejects_oversized_response(monkeypatch):
    payload = b"x" * (256 * 1024 + 1)
    monkeypatch.setattr(
        "lvjiang.core.announcement.urlopen",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    with pytest.raises(AnnouncementError, match="256 KB"):
        fetch_announcement_manifest()
