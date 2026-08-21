"""按键状态注册表 — DSL press 指令的统一状态管理

设计原则：
- 状态唯一来源：DSL 层面的 KeyStateRegistry，而非各 InputBackend
- 严格模式：重复 down 或未 down 就 up 直接报错（DSL 作者错误）
- 幂等清理：release_all 单键失败不阻塞其他键，最终清空集合
- Registry 持有 backend 引用，协调状态与后端调用
"""

from loguru import logger

from ...core.input_base import InputBackend
from .signals import WorkflowUserError


class KeyStateRegistry:
    """按键状态注册表

    跟踪 DSL 当前「按下未释放」的键集合，协调 backend 调用。
    工作流退出时（正常/异常/取消）统一调用 release_all 释放所有键。
    """

    def __init__(self, backend: InputBackend):
        self._backend = backend
        self._pressed: set[str] = set()

    def key_down(self, key: str) -> None:
        """按下按键

        Raises:
            WorkflowUserError: 该键已被按下（DSL 作者重复 down）
        """
        if key in self._pressed:
            raise WorkflowUserError(f"Key '{key}' is already pressed")
        self._backend.key_down(key)
        self._pressed.add(key)

    def key_up(self, key: str) -> None:
        """释放按键

        Raises:
            WorkflowUserError: 该键未被按下（DSL 作者未 down 就 up）
        """
        if key not in self._pressed:
            raise WorkflowUserError(f"Key '{key}' is not pressed")
        self._backend.key_up(key)
        self._pressed.discard(key)

    def release_all(self) -> None:
        """释放所有仍处于按下状态的键（幂等，单键失败不阻塞其他键）

        所有退出路径（正常结束/异常/取消/超时）都必须调用此方法。
        """
        if not self._pressed:
            return
        logger.debug(f"release_all: 释放 {len(self._pressed)} 个按键: {sorted(self._pressed)}")
        for key in list(self._pressed):
            try:
                self._backend.key_up(key)
            except Exception:
                logger.exception(f"release_all: 释放 {key} 失败")
            finally:
                self._pressed.discard(key)

    def is_pressed(self, key: str) -> bool:
        """查询某键是否处于「由 DSL 按下」状态"""
        return key in self._pressed
