"""环境快照与邮箱校验的回归测试。

保护的真实场景：反馈里自动附带的环境信息一旦带上用户名、路径或角色名，用户
「只是在提建议」却把身份发了出去。这里锁死两件事：字段是白名单式的技术事实，
以及邮箱校验与服务端同口径。
"""
from __future__ import annotations

from lvjiang.core.feedback.payload import (
    collect_environment,
    is_valid_email,
    normalize_email,
)


def test_environment_has_technical_fields_only():
    env = collect_environment()
    assert env["version"]
    assert env["platform"] in ("desktop", "android")
    assert env["os"]
    assert env["arch"]
    assert env["frozen"] in ("0", "1")
    # 白名单之外一律不出现：这些键一旦加进来就是隐私泄露
    for forbidden in ("login", "username", "role", "device_name", "path", "email"):
        assert forbidden not in env


def test_environment_accepts_host_extras_but_truncates():
    env = collect_environment(extra={"layout": "笔记本", "workflow": "x" * 500, "empty": ""})
    assert env["layout"] == "笔记本"
    assert len(env["workflow"]) <= 80
    assert "empty" not in env


def test_email_normalisation_and_validation():
    assert normalize_email("  User@Example.COM ") == "user@example.com"
    assert is_valid_email("user@example.com")
    assert is_valid_email("a.b+c@sub.example.cn")
    for bad in ("", "user", "user@", "@example.com", "user@example", "a b@example.com",
                "user@exa mple.com"):
        assert not is_valid_email(bad), bad
