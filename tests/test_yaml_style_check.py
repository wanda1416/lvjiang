"""session YAML 的排版门禁：文件必须等于本仓库序列化器的输出。

防的是真实出现过的问题：`config/session/profile.yaml` 被外部工具（网页端/其它
序列化器）写成缩进式 block sequence 后，本地保存一次就整篇重排，两千行 diff
里没有一处内容变化。守卫只做纯 YAML round-trip，不经 profile schema，
因此「未注册周期」「未知节点」这类核心看不懂但必须留着的定义不会被删改。
"""
from scripts.ci import check_yaml_style

from lvjiang.core.config import save_yaml

_INDENTED = """quota:
  - key: nn_of_week
    label: 周配额
note:
  - key: sample
    label: 示例
"""


def test_canonical_file_passes(tmp_path):
    path = tmp_path / "profile.yaml"
    save_yaml(path, {"quota": [{"key": "a", "label": "甲"}]})
    assert check_yaml_style.main([str(tmp_path)]) == 0


def test_indented_sequences_are_reported(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(_INDENTED, encoding="utf-8")
    assert check_yaml_style.main([str(tmp_path)]) == 1


def test_write_reformats_without_touching_definitions(tmp_path):
    path = tmp_path / "profile.yaml"
    pathological = (
        "quota:\n"
        "  - key: nn_of_week\n"
        "    label: 周配额\n"
        "    period: season          # 未注册周期：核心看不懂但必须留着\n"
        "legacy_section:\n"
        "  - key: keep_me\n"
        "    nested:\n"
        "      - deep\n"
    )
    path.write_text(pathological, encoding="utf-8")
    before = check_yaml_style.load_yaml(path)

    assert check_yaml_style.main([str(tmp_path), "--write"]) == 0

    after = check_yaml_style.load_yaml(path)
    assert after == before, "规范化只能改排版，不能改动任何定义"
    assert "legacy_section" in after and "period" in after["quota"][0]
    assert check_yaml_style.main([str(tmp_path)]) == 0


def test_missing_directory_is_skipped(tmp_path):
    assert check_yaml_style.main([str(tmp_path / "not-there")]) == 0


def test_dump_yaml_keeps_block_sequences_at_column_zero():
    """序列化口径是全局契约：改 dump 参数会让全仓 YAML 整篇重排。"""
    body = check_yaml_style.dump_yaml({"quota": [{"key": "a"}], "b": 1})
    assert body == "quota:\n- key: a\nb: 1\n"
