"""Profile 更新通知；服务不认识 Qt 消费者。"""
from collections.abc import Callable

from loguru import logger

_observers: list[Callable[[str], None]] = []


def observe_updates(callback: Callable[[str], None]) -> Callable[[], None]:
    _observers.append(callback)

    def remove() -> None:
        if callback in _observers:
            _observers.remove(callback)

    return remove


def notify_updated(username: str) -> None:
    for callback in _observers.copy():
        try:
            callback(username)
        except Exception as error:
            logger.warning("Profile 已更新，通知消费者失败：{}", error)
