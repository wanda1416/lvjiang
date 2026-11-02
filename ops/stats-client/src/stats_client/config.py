"""本机设置、客户端配置与只读凭据。

布局（参照 ``admin/`` 的做法，全部落在被 Git 忽略的 ``.local/`` 里）：

```text
ops/stats-client/
  .local/
    settings.toml          # secrets_dir = '...'（机器相关，不入库）
    client.json            # account_id / database_id / database_name / auto_sync
    stats-cache.sqlite3    # D1 快照缓存
```

凭据边界（**只读、不可写**）：Cloudflare API Token 只从
``<secrets_dir>/cloudflare_api_token.txt`` 读，程序**永不写它**，也不再读环境变量或
系统密钥环——想换 token 就去改那个文件，改完刷新页面即可。

``secrets_dir`` 没有默认值：``settings.toml`` 缺失、未填写或填成相对路径时，加载阶段
直接报错。这是 admin 那边踩过的坑——兜底到某个隐含位置，会让"以为已经改到云盘、实际
仍在读旧目录"的失误拖到同步失败之后才发现。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

import tomllib

logger = logging.getLogger(__name__)

LOCAL_DIR_NAME = ".local"
SETTINGS_NAME = "settings.toml"
CLIENT_CONFIG_NAME = "client.json"
CACHE_DB_NAME = "stats-cache.sqlite3"
TOKEN_FILE_NAME = "cloudflare_api_token.txt"

# database_id 本身不是密钥（见 ops/stats-worker/README："database_id 不是凭据"），
# 这里保留一份兜底值，仅在 wrangler.toml 读取失败时使用——正常路径总是从文件读，
# 兜底只防文件缺失/改名。
_FALLBACK_DATABASE_ID = "342147bc-54a7-4114-8c97-73e939352f54"
_FALLBACK_DATABASE_NAME = "lvjiang-stats"

_PKG_ROOT = Path(__file__).resolve().parents[2]        # ops/stats-client/
_WRANGLER_TOML = _PKG_ROOT.parent / "stats-worker" / "wrangler.toml"


class SettingsError(ValueError):
    """本机设置缺失或不完整；不提供隐含默认值（与 admin 同一套约定）。"""


# ─── 路径 ────────────────────────────────────────────────────

def workspace_root(root: Path | None = None) -> Path:
    """检出根（``.local/`` 的父目录）。``STATS_CLIENT_ROOT`` 只给测试隔离用。"""
    if root is not None:
        return Path(root)
    env = os.environ.get("STATS_CLIENT_ROOT")
    return Path(env) if env else _PKG_ROOT


def local_dir(root: Path | None = None) -> Path:
    return workspace_root(root) / LOCAL_DIR_NAME


def settings_path(root: Path | None = None) -> Path:
    return local_dir(root) / SETTINGS_NAME


def client_config_path(root: Path | None = None) -> Path:
    return local_dir(root) / CLIENT_CONFIG_NAME


# ─── 本机设置与只读凭据 ──────────────────────────────────────

@dataclass(frozen=True)
class LocalSettings:
    """``.local/settings.toml`` 的解析结果；``secrets_dir`` 必填且必须是绝对路径。"""

    secrets_dir: Path

    @classmethod
    def load(cls, root: Path | None = None) -> LocalSettings:
        path = settings_path(root)
        if not path.is_file():
            raise SettingsError(
                f"缺少本机设置 {path}：请从 settings.example.toml 复制一份，"
                f"填写 secrets_dir")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise SettingsError(f"本机设置 {path} 不可用：{error}") from error
        raw = data.get("secrets_dir", "")
        if not isinstance(raw, str):
            raise SettingsError(f"本机设置 {path} 的 secrets_dir 必须是文本")
        if not raw.strip():
            raise SettingsError(
                f"本机设置 {path} 未填写 secrets_dir：秘密目录没有默认值，请显式指定")
        configured = Path(raw).expanduser()
        if not configured.is_absolute():
            raise SettingsError(
                f"本机设置 {path} 的 secrets_dir 必须是绝对路径：{configured}")
        return cls(configured)

    @property
    def token_path(self) -> Path:
        return self.secrets_dir / TOKEN_FILE_NAME

    def read_token(self) -> str | None:
        """读只读 token 文件；缺失或为空时返回 None（不抛异常，便于界面显示状态）。"""
        try:
            text = self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return text or None


def token_status(root: Path | None = None) -> dict:
    """给界面用的凭据状态：文件路径、是否存在、以及设置本身的问题。"""
    try:
        settings = LocalSettings.load(root)
    except SettingsError as error:
        return {"path": "", "exists": False, "ready": False, "error": str(error)}
    token = settings.read_token()
    return {
        "path": str(settings.token_path),
        "exists": settings.token_path.is_file(),
        "ready": token is not None,
        "error": "" if token else f"{settings.token_path} 不存在或为空",
    }


def get_api_token(root: Path | None = None) -> str | None:
    """当前生效的 token：只来自 ``<secrets_dir>/cloudflare_api_token.txt``。"""
    try:
        settings = LocalSettings.load(root)
    except SettingsError:
        return None
    return settings.read_token()


# ─── 客户端配置 ──────────────────────────────────────────────

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
    data_dir: str = ""          # 空 = 用 .local/；填了就把缓存库放过去
    auto_sync: bool = True

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else local_dir()

    @property
    def db_path(self) -> Path:
        return self.data_path / CACHE_DB_NAME

    def is_ready(self) -> bool:
        return bool(self.account_id and self.database_id)


def migrate_legacy_data(root: Path | None = None) -> None:
    """把旧 ``data/`` 里的配置与缓存搬进 ``.local/``（只补缺失项，不覆盖、不删除）。

    旧版本把 ``config.json`` 与缓存库放在 ``data/`` 下；目录改名后如果不迁移，
    用户看到的就是"配置没了、缓存从零开始"。旧文件读完可手动删除。
    """
    legacy = workspace_root(root) / "data"
    target = local_dir(root)
    for src_name, dst_name in (
        ("config.json", CLIENT_CONFIG_NAME),
        (CACHE_DB_NAME, CACHE_DB_NAME),
    ):
        source, destination = legacy / src_name, target / dst_name
        if not source.is_file() or destination.exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        logger.info("已把旧 data/%s 迁移到 %s/%s（旧文件可删除）",
                    src_name, LOCAL_DIR_NAME, dst_name)


def load_config(root: Path | None = None) -> Config:
    """读 ``.local/client.json``；缺失字段用 wrangler.toml 的默认值补。"""
    migrate_legacy_data(root)
    path = client_config_path(root)
    saved: dict = {}
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}

    default_id, default_name = _read_wrangler_database()
    return Config(
        account_id=saved.get("account_id", ""),
        database_id=saved.get("database_id") or default_id,
        database_name=saved.get("database_name") or default_name,
        data_dir=saved.get("data_dir", ""),
        auto_sync=saved.get("auto_sync", True),
    )


def save_config(cfg: Config, root: Path | None = None) -> None:
    path = client_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # 部分文件系统（如某些网盘）不支持 chmod，不影响功能
