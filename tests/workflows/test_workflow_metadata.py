"""工作流文件头元数据解析测试。"""

import pytest

from lvjiang.workflows.metadata import (
    METADATA_WARNING,
    WorkflowMetadataError,
    build_flow_config,
    parse_metadata,
)

SAMPLE = """\
#% name: 单件装备调律
#% note: |-
#%   请先打开装备页面。
#%   确认无弹窗后执行。
#% required_scenes: [game_main_page, equip_tune_detail]
#% parameters:
#%   - name: target_material
#%     label: 目标材料
#%     type: select
#%     default: 紫色狗粮
#%     options:
#%       - { value: "", label: 不添加 }
#%       - { value: 紫色狗粮, label: 紫色狗粮 }
#%   - name: bag_row
#%     label: 背包行号
#%     type: select
#%     default: "1"
#%     options: ["1", "2", "3"]

# 这是普通注释，不应被采集
click [scene].[area]
wait @step_interval
"""


def test_parse_basic_fields():
    m = parse_metadata(SAMPLE)
    assert m["name"] == "单件装备调律"
    assert m["note"] == "请先打开装备页面。\n确认无弹窗后执行。"
    assert m["required_scenes"] == ["game_main_page", "equip_tune_detail"]
    assert len(m["parameters"]) == 2


def test_parse_options_value_label_pairs():
    m = parse_metadata(SAMPLE)
    p0 = m["parameters"][0]
    assert p0["name"] == "target_material"
    assert p0["default"] == "紫色狗粮"
    assert p0["options"][0] == {"value": "", "label": "不添加"}
    assert p0["options"][1] == {"value": "紫色狗粮", "label": "紫色狗粮"}
    # 简单字符串列表 options 也支持
    assert m["parameters"][1]["options"] == ["1", "2", "3"]


def test_first_non_metadata_line_ends_metadata():
    m = parse_metadata(SAMPLE)
    assert "这是普通注释" not in str(m)
    assert "[area]" not in str(m)

    text = '#% name: 前段\nlog "start"\n#% name: 后段\n'
    assert parse_metadata(text) == {"name": "前段"}


def test_metadata_must_start_on_first_line():
    text = "# 普通文件说明\n#% name: 不再解析\n"
    assert parse_metadata(text) == {}


def test_no_metadata_returns_empty():
    assert parse_metadata("click [a].[b]\nwait 1\n") == {}
    assert parse_metadata("") == {}


def test_indentation_prefix_stripped():
    # 缩进后的 #% 也应被识别（前缀允许前导空白）
    text = "   #% name: 缩进测试\n"
    assert parse_metadata(text) == {"name": "缩进测试"}


def test_malformed_yaml_raises():
    text = "#% name: [unclosed\n#%   bad: : :\n"
    with pytest.raises(WorkflowMetadataError, match="YAML 解析失败"):
        parse_metadata(text)


def test_unknown_metadata_fields_are_ignored():
    text = """\
#% name: 示例
#% author: XXX
#% parameters:
#%   - name: count
#%     type: number
#%     min: 1
#%     widget: slider
#%   - name: unsupported
#%     type: text
"""
    assert parse_metadata(text) == {
        "name": "示例",
        "parameters": [{"name": "count", "type": "number", "min": 1}],
    }


def test_unknown_option_fields_are_ignored():
    text = """\
#% parameters:
#%   - name: mode
#%     type: select
#%     options: [{value: fast, label: 快速, icon: rocket}]
"""
    option = parse_metadata(text)["parameters"][0]["options"][0]
    assert option == {"value": "fast", "label": "快速"}


def test_known_parameter_missing_required_structure_raises():
    text = "#% parameters:\n#%   - name: x\n#%     type: select\n"
    with pytest.raises(WorkflowMetadataError, match="select 参数必须声明 options"):
        parse_metadata(text)


def test_all_parameter_types_are_accepted():
    text = """\
#% parameters:
#%   - {name: count, type: number, default: 2, min: 1, max: 3}
#%   - {name: enabled, type: bool, default: true}
#%   - name: mode
#%     type: select
#%     options: [{value: fast, label: 快速}, slow]
#%   - name: slots
#%     type: checkgroup
#%     options: [{value: head, label: 头部}]
"""
    assert [item["type"] for item in parse_metadata(text)["parameters"]] == [
        "number",
        "bool",
        "select",
        "checkgroup",
    ]


def test_build_flow_config_defaults(tmp_path):
    # 无元数据时回退默认值
    wf = tmp_path / "demo_flow.wf"
    wf.write_text("click [a].[b]\n", encoding="utf-8")
    cfg = build_flow_config(wf)
    assert cfg["id"] == "__loaded__:demo_flow"
    assert cfg["name"] == "[外部] demo_flow.wf"
    assert cfg["note"] == ""
    assert cfg["wf_file"] == str(wf)
    assert cfg["required_scenes"] == []
    assert cfg["parameters"] == []


def test_build_flow_config_with_metadata(tmp_path):
    wf = tmp_path / "tuning.wf"
    wf.write_text(SAMPLE, encoding="utf-8")
    cfg = build_flow_config(wf)
    assert cfg["name"] == "单件装备调律"
    assert cfg["note"] == "请先打开装备页面。\n确认无弹窗后执行。"
    assert cfg["required_scenes"] == ["game_main_page", "equip_tune_detail"]
    assert len(cfg["parameters"]) == 2
    assert cfg["wf_file"] == str(wf)


def test_build_flow_config_metadata_error_becomes_own_warning(tmp_path):
    """手动加载坏元数据仍得到可执行配置，不向调用方抛异常。"""
    wf = tmp_path / "bad.wf"
    wf.write_text("#% name: [unclosed\nlog \"still runnable\"\n", encoding="utf-8")

    cfg = build_flow_config(wf)

    assert cfg["name"] == "[外部] bad.wf"
    assert cfg["note"] == METADATA_WARNING
    assert cfg["parameters"] == []
