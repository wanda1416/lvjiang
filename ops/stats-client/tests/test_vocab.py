"""stats_client.vocab 读快照文件的边界行为。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stats_client import vocab  # noqa: E402


class TestLoadVocab:
    def test_loads_real_committed_snapshot(self):
        data = vocab.load_vocab()
        assert len(data["parts"]) == 7
        assert "leg" in data["parts"]
        assert data["normal_affixes"]  # 非空

    def test_missing_file_returns_empty_shape_not_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vocab, "_VOCAB_PATH", tmp_path / "nope.json")
        data = vocab.load_vocab()
        assert data["parts"] == []
        assert data["normal_affixes"] == []

    def test_corrupt_file_returns_empty_shape_not_raises(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(vocab, "_VOCAB_PATH", bad)
        data = vocab.load_vocab()
        assert data["parts"] == []


class TestAffixesForPart:
    def test_no_part_returns_full_normal_affix_list(self):
        data = vocab.load_vocab()
        assert vocab.affixes_for_part(data, None) == data["normal_affixes"]

    def test_part_returns_only_that_parts_affixes(self):
        data = vocab.load_vocab()
        leg_affixes = vocab.affixes_for_part(data, "leg")
        assert "最大无相攻击" not in leg_affixes  # 武器限定词条
        assert "最大外功攻击" in leg_affixes

    def test_unknown_part_returns_empty_list(self):
        data = vocab.load_vocab()
        assert vocab.affixes_for_part(data, "not-a-real-part") == []
