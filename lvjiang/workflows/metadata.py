"""工作流 front-matter 元数据解析

.wf 文件可在任意位置（约定放文件顶部）用 `#%` 前缀声明 YAML 元数据，
schema 与 workflow.yaml 的单条 flow 完全一致（name / required_scenes / parameters）。

由于每行都以 `#` 开头，DSL 引擎将其视为普通注释忽略，文件仍可直接执行；
而本模块单独收集这些行、剥掉 `#%` 前缀后按 YAML 解析，从而“提前”拿到名字与参数。

示例：
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
"""

import re
from pathlib import Path

import yaml
from loguru import logger

# 匹配元数据行：行首（可含缩进）以 #% 开头，捕获其后内容（去掉紧跟的一个空格）
_META_LINE = re.compile(r"^\s*#%\s?(.*)$")


def parse_metadata(text: str) -> dict:
    """从 DSL 文本中提取 `#%` front-matter 并解析为 dict。

    无元数据时返回空 dict。解析失败仅记录警告并返回空 dict，不影响执行。
    """
    lines: list[str] = []
    for raw in text.splitlines():
        m = _META_LINE.match(raw)
        if m is not None:
            lines.append(m.group(1))

    if not lines:
        return {}

    body = "\n".join(lines)
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as e:
        logger.warning(f"工作流元数据 YAML 解析失败，已忽略: {e}")
        return {}

    if not isinstance(data, dict):
        logger.warning(f"工作流元数据不是键值映射，已忽略: {type(data).__name__}")
        return {}
    return data


def parse_metadata_file(path: str | Path) -> dict:
    """读取 .wf 文件并解析其 front-matter 元数据"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"读取工作流文件失败: {e}")
        return {}
    return parse_metadata(text)


def build_flow_config(path: str | Path) -> dict:
    """由 .wf 文件路径构造 GUI 可用的 flow 配置。

    合并 front-matter 元数据；缺失字段回退到合理默认值。
    id 由文件名派生，wf_file 为绝对路径。
    """
    p = Path(path)
    meta = parse_metadata_file(p)
    return {
        "id": f"__loaded__:{p.stem}",
        "name": meta.get("name") or f"[外部] {p.name}",
        "wf_file": str(p),
        "required_scenes": meta.get("required_scenes") or [],
        "parameters": meta.get("parameters") or [],
    }
