"""心跳 payload 与服务端 ops/stats-worker 校验器的契约。

这条契约曾经断过一次：客户端把 ``plugin``（字符串）改成 ``apps``（列表）
并升到 schema v2，服务端 ``validateHeartbeat()`` 却没同步。后果不是报错，
而是 **静默丢数据**——worker 判空后跳过入库但仍返回 200，客户端据此按
"已上报"记账，而心跳每 UTC 日只发一次，于是 DAU/WAU/留存全部归零且事后
无法补报。两边的测试当时都是全绿的：服务端 fixture 用的是旧形状。

所以这里直接读 ``ops/stats-worker/src/index.js`` 的源码来断言，而不是维护
一份会跟着漂移的期望值副本。断言的是**字段名集合**与**枚举取值**——
形状层面的东西，改动必须两边一起改。

正则的具体写法不在断言范围内：两边用不同语言的正则方言，逐字比对只会
制造假失败；真正会静默丢数据的是字段名/形状/枚举对不上。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvjiang.core.telemetry.heartbeat import HEARTBEAT_SCHEMA
from lvjiang.core.telemetry.schema import FieldSpec

_WORKER = (Path(__file__).parents[2] / "ops" / "stats-worker" / "src" / "index.js")

#: 客户端会发、但服务端**有意**不校验也不采信的字段。
#: 每一项都要写明理由——空着的豁免等于把契约撕开一个口子。
_SERVER_IGNORED = {
    # 服务端用自己的 todayUtc8() 记日期，不信客户端时钟
    # （见 schema.sql 的 first_day 注释与 index.js 的 upsertHeartbeat）
    "day",
}


@pytest.fixture(scope="module")
def worker_source() -> str:
    if not _WORKER.exists():
        pytest.skip(f"服务端源码不在仓库内: {_WORKER}")
    return _WORKER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def validator_body(worker_source: str) -> str:
    match = re.search(
        r"function validateHeartbeat\(hb\)\s*\{(.*?)\n\}", worker_source, re.S)
    assert match, "worker 里找不到 validateHeartbeat()，契约测试需要同步更新"
    return match.group(1)


def _client_field_names() -> set[str]:
    return {spec.name for spec in HEARTBEAT_SCHEMA.fields}


def test_every_client_field_is_validated_by_worker(validator_body: str):
    """客户端发的每个字段，服务端都要认得。

    漏掉的字段服务端读到 undefined —— 若该字段参与校验就整条拒收，
    若只参与入库就写进 NOT NULL 列失败。两种都是静默丢数据。
    """
    referenced = set(re.findall(r"hb\.([A-Za-z_][A-Za-z0-9_]*)", validator_body))
    missing = _client_field_names() - referenced - _SERVER_IGNORED
    assert not missing, (
        f"客户端心跳字段 {sorted(missing)} 未被 worker 的 validateHeartbeat() 校验；"
        "改字段名/形状必须连同 ops/stats-worker 与 D1 schema 一起改")


def test_worker_requires_nothing_the_client_omits(validator_body: str):
    """反向：服务端校验的字段，客户端必须都发。"""
    referenced = set(re.findall(r"hb\.([A-Za-z_][A-Za-z0-9_]*)", validator_body))
    absent = referenced - _client_field_names()
    assert not absent, (
        f"worker 校验了客户端不再发送的字段 {sorted(absent)}；"
        "这些字段会被判为 undefined 导致整条心跳被丢弃")


@pytest.mark.parametrize("field_name,worker_const", [
    ("run_env", "RUN_ENV"),
    ("os_name", "OS_NAME"),
    ("plugin", "PLUGIN"),
])
def test_enum_choices_match_worker(worker_source: str, field_name: str,
                                   worker_const: str):
    """枚举取值两边必须一致——客户端多一个值，服务端就整条拒收。

    ``plugin`` 尤其要注意：新增插件时必须同时扩充客户端 choices 与
    worker 的 PLUGIN 集合。
    """
    match = re.search(
        rf"const {worker_const} = new Set\(\[(.*?)\]\)", worker_source, re.S)
    assert match, f"worker 里找不到 {worker_const}"
    worker_values = set(re.findall(r'"([^"]+)"', match.group(1)))

    spec = next(s for s in HEARTBEAT_SCHEMA.fields if s.name == field_name)
    assert isinstance(spec, FieldSpec) and spec.choices, \
        f"{field_name} 在客户端不是枚举字段，与 worker 的 {worker_const} 不对称"
    assert set(spec.choices) == worker_values, (
        f"{field_name} 枚举不一致：客户端 {sorted(spec.choices)} "
        f"vs worker {sorted(worker_values)}")


def test_schema_version_matches_worker_known_schemas(worker_source: str):
    """心跳 schema 版本变更必须同步服务端（若服务端登记了版本）。"""
    match = re.search(r'"heartbeat":\s*new Set\(\[([^\]]*)\]\)', worker_source)
    if not match:
        pytest.skip("worker 未按版本登记 heartbeat，无需比对")
    versions = {int(v) for v in re.findall(r"\d+", match.group(1))}
    assert HEARTBEAT_SCHEMA.version in versions, (
        f"客户端 heartbeat v{HEARTBEAT_SCHEMA.version} 不在 worker 认可的 "
        f"{sorted(versions)} 内")
