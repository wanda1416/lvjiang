"""UserConfigManager CRUD 测试

覆盖 User 数据类序列化 + UserConfigManager 的增删查改 + 激活用户切换。
"""

import pytest

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.user_config import User, UserConfigManager


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """隔离的 session 环境"""
    from lvjiang import constants
    session_path = tmp_path / "session.json"
    monkeypatch.setattr(constants, "SESSION_PATH", session_path)
    reset_session_store()
    yield session_path
    reset_session_store()


# ─── User 数据类 ──────────────────────────────────────────

class TestUser:
    def test_to_dict(self):
        u = User(name="张三", created_at="2026-01-01T00:00:00")
        d = u.to_dict()
        assert d == {"name": "张三", "created_at": "2026-01-01T00:00:00"}

    def test_from_dict(self):
        u = User.from_dict({"name": "李四", "created_at": "2026-06-15"})
        assert u.name == "李四"
        assert u.created_at == "2026-06-15"

    def test_from_dict_missing_fields(self):
        u = User.from_dict({})
        assert u.name == ""
        assert u.created_at == ""

    def test_roundtrip(self):
        u = User(name="王五", created_at="2026-08-01T12:00:00")
        u2 = User.from_dict(u.to_dict())
        assert u2.name == u.name
        assert u2.created_at == u.created_at


# ─── UserConfigManager ────────────────────────────────────

class TestUserConfigManagerInit:
    def test_creates_default_user_when_empty(self, session_env):
        mgr = UserConfigManager()
        assert mgr.list_users() == ["默认用户"]
        assert mgr.get_active_user_name() == "默认用户"

    def test_loads_existing_users_from_session(self, session_env):
        import json
        session_env.write_text(json.dumps({
            "users": [
                {"name": "用户A", "created_at": "2026-01-01"},
                {"name": "用户B", "created_at": "2026-02-01"},
            ],
            "active_user": "用户B",
        }), encoding="utf-8")
        reset_session_store()
        mgr = UserConfigManager()
        assert set(mgr.list_users()) == {"用户A", "用户B"}
        assert mgr.get_active_user_name() == "用户B"

    def test_active_user_reset_if_not_found(self, session_env):
        import json
        session_env.write_text(json.dumps({
            "users": [{"name": "用户A", "created_at": ""}],
            "active_user": "不存在的用户",
        }), encoding="utf-8")
        reset_session_store()
        mgr = UserConfigManager()
        assert mgr.get_active_user_name() == ""


class TestUserConfigManagerCRUD:
    def test_create_user_success(self, session_env):
        mgr = UserConfigManager()
        assert mgr.create_user("新用户") is True
        assert "新用户" in mgr.list_users()

    def test_create_user_duplicate_rejected(self, session_env):
        mgr = UserConfigManager()
        mgr.create_user("重复用户")
        assert mgr.create_user("重复用户") is False

    def test_create_user_empty_name_rejected(self, session_env):
        mgr = UserConfigManager()
        assert mgr.create_user("") is False

    def test_get_user_exists(self, session_env):
        mgr = UserConfigManager()
        mgr.create_user("测试用户")
        u = mgr.get_user("测试用户")
        assert u is not None
        assert u.name == "测试用户"

    def test_get_user_not_found(self, session_env):
        mgr = UserConfigManager()
        assert mgr.get_user("不存在") is None

    def test_delete_user_success(self, session_env):
        mgr = UserConfigManager()
        mgr.create_user("待删除")
        assert mgr.delete_user("待删除") is True
        assert "待删除" not in mgr.list_users()

    def test_delete_user_not_found(self, session_env):
        mgr = UserConfigManager()
        assert mgr.delete_user("不存在") is False

    def test_delete_last_user_rejected(self, session_env):
        mgr = UserConfigManager()
        # 默认只有一个用户
        assert mgr.delete_user("默认用户") is False
        assert "默认用户" in mgr.list_users()

    def test_delete_active_user_switches(self, session_env):
        mgr = UserConfigManager()
        mgr.create_user("用户B")
        mgr.set_active_user("用户B")
        mgr.delete_user("用户B")
        # 激活用户应切换到剩余用户
        active = mgr.get_active_user_name()
        assert active != "用户B"
        assert active in mgr.list_users()

    def test_reorder_users_success(self, session_env):
        mgr = UserConfigManager()
        mgr.create_user("B")
        mgr.create_user("C")
        names = mgr.list_users()
        reversed_names = list(reversed(names))
        assert mgr.reorder_users(reversed_names) is True
        assert mgr.list_users() == reversed_names

    def test_reorder_users_mismatch_rejected(self, session_env):
        mgr = UserConfigManager()
        assert mgr.reorder_users(["错误名称"]) is False
        assert mgr.reorder_users([]) is False

    def test_set_active_user_success(self, session_env):
        mgr = UserConfigManager()
        mgr.create_user("目标用户")
        assert mgr.set_active_user("目标用户") is True
        assert mgr.get_active_user_name() == "目标用户"

    def test_set_active_user_not_found(self, session_env):
        mgr = UserConfigManager()
        assert mgr.set_active_user("不存在") is False

    def test_persistence_across_instances(self, session_env):
        """验证数据持久化到 session.json"""
        mgr1 = UserConfigManager()
        mgr1.create_user("持久用户")
        mgr1.set_active_user("持久用户")

        # 新建实例应能加载
        reset_session_store()
        mgr2 = UserConfigManager()
        assert "持久用户" in mgr2.list_users()
        assert mgr2.get_active_user_name() == "持久用户"


class TestUsernameValidation:
    """用户名会直接当文件名（users/{name}.json），也会拼进 profile 告警的
    复合键（{user}:{key}:...）。不校验的话：'../x' 能写出 users 目录之外、
    'a/b' 凭空建子目录、含 ':' 的名字在 Windows 上存不了且会让告警键按 ':'
    切分时错位，把有效记录当过期的删掉。
    """

    @pytest.mark.parametrize("name", [
        "默认用户", "张三", "user_01", "my-account", "A1", "测试User_2",
    ])
    def test_accepts_chinese_and_common_ids(self, name):
        from lvjiang.core.user_config import is_valid_username
        assert is_valid_username(name)

    @pytest.mark.parametrize("name", [
        "", "../逃逸", "a/b", "a\\b", "含:冒号", "a b", "a.b", "x" * 33, "emoji😀",
    ])
    def test_rejects_unsafe_names(self, name):
        from lvjiang.core.user_config import is_valid_username
        assert not is_valid_username(name)

    def test_create_user_rejects_invalid(self, session_env):
        mgr = UserConfigManager()
        assert mgr.create_user("../逃逸") is False
        assert mgr.create_user("含:冒号") is False
        assert mgr.create_user("正常用户") is True
