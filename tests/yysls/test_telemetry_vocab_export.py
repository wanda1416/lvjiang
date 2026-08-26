"""校验 ops/stats-client/vocab/telemetry_vocab.json 与 game_config.yaml 同步。

这份 JSON 是 ``scripts/export_yysls_vocab.py`` 的输出，给 ``ops/stats-client``
读——那边是零 GUI 依赖的独立 venv，直接 import ``lvjiang.apps.yysls`` 会经
``apps/yysls/__init__.py`` 强制拉 PyQt6（``i18n`` 模块要用），与"轻量网页
不该装一个 GUI 库"这条设计前提冲突，所以改成读快照。放在
``ops/stats-client/`` 下而不是 ``config/system/``：它是 stats-client 私有
的构建产物，不经 ``ConfigResolver`` 合并，不该放进配置分层目录。

快照的代价是可能过期：改了 ``game_config.yaml``（新词条、新武器类型……）
忘记重新跑导出脚本，下游就会读到一份 stale 数据。这个测试把导出逻辑重新
跑一遍在内存里比对，忘记导出时这里会红，而不是让下游静默用错数据。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import export_yysls_vocab  # noqa: E402

_VOCAB_JSON = _REPO_ROOT / "ops/stats-client/vocab/telemetry_vocab.json"


class TestVocabExportUpToDate:
    def test_committed_json_matches_freshly_built_vocab(self):
        assert _VOCAB_JSON.exists(), (
            f"{_VOCAB_JSON} 不存在——先跑一遍 "
            "`python scripts/export_yysls_vocab.py`")
        committed = json.loads(_VOCAB_JSON.read_text(encoding="utf-8"))
        fresh = export_yysls_vocab.build_vocab()
        assert committed == fresh, (
            "ops/stats-client/vocab/telemetry_vocab.json 与 game_config.yaml "
            "不同步——改了 game_config.yaml 之后要重新跑一遍 "
            "`python scripts/export_yysls_vocab.py` 并把结果一起提交。")

    def test_snapshot_has_all_seven_parts_with_labels(self):
        data = json.loads(_VOCAB_JSON.read_text(encoding="utf-8"))
        assert len(data["parts"]) == 7
        assert set(data["parts"]) == set(data["part_labels"])
        # 武器组的标签必须是通用的"武器"，不能是某个具体武器型号（见
        # export_yysls_vocab._part_labels 的注释）
        assert data["part_labels"]["weapon"] == "武器"

    def test_normal_affixes_by_part_only_uses_known_parts_and_affixes(self):
        data = json.loads(_VOCAB_JSON.read_text(encoding="utf-8"))
        assert set(data["normal_affixes_by_part"]) == set(data["parts"])
        all_normal = set(data["normal_affixes"])
        for affixes in data["normal_affixes_by_part"].values():
            assert set(affixes) <= all_normal
