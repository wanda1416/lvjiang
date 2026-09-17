"""工作流文件头元数据解析测试。"""

import pytest

from lvjiang.core.task_params import parameters_for_env
from lvjiang.workflows.metadata import (
    METADATA_WARNING,
    WorkflowMetadataError,
    build_flow_config,
    known_envs,
    metadata_error,
    metadata_for_script_config,
    parse_metadata,
    parse_metadata_file,
)

SAMPLE = """\
#% name: 单件装备调律
#% note: |-
#%   请先打开装备页面。
#%   确认无弹窗后执行。
#% runnable: true
#% batchable: true
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
    assert m["runnable"] is True
    assert m["batchable"] is True
    assert len(m["parameters"]) == 2


def test_script_id_accepts_unicode_letters():
    assert parse_metadata("#% id: 好友送礼\n")["id"] == "好友送礼"
    assert parse_metadata("#% id: Équipement_2\n")["id"] == "Équipement_2"


@pytest.mark.parametrize("script_id", ["2fast", "_internal", "bad-id", "好友-送礼"])
def test_script_id_still_rejects_non_identifier_shapes(script_id):
    with pytest.raises(WorkflowMetadataError, match="Unicode 字母"):
        parse_metadata(f"#% id: {script_id}\n")


def test_runnable_defaults_to_false():
    """未声明 runnable / batchable 的 .wf 不注册为脚本"""
    m = parse_metadata("#% name: 内部过程库\n")
    assert "runnable" not in m
    assert "batchable" not in m


def test_script_traits_batchable_implies_runnable():
    from lvjiang.workflows.metadata import script_traits
    assert script_traits({})["runnable"] is False
    assert script_traits({"batchable": True})["runnable"] is True
    assert script_traits({"batchable": True})["batchable"] is True
    assert script_traits({})["scope"] == "daily"


def test_parse_options_value_label_pairs():
    m = parse_metadata(SAMPLE)
    p0 = m["parameters"][0]
    assert p0["name"] == "target_material"
    assert p0["default"] == "紫色狗粮"
    assert p0["options"][0] == {"value": "", "label": "不添加"}
    assert p0["options"][1] == {"value": "紫色狗粮", "label": "紫色狗粮"}
    # 简单字符串列表 options 也支持
    assert m["parameters"][1]["options"] == ["1", "2", "3"]


def test_parameter_env_is_validated_and_filters_only_declared_parameters():
    text = """\
#% parameters:
#%   - {name: common, type: text}
#%   - {name: desktop_key, type: text, env: [desktop]}
#%   - {name: android_area, type: text, env: [android]}
"""
    definitions = parse_metadata(text)["parameters"]

    assert definitions[1]["env"] == ["desktop"]
    assert [item["name"] for item in parameters_for_env(
        definitions, "desktop")] == ["common", "desktop_key"]
    assert [item["name"] for item in parameters_for_env(
        definitions, "android")] == ["common", "android_area"]


def test_parameter_env_must_be_a_string_list():
    text = "#% parameters:\n#%   - {name: key, type: text, env: desktop}\n"
    with pytest.raises(WorkflowMetadataError, match="parameters\\[0\\]\\.env"):
        parse_metadata(text)


@pytest.mark.parametrize("text, path", [
    ("#% env: [andriod]\n", "env"),
    ("#% parameters:\n#%   - {name: key, type: text, env: [pc]}\n",
     "parameters\\[0\\]\\.env"),
])
def test_env_must_use_environments_declared_in_app_config(text, path, monkeypatch):
    monkeypatch.setattr(
        "lvjiang.workflows.metadata.known_envs", lambda: ["android", "desktop"])
    with pytest.raises(WorkflowMetadataError, match=f"{path}.*未知环境.*android, desktop"):
        parse_metadata(text)


def test_env_accepts_custom_environment_from_app_config(monkeypatch):
    """环境列表来自系统参数 app.yaml 的 envs，不是代码常量。"""
    monkeypatch.setattr(
        "lvjiang.workflows.metadata.known_envs", lambda: ["android", "desktop", "ios"])

    assert parse_metadata("#% env: [ios]\n") == {"env": ["ios"]}


def test_known_envs_reads_app_config(monkeypatch):
    monkeypatch.setattr(
        "lvjiang.core.config.resolver.load_available_envs",
        lambda: [("android", "安卓"), ("desktop", "桌面"), ("ios", "ios")])

    assert known_envs() == ["android", "desktop", "ios"]


def test_env_skips_value_check_when_app_config_unavailable(monkeypatch):
    monkeypatch.setattr("lvjiang.workflows.metadata.known_envs", lambda: [])

    assert parse_metadata("#% env: [anything]\n") == {"env": ["anything"]}


def test_bom_prefixed_file_still_parses(tmp_path):
    """Windows 记事本默认带 BOM；BOM 文件不能因为首行不匹配 ``#%`` 而静默消失。"""
    path = tmp_path / "bom.wf"
    path.write_bytes("\ufeff#% name: x\n#% runnable: true\nwait 1\n".encode("utf-8"))

    assert parse_metadata_file(path) == {"name": "x", "runnable": True}


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


def test_metadata_error_returns_editor_message():
    assert "YAML" in metadata_error("#% name: [unclosed\n")
    assert metadata_error("#% runnable: true\n") == ""


def test_script_id_is_a_plain_identifier():
    for value in ("weekly/a", "a-b", "_private", "../escape"):
        with pytest.raises(WorkflowMetadataError, match="id"):
            parse_metadata(f"#% id: {value}\n")
    assert parse_metadata("#% id: weekly_a\n")["id"] == "weekly_a"
    assert parse_metadata("#% id: 中文\n")["id"] == "中文"


def test_checkgroup_numeric_default_key_is_a_metadata_error():
    text = """\
#% parameters:
#%   - name: choices
#%     type: checkgroup
#%     options: [a]
#%     default:
#%       1: true
"""
    with pytest.raises(WorkflowMetadataError, match="选项键"):
        parse_metadata(text)


def test_invalid_utf8_file_is_isolated(tmp_path, monkeypatch):
    path = tmp_path / "broken.wf"
    path.write_bytes(b"\xff\xfe")
    errors = []
    monkeypatch.setattr("lvjiang.workflows.metadata.logger.error", errors.append)

    metadata, warning = metadata_for_script_config(path)

    assert metadata == {}
    assert warning == METADATA_WARNING
    assert any("UTF-8" in message for message in errors)


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
#%     type: color_picker
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
#%   - {name: code, type: text, default: "", placeholder: 区分大小写}
"""
    assert [item["type"] for item in parse_metadata(text)["parameters"]] == [
        "number",
        "bool",
        "select",
        "checkgroup",
        "text",
    ]


def test_text_parameter_keeps_default_and_placeholder():
    text = ('#% parameters:\n'
            '#%   - {name: code, type: text, default: ABC, placeholder: 兑换码}\n')
    param = parse_metadata(text)["parameters"][0]
    assert param["default"] == "ABC"
    assert param["placeholder"] == "兑换码"


@pytest.mark.parametrize("value", ["true", "false"])
def test_text_parameter_accepts_multiline_boolean(value):
    param = parse_metadata(
        f"#% parameters:\n#%   - {{name: codes, type: text, multiline: {value}}}\n"
    )["parameters"][0]
    assert param["multiline"] is (value == "true")


@pytest.mark.parametrize("value", ['"true"', "1", "null"])
def test_text_parameter_rejects_invalid_multiline(value):
    with pytest.raises(WorkflowMetadataError, match="必须是布尔值"):
        parse_metadata(
            f"#% parameters:\n#%   - {{name: codes, type: text, multiline: {value}}}\n"
        )


@pytest.mark.parametrize("field", ["default", "placeholder"])
def test_text_parameter_rejects_non_string(field):
    """YAML 里漏引号会把 123 解析成整数，注入后拿到的不是字符串。

    等到运行期才发现，游戏已经被点到输入框那一步了，这里当场拦下。
    """
    text = f'#% parameters:\n#%   - {{name: code, type: text, {field}: 123}}\n'
    with pytest.raises(WorkflowMetadataError, match="必须是字符串"):
        parse_metadata(text)


def test_build_flow_config_defaults(tmp_path):
    # 无元数据时回退默认值
    wf = tmp_path / "demo_flow.wf"
    wf.write_text("click [a].[b]\n", encoding="utf-8")
    cfg = build_flow_config(wf)
    assert cfg["id"] == "__loaded__:demo_flow"
    assert cfg["name"] == "[外部] demo_flow.wf"
    assert cfg["note"] == ""
    assert cfg["wf_file"] == str(wf)
    assert cfg["runnable"] is True
    assert cfg["parameters"] == []


def test_build_flow_config_with_metadata(tmp_path):
    wf = tmp_path / "tuning.wf"
    wf.write_text(SAMPLE, encoding="utf-8")
    cfg = build_flow_config(wf)
    assert cfg["name"] == "单件装备调律"
    assert cfg["note"] == "请先打开装备页面。\n确认无弹窗后执行。"
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
