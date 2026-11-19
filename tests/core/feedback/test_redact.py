"""日志脱敏的回归测试。

保护的真实场景：用户点了「提交」，附件里是两千行原始日志。脱敏一旦漏掉用户目录
或令牌，等于把身份信息直接发给作者——这类错误在界面上完全看不出来，只能靠测试
兜住。
"""
from __future__ import annotations

from lvjiang.core.feedback.redact import redact_text

ANON = "8f14e45fceea167a5a36dedd4bea2543"


def test_windows_user_dir_is_masked():
    text = r"2026-11-19 09:00:01 | INFO | 读取 C:\Users\alice\Githubs\lvjiang\config\session"
    result = redact_text(text)
    assert "alice" not in result
    assert "<用户目录>" in result
    # 路径的其余部分保留：排查「读的是哪个文件」还要靠它
    assert r"\Githubs\lvjiang\config\session" in result


def test_posix_user_dir_is_masked():
    result = redact_text("/home/alice/lvjiang/logs/lvjiang_2026-11-19.log")
    assert "alice" not in result
    assert "<用户目录>/lvjiang/logs" in result


def test_tokens_are_masked():
    text = (
        'header authorization: Bearer 1234567890abcdef\n'
        'payload {"refresh_token": "abcdef123456", "access_token":"zzz999"}\n'
        "参数 token=deadbeef password=hunter2\n"
    )
    result = redact_text(text)
    for secret in ("1234567890abcdef", "abcdef123456", "zzz999", "deadbeef", "hunter2"):
        assert secret not in result
    assert result.count("<已隐藏>") >= 5


def test_adb_serial_is_masked_but_plain_ids_survive():
    text = "adb -s serial: R58M12345XY device\nANDROID_SERIAL=R58M12345XY\n指纹 6f2c9a1e"
    result = redact_text(text)
    assert "R58M12345XY" not in result
    assert "<设备序号>" in result
    # 普通十六进制（指纹、sha）不是序列号，不能被误伤成掩码——排查要靠它
    assert "6f2c9a1e" in result


def test_install_id_is_masked_when_known():
    result = redact_text(f"telemetry identity {ANON} sent", install_id=ANON)
    assert ANON not in result
    assert "<匿名ID>" in result


def test_lan_ip_and_workflow_names_survive():
    text = "连接 192.168.1.7:8080 成功，工作流 每日任务 开始运行"
    assert redact_text(text) == text


def test_empty_input():
    assert redact_text("") == ""
