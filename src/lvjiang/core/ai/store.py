"""连接参数独立保存；API Key 只进入系统凭据库。"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path

import keyring

from ..config.session import SessionStore
from .service import AIError, AISettings


class AIStore(SessionStore):
    FORMAT_VERSION = 1
    TRANSIENT_PATHS = frozenset()

    @property
    def path(self) -> Path:
        from ... import constants
        return self._path_override or constants.SESSION_PATH.with_name("ai.json")

    def settings(self) -> AISettings:
        node = self.get_node("connection", {})
        if not isinstance(node, dict):
            return AISettings()
        timeout = node.get("timeout", 30.0)
        return AISettings(
            str(node.get("base_url") or ""), str(node.get("model") or ""),
            float(timeout) if isinstance(timeout, (int, float)) else 30.0,
        )

    def save(self, settings: AISettings, api_key: str) -> None:
        settings = settings.validated()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.set_key(settings.base_url, api_key)
        self.set_node("connection", asdict(settings))

    def _account(self, base_url: str) -> str:
        identity = str(self.path.resolve()) + "\n" + base_url.strip().rstrip("/")
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _credentials():
        backend = keyring.get_keyring()
        # 不接受用户环境中另装的明文文件后端或空后端。
        allowed = {
            "keyring.backends.Windows", "keyring.backends.macOS",
            "keyring.backends.SecretService", "keyring.backends.kwallet",
            "keyring.backends.libsecret",
        }
        if type(backend).__module__ == "keyring.backends.chainer":
            backend = next((candidate for candidate in backend.backends
                            if type(candidate).__module__ in allowed), None)
        if backend is None or type(backend).__module__ not in allowed:
            raise AIError("credentials", "系统凭据库不可用；可临时填写 Key 测试，保存 Key 需要启用系统凭据库")
        return backend

    def get_key(self, base_url: str) -> str:
        try:
            return self._credentials().get_password("lvjiang.ai", self._account(base_url)) or ""
        except AIError:
            raise
        except Exception:
            raise AIError("credentials", "无法读取系统凭据库，请检查凭据库是否已解锁") from None

    def set_key(self, base_url: str, api_key: str) -> None:
        try:
            backend = self._credentials()
            account = self._account(base_url)
            if api_key.strip():
                backend.set_password("lvjiang.ai", account, api_key.strip())
            elif backend.get_password("lvjiang.ai", account):
                backend.delete_password("lvjiang.ai", account)
        except AIError:
            if api_key.strip():
                raise
        except Exception:
            raise AIError("credentials", "无法更新系统凭据库，连接配置未保存") from None
