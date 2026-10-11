"""索引选择必须按应用系列隔离，且不同部署代次不能组成可安装包。"""
import pytest

from lvjiang.core.config.update_download import fetch_public_update
from lvjiang.core.update_bundle import (
    PUBLIC_INDEX_URL,
    digest,
    json_bytes,
    make_manifest,
)


def test_exact_series_selection_and_consistent_artifacts():
    manifest, archive = make_manifest("0.15.0", 2, {}, {"new.bin": b"\xff"})
    raw_manifest = json_bytes(manifest)
    index = {"schema_version": 1, "series": {"0.15.0": {
        "revision": 2, "manifest": "v0.15.0/r2/manifest.json", "sha256": digest(raw_manifest)}}}
    root = PUBLIC_INDEX_URL.rsplit("/", 1)[0]
    files = {f"{root}/index.json": json_bytes(index),
             f"{root}/v0.15.0/r2/manifest.json": raw_manifest,
             f"{root}/v0.15.0/r2/update.zip": archive}
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return files[url], "", False

    result = fetch_public_update("0.15.0.1", fetch=fetch)
    assert result is not None and result.archive == archive
    calls.clear()
    assert fetch_public_update("0.15.1", fetch=fetch) is None
    assert calls == [f"{root}/index.json"]
    files[f"{root}/v0.15.0/r2/update.zip"] = b"partial upload"
    with pytest.raises(ValueError, match="摘要"):
        fetch_public_update("0.15.0", fetch=fetch)
