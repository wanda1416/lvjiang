"""SessionManager 测试

覆盖 users/{username}.json 的 load/save/save_fn。
"""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from lvjiang.core.config.users import SessionManager


@pytest.fixture
def mgr(tmp_path):
    """使用 tmp_path 构造隔离的 SessionManager"""
    return SessionManager(users_dir=tmp_path)


class TestLoad:
    def test_load_existing_file(self, mgr, tmp_path):
        data = {"current_user": "张三", "score": 100}
        (tmp_path / "张三.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        result = mgr.load("张三")
        assert result["current_user"] == "张三"
        assert result["score"] == 100

    def test_load_missing_file_returns_default(self, mgr):
        result = mgr.load("不存在")
        assert result == {"current_user": "不存在"}

    def test_load_corrupted_json_returns_default(self, mgr, tmp_path):
        (tmp_path / "损坏.json").write_text("{invalid json!!", encoding="utf-8")
        result = mgr.load("损坏")
        assert result == {"current_user": "损坏"}


class TestSave:
    def test_save_creates_file(self, mgr, tmp_path):
        mgr.save("新用户", {"current_user": "新用户", "level": 5})
        path = tmp_path / "新用户.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["current_user"] == "新用户"
        assert data["level"] == 5

    def test_save_overwrites_existing(self, mgr, tmp_path):
        mgr.save("用户", {"v": 1})
        mgr.save("用户", {"v": 2})
        data = json.loads((tmp_path / "用户.json").read_text(encoding="utf-8"))
        assert data["v"] == 2

    def test_save_preserves_chinese(self, mgr, tmp_path):
        mgr.save("中文用户", {"name": "测试中文"})
        content = (tmp_path / "中文用户.json").read_text(encoding="utf-8")
        assert "中文" in content  # ensure_ascii=False

    def test_concurrent_save_keeps_valid_json(self, mgr, tmp_path):
        """并发保存同一用户时，最终文件不能出现半截 JSON。"""
        def save_one(i: int):
            mgr.save("并发用户", {"current_user": "并发用户", "counter": i})

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(save_one, range(50)))

        data = json.loads((tmp_path / "并发用户.json").read_text(encoding="utf-8"))
        assert data["current_user"] == "并发用户"
        assert isinstance(data["counter"], int)


class TestUpdate:
    def test_update_merges_with_latest_file(self, mgr, tmp_path):
        mgr.save("用户", {"current_user": "用户", "a": 1})

        def mutate(data: dict):
            data["b"] = 2

        result = mgr.update("用户", mutate)
        assert result["a"] == 1
        assert result["b"] == 2
        saved = json.loads((tmp_path / "用户.json").read_text(encoding="utf-8"))
        assert saved == result

    def test_update_raises_on_mutator_error(self, mgr):
        """mutator 抛异常时 update() 应向上传播，不吞掉"""
        mgr.save("用户", {"current_user": "用户", "a": 1})

        def bad_mutate(data: dict):
            raise ValueError("模拟失败")

        with pytest.raises(ValueError, match="模拟失败"):
            mgr.update("用户", bad_mutate)


class TestSaveFn:
    def test_save_fn_returns_callable(self, mgr):
        fn = mgr.save_fn("用户", {"data": 1})
        assert callable(fn)

    def test_save_fn_writes_on_call(self, mgr, tmp_path):
        session = {"counter": 0}
        fn = mgr.save_fn("用户", session)
        session["counter"] = 42
        fn()
        data = json.loads((tmp_path / "用户.json").read_text(encoding="utf-8"))
        assert data["counter"] == 42

    def test_save_fn_captures_reference(self, mgr, tmp_path):
        """save_fn 捕获的是引用，不是快照"""
        session = {"val": "initial"}
        fn = mgr.save_fn("用户", session)
        session["val"] = "updated"
        fn()
        data = json.loads((tmp_path / "用户.json").read_text(encoding="utf-8"))
        assert data["val"] == "updated"
