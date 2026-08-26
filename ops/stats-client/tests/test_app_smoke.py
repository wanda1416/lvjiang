"""端到端冒烟测试：真的把 FastAPI app 跑起来（TestClient，不碰真实网络），
用依赖覆盖把 D1Client 换成内存里的假实现。

隔离手段：``STATS_CLIENT_DATA_DIR`` 环境变量决定 config.json/缓存库落在哪，
每个测试指向自己的 tmp_path，互不干扰、也不碰真实的
``ops/stats-client/data/``。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from stats_client import config as config_mod  # noqa: E402
from stats_client.app import create_app  # noqa: E402
from stats_client.deps import get_client  # noqa: E402
from stats_client.sync import Syncer  # noqa: E402
from tests.test_sync import FakeD1Client, _mkbatch, _mkdaily, _today_utc8  # noqa: E402


def _make_client(tmp_path, monkeypatch, seed: bool = True) -> TestClient:
    monkeypatch.setenv("STATS_CLIENT_DATA_DIR", str(tmp_path / "data"))
    cfg = config_mod.Config(account_id="acct-test", database_id="db-test",
                            database_name="lvjiang-stats",
                            auto_sync=False)  # 关掉自动同步，避免冒烟测试悄悄发真实请求
    config_mod.save_config(cfg)
    config_mod.set_api_token(cfg, "fake-token-for-tests")

    if seed:
        fake = FakeD1Client()
        today = _today_utc8()
        fake.daily = [_mkdaily(today, "i1"), _mkdaily(today, "i2")]
        fake.roll_batch = [_mkbatch("b1", "2026-08-20T00:00:00Z", today, n_events=2)]
        fake.installs_count = 5
        from stats_client.database import connect
        conn = connect(cfg.db_path)
        Syncer(conn, fake).run()
        conn.close()

    app = create_app()
    return TestClient(app)


class TestSetupFlow:
    def test_setup_page_renders(self, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch, seed=False)
        r = client.get("/setup")
        assert r.status_code == 200
        assert "Account ID" in r.text

    def test_save_keeps_env_account_id_despite_disabled(self, tmp_path, monkeypatch):
        """account_id 来自环境变量时表单里的 <input disabled> 不会被浏览器提交
        （模拟为提交空字符串）——保存不该因此把 account_id 当成"没填"报错。"""
        monkeypatch.setenv("STATS_CLIENT_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-from-env")
        import stats_client.routes.setup as setup_mod

        class _FakeVerifyClient:
            def __init__(self, **kwargs):
                pass

            def verify(self):
                pass  # 不打真实网络

        monkeypatch.setattr(setup_mod, "D1Client", _FakeVerifyClient)
        app = create_app()
        client = TestClient(app, follow_redirects=False)
        r = client.post("/setup", data={
            "account_id": "",  # disabled 输入框不会被提交
            "api_token": "tok",
            "database_id": "db-x",
            "database_name": "n",
        })
        assert r.status_code == 303, r.text
        cfg = config_mod.load_config()
        assert cfg.account_id == "acct-from-env"

    def test_root_redirects_to_setup_when_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATS_CLIENT_DATA_DIR", str(tmp_path / "empty"))
        app = create_app()
        client = TestClient(app, follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 303
        assert r.headers["location"] == "/setup"


class TestOverviewPage:
    def test_overview_renders_seeded_data(self, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        r = client.get("/")
        assert r.status_code == 200
        assert "今日 DAU" in r.text
        assert "dau-chart" in r.text


class TestRollsPage:
    def test_rolls_page_without_run_shows_form_only(self, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        r = client.get("/rolls")
        assert r.status_code == 200
        assert "生成报告" in r.text

    def test_rolls_page_with_run_generates_report(self, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        r = client.get("/rolls?run=1&top=5")
        assert r.status_code == 200
        assert "词条分布" in r.text

    def test_rolls_download_returns_markdown_attachment(self, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        r = client.get("/rolls/report.md")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        assert "attachment" in r.headers["content-disposition"]
        assert "调律词条分布分析报告" in r.text

    def test_rolls_download_empty_cache_returns_400(self, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch, seed=False)
        r = client.get("/rolls/report.md")
        assert r.status_code == 400


def _tuning_event(part, first_affix, roll2_affix, install_id="i1"):
    return {
        "schema": "yysls.tuning_session", "install_id": install_id,
        "date": _today_utc8(), "part": part, "weapon_type": "", "level": 80,
        "quality": "orange", "mode": "normal", "active_rule": "default",
        "season": "s1", "game_config_customized": False,
        "initial_affixes": [{"affix": first_affix, "cap_pct": 10.0,
                             "is_transferred": False}],
        "rolls": [{"affix": roll2_affix, "cap_pct": 20.0, "is_transferred": False,
                   "slot": 2, "food": "none", "resets": 0}],
        "stop_reason": "hit_target", "final_rating": "top",
        "total_rounds": 1, "resets": 0,
    }


class TestRollsSlotQuery:
    """首词条/部位下拉用的是 ops/stats-client/vocab/telemetry_vocab.json 里的
    规范枚举（见 vocab.py），不是本地缓存里"碰巧出现过"的值——这里用真实
    存在于该快照里的词条名（"最大外功攻击"/"最大鸣金攻击"，两个都是腿甲
    能出现的普通词条），保证测试跟真实游戏词条池对得上。
    """

    def _seed_slot_data(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATS_CLIENT_DATA_DIR", str(tmp_path / "data"))
        cfg = config_mod.Config(account_id="a", database_id="d", database_name="n",
                                auto_sync=False)
        config_mod.save_config(cfg)
        config_mod.set_api_token(cfg, "tok")
        import json as json_mod

        from stats_client.database import connect
        conn = connect(cfg.db_path)
        events = ([_tuning_event("leg", "最大外功攻击", "最小外功攻击", f"i{i}")
                  for i in range(20)]
                  + [_tuning_event("leg", "最大鸣金攻击", "最小鸣金攻击", f"j{i}")
                     for i in range(20)])
        conn.execute(
            "INSERT INTO remote_roll_batch (batch_id, install_id, day, app_version, "
            "plugin, n_events, payload, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("b1", "i0", _today_utc8(), "0.7.0", "yysls", len(events),
             json_mod.dumps(events), "2026-08-20T00:00:00Z"))
        conn.close()
        return TestClient(create_app())

    def test_first_affix_dropdown_includes_never_observed_canonical_values(
            self, tmp_path, monkeypatch):
        """本地缓存只出现过"最大外功攻击"这一个首词条，但下拉框应该展示
        腿甲能出的全部普通词条（含从没在缓存里见过的），不是只列样本里
        碰巧出现的那个——这正是这次要修的东西。"""
        client = self._seed_slot_data(tmp_path, monkeypatch)
        r = client.get("/rolls?slot_part=leg")
        assert r.status_code == 200
        assert "最大外功攻击" in r.text  # 观测过的
        assert "最大破竹攻击" in r.text  # 腿甲能出、但这份缓存里从没见过

    def test_first_affix_dropdown_filtered_by_part(self, tmp_path, monkeypatch):
        """"最大无相攻击"在 game_config.yaml 的 affix_parts 里配的是只有
        武器能出；选了「腿甲」时不该出现在候选里。"""
        client = self._seed_slot_data(tmp_path, monkeypatch)
        r_weapon = client.get("/rolls?slot_part=weapon")
        r_leg = client.get("/rolls?slot_part=leg")
        assert "最大无相攻击" in r_weapon.text
        assert "最大无相攻击" not in r_leg.text

    def test_query_filters_by_part_and_first_affix(self, tmp_path, monkeypatch):
        client = self._seed_slot_data(tmp_path, monkeypatch)
        r = client.get(
            "/rolls?slot_part=leg&slot_first_affix=最大外功攻击&slot_target=2")
        assert r.status_code == 200
        assert "n=20" in r.text
        assert "最小外功攻击" in r.text
        # 「最大鸣金攻击」那批的产出（最小鸣金攻击）不该混进这次筛选结果里
        assert "筛选：第 2 格，部位=leg，首词条=最大外功攻击" in r.text

    def test_query_missing_target_shows_no_result(self, tmp_path, monkeypatch):
        client = self._seed_slot_data(tmp_path, monkeypatch)
        r = client.get("/rolls")
        assert r.status_code == 200
        assert "筛选：" not in r.text

    def test_given_slot_without_given_affix_is_an_error(self, tmp_path, monkeypatch):
        client = self._seed_slot_data(tmp_path, monkeypatch)
        r = client.get("/rolls?slot_target=2&slot_given_slot=2")
        assert r.status_code == 200
        assert "必须一起填" in r.text


class TestSyncPage:
    def test_sync_page_renders_history(self, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        r = client.get("/sync")
        assert r.status_code == 200
        assert "同步历史" in r.text

    def test_sync_run_uses_overridden_client(self, tmp_path, monkeypatch):
        """依赖覆盖确认没有打到真实 Cloudflare。"""
        client = _make_client(tmp_path, monkeypatch)
        fake = FakeD1Client()
        fake.installs_count = 99
        client.app.dependency_overrides[get_client] = lambda: fake
        r = client.post("/sync/run")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True

    def test_clear_cache_removes_db_file(self, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        cfg = config_mod.load_config()
        assert cfg.db_path.exists()
        r = client.post("/sync/clear-cache", follow_redirects=False)
        assert r.status_code == 303
        assert not cfg.db_path.exists()
