"""首次配置向导 + 凭据存取。

- ``account_id`` / ``api_token`` 优先读环境变量（``CLOUDFLARE_ACCOUNT_ID`` /
  ``CLOUDFLARE_API_TOKEN``），其次是本地保存的值。
- ``api_token`` 默认写系统密钥环（``keyring``），不支持密钥环的平台退化为
  ``data/token.secret``（写入时 chmod 0600）。**永不**写进 ``config.json``，
  避免这份文件被误提交或误分享时带出 token。
- ``database_id`` 自动从 ``ops/stats-worker/wrangler.toml`` 读取（非密钥，见
  该文件同目录 README："database_id 不是凭据"），允许在配置页覆盖。
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path

_KEYRING_SERVICE = "lvjiang-stats-client"
_KEYRING_USERNAME = "cloudflare-api-token"

# database_id 本身不是密钥（README 原话），这里保留一份兜底值，仅在
# wrangler.toml 读取失败时使用——正常路径总是从文件读，兜底只防文件缺失/改名。
_FALLBACK_DATABASE_ID = "342147bc-54a7-4114-8c97-73e939352f54"
_FALLBACK_DATABASE_NAME = "lvjiang-stats"

_PKG_ROOT = Path(__file__).resolve().parents[2]        # ops/stats-client/
_WRANGLER_TOML = _PKG_ROOT.parent / "stats-worker" / "wrangler.toml"


def _default_data_dir() -> Path:
    """``config.json`` 自身的落脚点——``STATS_CLIENT_DATA_DIR`` 环境变量覆盖，
    否则是包内 ``data/``。这是解“先有鸡还是先有蛋”的锚点：配置页里的
    "本地数据目录"字段（``Config.data_dir``）决定的是缓存库放哪，不影响
    config.json 自己放哪——不然找配置文件本身都不知道去哪找。测试用这个
    环境变量做隔离，不必碰任何私有属性。"""
    env = os.environ.get("STATS_CLIENT_DATA_DIR")
    return Path(env) if env else _PKG_ROOT / "data"


def _read_wrangler_database() -> tuple[str, str]:
    """从 wrangler.toml 里抠 database_id/database_name，不引入 toml 依赖。

    wrangler.toml 是本项目自己维护的部署配置，格式简单固定
    （``database_id = "..."`` / ``database_name = "..."``），用正则够用；
    真读不到就退回 :data:`_FALLBACK_DATABASE_ID`。
    """
    try:
        text = _WRANGLER_TOML.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK_DATABASE_ID, _FALLBACK_DATABASE_NAME
    m_id = re.search(r'database_id\s*=\s*"([^"]+)"', text)
    m_name = re.search(r'database_name\s*=\s*"([^"]+)"', text)
    return (m_id.group(1) if m_id else _FALLBACK_DATABASE_ID,
            m_name.group(1) if m_name else _FALLBACK_DATABASE_NAME)


@dataclass
class Config:
    account_id: str = ""
    database_id: str = ""
    database_name: str = ""
    data_dir: str = ""
    auto_sync: bool = True
    # 仅用于配置页展示"这是从环境变量来的还是本地保存的"，不参与持久化
    account_id_from_env: bool = field(default=False, compare=False)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else _default_data_dir()

    @property
    def db_path(self) -> Path:
        return self.data_path / "stats-cache.sqlite3"

    def is_ready(self) -> bool:
        return bool(self.account_id and self.database_id)


def _config_path(data_dir: Path | None = None) -> Path:
    return (data_dir or _default_data_dir()) / "config.json"


def load_config() -> Config:
    """读取本地保存的配置，环境变量覆盖 account_id。永不返回 token。"""
    path = _config_path()
    saved: dict = {}
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}

    default_id, default_name = _read_wrangler_database()
    env_account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    cfg = Config(
        account_id=env_account or saved.get("account_id", ""),
        database_id=saved.get("database_id") or default_id,
        database_name=saved.get("database_name") or default_name,
        data_dir=saved.get("data_dir", ""),
        auto_sync=saved.get("auto_sync", True),
        account_id_from_env=bool(env_account),
    )
    return cfg


def save_config(cfg: Config) -> None:
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    path = _config_path(cfg.data_path)
    payload = asdict(cfg)
    payload.pop("account_id_from_env", None)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # 部分文件系统（如某些网络盘）不支持 chmod，不影响功能


def get_api_token(cfg: Config) -> str | None:
    """优先环境变量，其次密钥环，最后本地 0600 文件。"""
    env = os.environ.get("CLOUDFLARE_API_TOKEN")
    if env:
        return env
    try:
        import keyring
        token = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if token:
            return token
    except Exception:
        pass  # 无密钥环后端（常见于无桌面环境的服务器/CI）时退化到文件
    secret_path = cfg.data_path / "token.secret"
    if secret_path.exists():
        try:
            return secret_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def set_api_token(cfg: Config, token: str) -> None:
    """写入密钥环；失败（无后端）时退化为 0600 本地文件。"""
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, token)
        return
    except Exception:
        pass
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    secret_path = cfg.data_path / "token.secret"
    secret_path.write_text(token, encoding="utf-8")
    try:
        secret_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
