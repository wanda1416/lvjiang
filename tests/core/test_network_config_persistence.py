"""settings.network 的往返持久化。

``UserConfig.__post_init__`` 要按字段名过滤 session.json 里的未知键（可能
存着旧版本/其他模块写入的东西，直接展开会 TypeError）。字段名必须**从
dataclass 自身取**——手写白名单一旦漏同步新字段，用户存下的值会被静默丢
掉、回退成默认值：``network.remote_config`` 就这么漏过一次，表现为"用户
关掉在线配置更新，重启后又自己打开了"。
"""
from __future__ import annotations

from dataclasses import fields

import pytest

from lvjiang.core.config.models import HotkeyConfig, NetworkConfig, UserConfig


class TestNetworkRoundTrip:
    @pytest.mark.parametrize("name", [f.name for f in fields(NetworkConfig)])
    def test_every_field_survives_round_trip(self, name):
        """逐字段参数化：将来加新开关，漏同步会直接红在这里。"""
        flipped = not getattr(NetworkConfig(), name)
        config = UserConfig(network={name: flipped})
        assert getattr(config.network, name) is flipped

    def test_remote_config_can_be_turned_off(self):
        """本 bug 的回归点。"""
        assert UserConfig(network={"remote_config": False}).network.remote_config is False

    def test_unknown_keys_ignored(self):
        config = UserConfig(network={"旧版本遗留键": 1, "telemetry": True})
        assert config.network.telemetry is True

    def test_values_coerced_to_bool(self):
        assert UserConfig(network={"telemetry": 1}).network.telemetry is True


class TestHotkeyRoundTrip:
    @pytest.mark.parametrize("name", [f.name for f in fields(HotkeyConfig)])
    def test_every_field_survives_round_trip(self, name):
        config = UserConfig(hotkeys={name: "F7"})
        assert getattr(config.hotkeys, name) == "F7"

    def test_unknown_keys_ignored(self):
        assert UserConfig(hotkeys={"没这个动作": "F7"}).hotkeys.start == "F9"
