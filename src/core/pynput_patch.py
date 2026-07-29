"""pynput Windows 钩子回调防护补丁。

pynput 1.8.2 的 ``SystemHook._handler`` 存在缺陷：钩子回调触发时若
``_HOOKS`` 表中查不到当前线程（退出竞态——UnhookWindowsHookEx 之后
消息队列中残留的钩子消息仍可能派发到已注销的回调），函数落空返回
None；而回调原型 ``_HOOKPROC`` 要求返回 LPARAM（整数），ctypes 转换
失败即打出::

    Exception ignored on converting result of ctypes callback function
    TypeError: 'NoneType' object cannot be interpreted as an integer

本补丁用同签名的安全版本替换 ``SystemHook._handler``，保证任何路径
都返回整数。必须在任何 pynput Listener 启动之前调用 :func:`install`。
"""
from __future__ import annotations

import logging
import sys
import threading

logger = logging.getLogger(__name__)

_installed = False
# 持有 ctypes 回调对象的强引用，防止被 GC 后钩子踩空（access violation）
_patched_handler = None


def install() -> None:
    """替换 SystemHook._handler 为永不返回 None 的安全版本（幂等）。"""
    global _installed, _patched_handler
    if _installed or sys.platform != "win32":
        return
    try:
        from pynput._util.win32 import SystemHook
    except Exception:  # pynput 未安装或导入失败，不影响主程序
        logger.debug("[pynput_patch] pynput 不可用，跳过补丁", exc_info=True)
        return

    @SystemHook._HOOKPROC
    def _safe_handler(code, msg, lpdata):
        # 与原实现逻辑一致，仅补齐 self 为 None 时的整数返回值
        try:
            key = threading.current_thread().ident
            self = SystemHook._HOOKS.get(key, None)
            if self is not None:
                try:
                    self.on_hook(code, msg, lpdata)
                except self.SuppressException:
                    # 返回非零以阻止事件继续传播
                    return 1
                except:  # noqa: E722 —— 与原实现一致，吞掉一切回调异常
                    pass
            return SystemHook._CallNextHookEx(0, code, msg, lpdata)
        except:  # noqa: E722 —— 解释器关闭阶段兜底，绝不返回 None
            return 0

    SystemHook._handler = _safe_handler
    _patched_handler = _safe_handler
    _installed = True
    logger.debug("[pynput_patch] SystemHook._handler 防护补丁已安装")
