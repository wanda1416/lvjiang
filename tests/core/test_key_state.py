"""KeyStateRegistry 单元测试

覆盖：
- key_down / key_up 基本流程
- 重复 key_down 报错
- 未 key_down 就 key_up 报错
- release_all 幂等性
- release_all 单键失败不阻塞其他键
"""

from unittest.mock import MagicMock

import pytest

from lvjiang.workflows.engine.key_state import KeyStateRegistry
from lvjiang.workflows.engine.signals import WorkflowUserError


def _make_registry(backend=None):
    """创建带 mock backend 的 registry"""
    if backend is None:
        backend = MagicMock()
    return KeyStateRegistry(backend), backend


class TestBasicFlow:
    def test_key_down_calls_backend(self):
        reg, backend = _make_registry()
        reg.key_down("W")
        backend.key_down.assert_called_once_with("W")
        assert reg.is_pressed("W")

    def test_key_up_calls_backend(self):
        reg, backend = _make_registry()
        reg.key_down("W")
        reg.key_up("W")
        backend.key_up.assert_called_once_with("W")
        assert not reg.is_pressed("W")

    def test_down_up_sequence(self):
        reg, backend = _make_registry()
        reg.key_down("SHIFT")
        reg.key_down("W")
        assert reg.is_pressed("SHIFT")
        assert reg.is_pressed("W")
        reg.key_up("W")
        assert reg.is_pressed("SHIFT")
        assert not reg.is_pressed("W")
        reg.key_up("SHIFT")
        assert not reg.is_pressed("SHIFT")


class TestStrictValidation:
    def test_duplicate_down_raises(self):
        reg, _ = _make_registry()
        reg.key_down("W")
        with pytest.raises(WorkflowUserError, match="already pressed"):
            reg.key_down("W")

    def test_up_without_down_raises(self):
        reg, _ = _make_registry()
        with pytest.raises(WorkflowUserError, match="not pressed"):
            reg.key_up("W")

    def test_up_already_released_raises(self):
        reg, _ = _make_registry()
        reg.key_down("W")
        reg.key_up("W")
        with pytest.raises(WorkflowUserError, match="not pressed"):
            reg.key_up("W")


class TestReleaseAll:
    def test_release_all_basic(self):
        reg, backend = _make_registry()
        reg.key_down("W")
        reg.key_down("SHIFT")
        reg.release_all()
        assert backend.key_up.call_count == 2
        assert not reg.is_pressed("W")
        assert not reg.is_pressed("SHIFT")

    def test_release_all_empty(self):
        reg, backend = _make_registry()
        reg.release_all()  # 无按键按下，不报错
        backend.key_up.assert_not_called()

    def test_release_all_idempotent(self):
        reg, backend = _make_registry()
        reg.key_down("W")
        reg.release_all()
        reg.release_all()  # 第二次调用不报错
        assert backend.key_up.call_count == 1  # 只释放一次

    def test_release_all_single_key_failure_doesnt_block(self):
        """单键释放失败不阻塞其他键"""
        backend = MagicMock()
        # 第二次 key_up 抛异常
        backend.key_up.side_effect = [None, RuntimeError("硬件错误"), None]
        reg = KeyStateRegistry(backend)
        reg.key_down("A")
        reg.key_down("B")
        reg.key_down("C")
        reg.release_all()
        # 三个键都尝试释放
        assert backend.key_up.call_count == 3
        # 所有键都被清理
        assert not reg.is_pressed("A")
        assert not reg.is_pressed("B")
        assert not reg.is_pressed("C")
