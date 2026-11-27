"""应用内更新的 APK 校验入口：三态结论与失败分支。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvjiang.core.ondevice import update_api


def _call(path: Path | str, version: str = "1.2.3") -> dict:
    return json.loads(update_api.verify_apk(str(path), version))


def test_missing_file_is_reported(tmp_path: Path) -> None:
    result = _call(tmp_path / "nope.apk")

    assert result["ok"] is False
    assert "重新下载" in result["message"]


def test_matching_checksum_passes(tmp_path: Path, monkeypatch) -> None:
    apk = tmp_path / "lvjiang-v1.2.3.apk"
    apk.write_bytes(b"payload")
    monkeypatch.setattr(
        "lvjiang.core.android.apk_release.fetch_expected_sha256", lambda *_a, **_k: None)

    result = _call(apk)

    assert result["ok"] is True
    assert result["size"] == len(b"payload")
    assert len(result["sha256"]) == 64


def test_checksum_mismatch_is_reported_as_not_verified(tmp_path: Path, monkeypatch) -> None:
    apk = tmp_path / "lvjiang-v1.2.3.apk"
    apk.write_bytes(b"payload")
    monkeypatch.setattr(
        "lvjiang.core.android.apk_release.fetch_expected_sha256",
        lambda *_a, **_k: "0" * 64,
    )

    result = _call(apk)

    assert result["ok"] is True
    assert result["verified"] is False
    assert "不匹配" in result["message"]


def test_unreachable_sums_degrade_to_null(tmp_path: Path, monkeypatch) -> None:
    apk = tmp_path / "lvjiang-v1.2.3.apk"
    apk.write_bytes(b"payload")
    monkeypatch.setattr(
        "lvjiang.core.android.apk_release.fetch_expected_sha256", lambda *_a, **_k: None)

    result = _call(apk)

    assert result["ok"] is True
    assert result["verified"] is None
    assert "继续安装" in result["message"]


@pytest.mark.parametrize("version", ["", "9.9.9"])
def test_unknown_version_does_not_crash(tmp_path: Path, monkeypatch, version: str) -> None:
    apk = tmp_path / "lvjiang-v1.2.3.apk"
    apk.write_bytes(b"payload")
    monkeypatch.setattr(
        "lvjiang.core.android.apk_release.fetch_expected_sha256", lambda *_a, **_k: None)

    result = _call(apk, version)

    assert result["ok"] is True
    assert result["verified"] is None
