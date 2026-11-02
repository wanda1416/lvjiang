"""本机设置与只读凭据的落点契约。

这一层出错的代价是"静默读错目录"——同步一直 401 或读回旧配置，却看不出原因。
所以这里把三件事钉死：

1. ``.local/settings.toml`` 的 ``secrets_dir`` 必填、必须是绝对路径，没有任何回调兜底；
2. token 只从 ``<secrets_dir>/cloudflare_api_token.txt`` 读，**程序不写**；
3. 配置与缓存落在 ``.local/``，旧 ``data/`` 只做一次性拷贝迁移。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stats_client import config as config_mod  # noqa: E402


def _write_settings(root: Path, value: str) -> Path:
    local = root / ".local"
    local.mkdir(parents=True, exist_ok=True)
    path = local / "settings.toml"
    path.write_text(f"secrets_dir = '{value}'\n", encoding="utf-8")
    return path


def _write_token(secrets: Path, token: str = "tok-123\n") -> Path:
    secrets.mkdir(parents=True, exist_ok=True)
    path = secrets / "cloudflare_api_token.txt"
    path.write_text(token, encoding="utf-8")
    return path


class TestSettings:

    def test_missing_settings_file_names_the_path(self, tmp_path):
        with pytest.raises(config_mod.SettingsError) as exc:
            config_mod.LocalSettings.load(tmp_path)
        assert "settings.toml" in str(exc.value)
        assert "secrets_dir" in str(exc.value)

    def test_empty_secrets_dir_is_rejected(self, tmp_path):
        _write_settings(tmp_path, "")
        with pytest.raises(config_mod.SettingsError, match="没有默认值"):
            config_mod.LocalSettings.load(tmp_path)

    def test_relative_secrets_dir_is_rejected(self, tmp_path):
        _write_settings(tmp_path, "relative/secrets")
        with pytest.raises(config_mod.SettingsError, match="绝对路径"):
            config_mod.LocalSettings.load(tmp_path)

    def test_absolute_secrets_dir_is_loaded(self, tmp_path):
        secrets = tmp_path / "secrets"
        _write_settings(tmp_path, secrets.as_posix())
        assert config_mod.LocalSettings.load(tmp_path).secrets_dir == secrets

    def test_token_status_reports_problem_without_raising(self, tmp_path):
        status = config_mod.token_status(tmp_path)
        assert status["ready"] is False
        assert "settings.toml" in status["error"]


class TestToken:

    def test_token_is_read_from_secrets_dir(self, tmp_path):
        secrets = tmp_path / "secrets"
        _write_settings(tmp_path, secrets.as_posix())
        _write_token(secrets, "tok-abc\n")
        assert config_mod.get_api_token(tmp_path) == "tok-abc"

    def test_missing_token_file_returns_none_with_path_hint(self, tmp_path):
        secrets = tmp_path / "secrets"
        _write_settings(tmp_path, secrets.as_posix())
        secrets.mkdir(parents=True)
        assert config_mod.get_api_token(tmp_path) is None
        status = config_mod.token_status(tmp_path)
        assert status["ready"] is False
        assert status["exists"] is False
        assert str(secrets / "cloudflare_api_token.txt") in status["error"]

    def test_blank_token_file_counts_as_missing(self, tmp_path):
        secrets = tmp_path / "secrets"
        _write_settings(tmp_path, secrets.as_posix())
        _write_token(secrets, "   \n")
        assert config_mod.get_api_token(tmp_path) is None

    def test_client_never_writes_the_token(self, tmp_path):
        """凭据不可写：模块里不应存在写 token 的入口（连回退文件都没有）。"""
        secrets = tmp_path / "secrets"
        _write_settings(tmp_path, secrets.as_posix())
        token_path = _write_token(secrets, "tok-abc\n")
        before = token_path.read_bytes()
        assert not hasattr(config_mod, "set_api_token")
        config_mod.save_config(config_mod.Config(account_id="a", database_id="d"),
                               tmp_path)
        assert token_path.read_bytes() == before

    def test_legacy_env_and_file_fallbacks_ignored(self, tmp_path, monkeypatch):
        """旧路径（环境变量 / 密钥环 / token.secret）都不再是凭据来源。"""
        secrets = tmp_path / "secrets"
        _write_settings(tmp_path, secrets.as_posix())
        secrets.mkdir(parents=True)
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "from-env")
        (tmp_path / ".local" / "token.secret").write_text("from-file",
                                                          encoding="utf-8")
        assert config_mod.get_api_token(tmp_path) is None


class TestLocalLayout:

    def test_config_lives_in_dot_local(self, tmp_path):
        cfg = config_mod.Config(account_id="a", database_id="d", database_name="n",
                                auto_sync=False)
        config_mod.save_config(cfg, tmp_path)
        assert (tmp_path / ".local" / "client.json").is_file()
        assert config_mod.load_config(tmp_path).account_id == "a"

    def test_cache_db_lives_in_dot_local(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATS_CLIENT_ROOT", str(tmp_path))
        cfg = config_mod.Config(account_id="a", database_id="d")
        assert cfg.db_path == tmp_path / ".local" / "stats-cache.sqlite3"

    def test_explicit_data_dir_overrides_cache_location(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATS_CLIENT_ROOT", str(tmp_path))
        other = tmp_path / "elsewhere"
        cfg = config_mod.Config(account_id="a", database_id="d",
                                data_dir=str(other))
        assert cfg.db_path == other / "stats-cache.sqlite3"


class TestLegacyMigration:

    def test_old_data_dir_is_copied_once(self, tmp_path):
        legacy = tmp_path / "data"
        legacy.mkdir()
        (legacy / "config.json").write_text(
            '{"account_id": "old", "database_id": "db"}', encoding="utf-8")
        (legacy / "stats-cache.sqlite3").write_bytes(b"fake-db")

        cfg = config_mod.load_config(tmp_path)

        assert cfg.account_id == "old"
        assert (tmp_path / ".local" / "client.json").is_file()
        assert (tmp_path / ".local" / "stats-cache.sqlite3").read_bytes() == b"fake-db"
        # 旧文件保留，不删不改——迁移只补缺失项
        assert (legacy / "config.json").is_file()

    def test_existing_local_config_is_not_overwritten(self, tmp_path):
        legacy = tmp_path / "data"
        legacy.mkdir()
        (legacy / "config.json").write_text('{"account_id": "old"}', encoding="utf-8")
        config_mod.save_config(config_mod.Config(account_id="new", database_id="db"),
                               tmp_path)

        assert config_mod.load_config(tmp_path).account_id == "new"
