"""Exception-safe lease lifetime for asynchronous GUI workflow launches."""
from contextlib import nullcontext
from functools import wraps

from ...core.access import AccessDeniedError, acquire_user


def guarded_launch(method=None, *, user_selector: str | None = None):
    if method is None:
        return lambda fn: guarded_launch(fn, user_selector=user_selector)

    @wraps(method)
    def launch(self, *args, **kwargs):
        if self._running:
            return method(self, *args, **kwargs)
        username = kwargs.get("execution_username")
        if username is None:
            selector = getattr(self, user_selector, None) if user_selector else None
            username = (selector.resolve_username() if selector is not None
                        else self._user_manager.get_active_user_name())
        try:
            lease = acquire_user(username, self._session_manager._users_dir)
        except AccessDeniedError as exc:
            self.log_text.append(f"[拒绝] {exc}")
            show_busy = getattr(self, "_show_user_execution_busy", None)
            if callable(show_busy):
                show_busy(str(exc))
            return None
        except Exception as exc:
            self.log_text.append(f"[拒绝] {exc}")
            return None
        self._execution_lease = lease
        self._execution_username_snapshot = username
        self._execution_started = False
        try:
            with lease.authorized():
                return method(self, *args, **kwargs)
        except Exception as exc:
            self.log_text.append(f"[错误] 启动失败: {exc}")
            self._end_automation("启动失败")
            return None
        finally:
            if not self._execution_started:
                lease.release()
                self._execution_lease = None
    return launch


def guarded_finish(method):
    @wraps(method)
    def finish(self, *args, **kwargs):
        lease = getattr(self, "_execution_lease", None)
        try:
            with lease.authorized() if lease is not None else nullcontext():
                return method(self, *args, **kwargs)
        except Exception as exc:
            self.log_text.append(f"[错误] 执行收尾或保存失败: {exc}")
            worker = self.sender()
            self._finish_task_run(worker, status="failed", error_message=str(exc))
        finally:
            try:
                self._end_automation("工作流")
            finally:
                if lease is not None:
                    lease.release()
                    self._execution_lease = None
    return finish
