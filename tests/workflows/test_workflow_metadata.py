"""工作流 front-matter 元数据解析测试"""

from lvjiang.workflows.metadata import parse_metadata, build_flow_config


SAMPLE = """\
#% name: 单件装备调律
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
wait step_interval
"""


def test_parse_basic_fields():
    m = parse_metadata(SAMPLE)
    assert m["name"] == "单件装备调律"
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


def test_normal_comments_ignored():
    # 普通 # 注释与正文语句不进入元数据
    m = parse_metadata(SAMPLE)
    assert "这是普通注释" not in str(m)
    assert "[area]" not in str(m)


def test_no_metadata_returns_empty():
    assert parse_metadata("click [a].[b]\nwait 1\n") == {}
    assert parse_metadata("") == {}


def test_indentation_prefix_stripped():
    # 缩进后的 #% 也应被识别（前缀允许前导空白）
    text = "   #% name: 缩进测试\n"
    assert parse_metadata(text) == {"name": "缩进测试"}


def test_malformed_yaml_is_ignored(caplog):
    # 非法 YAML 不抛异常，返回空 dict
    text = "#% name: [unclosed\n#%   bad: : :\n"
    assert parse_metadata(text) == {}


def test_build_flow_config_defaults(tmp_path):
    # 无元数据时回退默认值
    wf = tmp_path / "demo_flow.wf"
    wf.write_text("click [a].[b]\n", encoding="utf-8")
    cfg = build_flow_config(wf)
    assert cfg["id"] == "__loaded__:demo_flow"
    assert cfg["name"] == "[外部] demo_flow.wf"
    assert cfg["wf_file"] == str(wf)
    assert cfg["required_scenes"] == []
    assert cfg["parameters"] == []


def test_build_flow_config_with_metadata(tmp_path):
    wf = tmp_path / "tuning.wf"
    wf.write_text(SAMPLE, encoding="utf-8")
    cfg = build_flow_config(wf)
    assert cfg["name"] == "单件装备调律"
    assert cfg["required_scenes"] == ["game_main_page", "equip_tune_detail"]
    assert len(cfg["parameters"]) == 2
    assert cfg["wf_file"] == str(wf)
