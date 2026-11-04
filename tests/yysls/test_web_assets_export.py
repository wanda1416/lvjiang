"""网页端毕业率资产必须与游戏配置同步：忘了重新导出时测试要红，而不是让网页
继续拿旧规则算毕业率。

资产落在 ``server/web/public/assets/yysls/``（本仓是嵌套的独立仓库），单独检出
本仓时该目录不存在，此时跳过——漂移检测只在两端都在的完整工作副本里有意义。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ASSET_DIR = _REPO_ROOT / "server/web/public/assets/yysls"

sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import export_yysls_web_assets as export  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (_ASSET_DIR / "config.json").is_file(),
    reason="未检出 server/web 资产目录（嵌套仓库不在当前工作副本）",
)


def test_committed_config_matches_game_config() -> None:
    """规则表与当前 game_config 一致（新增/改动词条、等级、套装后要重跑导出）。"""
    expected = export._dump(export.build_config())
    assert (_ASSET_DIR / "config.json").read_bytes() == expected


def test_manifest_covers_every_model_shard() -> None:
    """manifest 与分片一一对应：不能有登记了却不存在的模型，也不能有孤儿分片。"""
    manifest = json.loads((_ASSET_DIR / "manifest.json").read_text(encoding="utf-8"))
    listed = {entry["path"] for entry in manifest["models"]}
    on_disk = {f"models/{path.name}" for path in (_ASSET_DIR / "models").glob("*.json")}
    assert listed == on_disk
    for entry in manifest["models"]:
        payload = (_ASSET_DIR / entry["path"]).read_bytes()
        assert len(payload) == entry["bytes"]
        assert json.loads(payload)["school"] == entry["school"]


def test_check_mode_reports_drift(tmp_path) -> None:
    """``--check`` 是提交前的漂移门禁：产物缺失或过期必须返回非零。"""
    assert export.main(["--dest", str(tmp_path), "--check"]) == 1
    export.main(["--dest", str(tmp_path)])
    assert export.main(["--dest", str(tmp_path), "--check"]) == 0
    config = tmp_path / "config.json"
    config.write_text("{}\n", encoding="utf-8")
    assert export.main(["--dest", str(tmp_path), "--check"]) == 1
