"""refresh 令牌的存放后端。

桌面端只进系统凭据库（Windows/macOS/SecretService/kwallet）；Android 用应用
私有沙箱文件——进程隔离由 OS 保证，等价于桌面的凭据库边界。

**桌面端缺 keyring 时不退回明文文件**：否则 refresh 令牌会明文落到
``config/session/account_token``，是静默的安全降级。宁可明确报错。

本模块不决定路径、也不读模块级全局：路径与 keyring 模块都由装配层
（:mod:`.service`）以参数传入，这样测试替换后端时只需替换装配层的入口。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .errors import AccountError

#: 认得的系统凭据库后端（keyring 的第三方/内存后端不算「安全存储」）。
ALLOWED_KEYRING_MODULES = frozenset({
    "keyring.backends.Windows", "keyring.backends.macOS",
    "keyring.backends.SecretService", "keyring.backends.kwallet",
    "keyring.backends.libsecret",
})

#: keyring 里存令牌用的 service 名（与用户名一起构成键）。
KEYRING_SERVICE = "lvjiang.account"


class _AndroidTokenStore:
    """Android 的 refresh 令牌后端：应用私有目录下的单个 JSON 文件。

    接口与 keyring 后端同形（get/set/delete_password + stored_users），
    这样上层不必区分两种后端。
    """

    def __init__(self, token_path: Callable[[], Path]):
        self._token_path = token_path

    def _read(self) -> dict:
        try:
            return json.loads(self._token_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get_password(self, service: str, username: str) -> str | None:
        data = self._read()
        if isinstance(data, dict) and data.get("user") == username:
            value = data.get("token")
            return value if isinstance(value, str) else None
        return None

    def set_password(self, service: str, username: str, password: str) -> None:
        path = self._token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"user": username, "token": password}, ensure_ascii=False),
            encoding="utf-8", newline="\n")

    def delete_password(self, service: str, username: str) -> None:
        try:
            self._token_path().unlink()
        except OSError:
            pass

    def stored_users(self) -> set[str]:
        """文件里已经存了哪些实例键（诊断「登录态被实例变更孤立」用）。"""
        data = self._read()
        user = data.get("user") if isinstance(data, dict) else None
        return {user} if isinstance(user, str) and user else set()


def select_backend(
    *,
    on_android: bool,
    keyring_module,
    token_path: Callable[[], Path],
):
    """挑一个可用的凭据后端；不可用时抛 :class:`AccountError`。

    ``keyring_module`` 由装配层传入（桌面端是 ``keyring``，Android/缺依赖时是
    ``None``）——本模块不自己 import，避免与「测试把 keyring 换掉」冲突。
    """
    if on_android:
        return _AndroidTokenStore(token_path)
    if keyring_module is None:
        raise AccountError("系统凭据库不可用，无法保存登录态")
    backend = keyring_module.get_keyring()
    if type(backend).__module__ == "keyring.backends.chainer":
        backend = next((candidate for candidate in backend.backends
                        if type(candidate).__module__ in ALLOWED_KEYRING_MODULES), None)
    if backend is None or type(backend).__module__ not in ALLOWED_KEYRING_MODULES:
        raise AccountError("系统凭据库不可用，无法保存登录态")
    return backend


def read_token(backend_factory: Callable[[], object], user: str) -> str:
    """读该实例键下的 refresh 令牌；凭据库故障翻成可展示的中文错误。"""
    if not user:
        return ""
    try:
        return backend_factory().get_password(KEYRING_SERVICE, user) or ""
    except AccountError:
        raise
    except Exception as error:
        raise AccountError(f"无法读取系统凭据库：{error}") from None


def write_token(
    backend_factory: Callable[[], object], user: str, token: str,
) -> None:
    """写入或清除 refresh 令牌（``token`` 为空即清除）。"""
    if not user:
        if token:
            raise AccountError("缺少实例标识，无法保存登录态")
        return
    try:
        backend = backend_factory()
        if token:
            backend.set_password(KEYRING_SERVICE, user, token)
        elif backend.get_password(KEYRING_SERVICE, user):
            backend.delete_password(KEYRING_SERVICE, user)
    except AccountError:
        raise
    except Exception as error:
        raise AccountError(f"无法写入系统凭据库：{error}") from None


def token_owner(backend_factory: Callable[[], object]) -> str:
    """凭据库里现存令牌归属的实例键；没有令牌返回空串。

    用途：区分「从没登录」与「登录态还在，但当前实例标识变了、匹配不上」——
    后者要明确告诉用户重新登录，而不是静默显示成未登录。
    """
    try:
        backend = backend_factory()
    except AccountError:
        return ""
    owners = getattr(backend, "stored_users", None)
    if not callable(owners):
        return ""
    try:
        return next(iter(owners()), "")
    except Exception:                      # noqa: BLE001 — 诊断信息，取不到就算了
        return ""
