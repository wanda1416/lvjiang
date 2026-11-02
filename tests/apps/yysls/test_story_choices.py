"""自动剧情文案库：归一化 / 相似度 / 匹配 / 多配置组双层读写。"""

from __future__ import annotations

import json

import pytest

from lvjiang.core.story import (
    DEFAULT_THRESHOLD,
    StoryDoc,
    StoryEntry,
    list_groups,
    load_all,
    load_group,
    match,
    normalize,
    set_group_threshold,
    similarity,
    upsert_entry,
)


def _fake_resolver(tmp_path, monkeypatch):
    from lvjiang.core.config.resolver import ConfigResolver

    system = tmp_path / "system"
    local = tmp_path / "local"
    system.mkdir(exist_ok=True)
    local.mkdir(exist_ok=True)
    fake = ConfigResolver(
        system_dir=system,
        local_dir=local,
        dev_mode=False,
        config_dir=tmp_path,
        track_content=False,
    )
    monkeypatch.setattr(
        "lvjiang.core.config.resolver.get_resolver", lambda: fake
    )
    return system, local


def _write_group(root, name: str, entries: list[dict]) -> None:
    target = root / "tools" / "auto_plot" / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"version": 1, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_normalize_strips_punctuation_and_fullwidth():
    assert normalize("为 守护 他人！") == "为守护他人"
    assert normalize("ＡＢＣ") == "abc"


def test_similarity_tolerates_ocr_noise():
    assert similarity("为守护他人", "为守护他人") == 1.0
    assert similarity("为守护他人", "为守沪他人") > 0.7
    assert similarity("为守护他人", "完全无关") < 0.3


def test_match_picks_entry_by_option_set():
    doc = StoryDoc(
        entries=[
            StoryEntry(id="a", options=("为守护他人", "为求长生", "为复仇"), answer="为守护他人"),
            StoryEntry(id="b", options=("北上", "南下"), answer="南下"),
        ]
    )
    result = match(doc, ["为守护他人", "为求长生", "为复仇"])
    assert result is not None
    assert result.entry.id == "a"
    assert result.answer_text == "为守护他人"


def test_match_returns_none_below_threshold():
    doc = StoryDoc(
        entries=[StoryEntry(id="a", options=("为守护他人", "为求长生"), answer="为守护他人")]
    )
    assert match(doc, ["完全无关的选项甲", "完全无关的选项乙"]) is None


def test_match_rejects_when_answer_missing_from_screen():
    """选项集合相似但画面没识别到答案项 → 不猜，交给 pause。"""
    doc = StoryDoc(
        entries=[StoryEntry(id="a", options=("为守护他人", "为求长生"), answer="为守护他人")]
    )
    assert match(doc, ["为求长生"]) is None


def test_match_tolerates_ocr_noise_in_answer():
    doc = StoryDoc(
        entries=[StoryEntry(id="a", options=("为守护他人", "为求长生"), answer="为守护他人")]
    )
    result = match(doc, ["为守沪他人", "为求长生"])
    assert result is not None
    assert result.answer_text == "为守沪他人"


def test_story_entry_from_dict_rejects_invalid():
    assert StoryEntry.from_dict({"id": "", "options": ["a"], "answer": "a"}) is None
    assert StoryEntry.from_dict({"id": "x", "options": [], "answer": "a"}) is None
    assert StoryEntry.from_dict({"id": "x", "options": ["a"], "answer": ""}) is None
    assert StoryEntry.from_dict("not-a-dict") is None


def test_list_groups_and_merge_across_layers(tmp_path, monkeypatch):
    system, local = _fake_resolver(tmp_path, monkeypatch)
    _write_group(system, "主线", [
        {"id": "a1", "options": ["x", "y"], "answer": "x"},
        {"id": "a2", "options": ["m", "n"], "answer": "n"},
    ])
    _write_group(local, "主线", [
        {"id": "a2", "options": ["m", "n"], "answer": "m"},
        {"id": "a3", "options": ["p", "q"], "answer": "p"},
    ])
    _write_group(system, "活动", [
        {"id": "b1", "options": ["u", "v"], "answer": "v"},
    ])

    assert list_groups() == ["主线", "活动"]

    doc = load_group("主线")
    by_id = {entry.id: entry for entry in doc.entries}
    assert set(by_id) == {"a1", "a2", "a3"}
    assert by_id["a2"].answer == "m"  # local 覆盖 system

    all_doc = load_all()
    assert {entry.id for entry in all_doc.entries} == {"a1", "a2", "a3", "b1"}


def test_list_groups_skips_underscore_prefixed_files(tmp_path, monkeypatch):
    """``_`` 前缀是内部文件（临时/草稿），不算配置文件。"""
    system, _local = _fake_resolver(tmp_path, monkeypatch)
    _write_group(system, "主线", [{"id": "a", "options": ["x"], "answer": "x"}])
    sidecar = system / "tools" / "auto_plot" / "_draft.json"
    sidecar.write_text('{"entries": []}', encoding="utf-8")
    assert list_groups() == ["主线"]


def test_threshold_lives_in_the_file_and_roundtrips(tmp_path, monkeypatch):
    """阈值跟着文件走：写进去再读回来，且不影响别的文件。"""
    _system, local = _fake_resolver(tmp_path, monkeypatch)
    _write_group(local, "主线", [{"id": "a", "options": ["x"], "answer": "x"}])
    _write_group(local, "活动", [{"id": "b", "options": ["y"], "answer": "y"}])

    assert load_group("主线").match_threshold == DEFAULT_THRESHOLD
    set_group_threshold("主线", 0.55, layer="local")

    assert load_group("主线").match_threshold == pytest.approx(0.55)
    assert load_group("活动").match_threshold == DEFAULT_THRESHOLD
    raw = json.loads(
        (local / "tools" / "auto_plot" / "主线.json").read_text(encoding="utf-8"))
    assert raw["match_threshold"] == pytest.approx(0.55)
    # 条目没被阈值写入碰掉
    assert [item["id"] for item in raw["entries"]] == ["a"]


def test_upsert_entry_keeps_existing_threshold(tmp_path, monkeypatch):
    """加一条文案不能把该文件已调好的阈值冲回默认值。"""
    _system, local = _fake_resolver(tmp_path, monkeypatch)
    _write_group(local, "主线", [{"id": "a", "options": ["x"], "answer": "x"}])
    set_group_threshold("主线", 0.4, layer="local")

    upsert_entry(
        StoryEntry(id="b", options=("y",), answer="y"),
        group="主线", layer="local")

    assert load_group("主线").match_threshold == pytest.approx(0.4)


def test_match_uses_the_threshold_of_the_entry_source_file():
    """合并视图里按命中条目**来源文件**的阈值判分，文件之间互不污染。

    严格文件(0.99)的条目分数更高但没到自己的阈值 → 不命中；
    把阈值对调后同样的分数就命中，证明判分看的是来源而不是某个全局值。
    """
    entry = StoryEntry(id="a", options=("为守护他人", "为求长生"), answer="为守护他人")

    strict = StoryDoc(entries=[entry], thresholds={"a": 0.99})
    assert match(strict, ["为守沪他人", "为求长生"]) is None

    lenient = StoryDoc(entries=[entry], thresholds={"a": 0.3})
    assert match(lenient, ["为守沪他人", "为求长生"]) is not None


def test_upsert_entry_only_writes_target_layer(tmp_path, monkeypatch):
    """增量写只落目标层的目标组，同 id 覆盖不重复追加。"""
    system, local = _fake_resolver(tmp_path, monkeypatch)

    upsert_entry(
        StoryEntry(id="x", options=("选项A", "选项B"), answer="选项A"),
        group="主线",
        layer="local",
    )
    assert not (system / "tools" / "auto_plot" / "主线.json").exists()
    data = json.loads(
        (local / "tools" / "auto_plot" / "主线.json").read_text(encoding="utf-8")
    )
    assert [item["id"] for item in data["entries"]] == ["x"]

    upsert_entry(
        StoryEntry(id="x", options=("选项A", "选项C"), answer="选项C"),
        group="主线",
        layer="local",
    )
    data = json.loads(
        (local / "tools" / "auto_plot" / "主线.json").read_text(encoding="utf-8")
    )
    assert [item["id"] for item in data["entries"]] == ["x"]
    assert data["entries"][0]["answer"] == "选项C"
