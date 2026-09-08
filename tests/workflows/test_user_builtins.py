from types import SimpleNamespace

from lvjiang.core.user_config import User, save_user_metadata
from lvjiang.workflows.builtins import get_function


def test_user_get_reads_metadata_without_batch_attribute_injection(tmp_path):
    save_user_metadata(
        User(name="用户A", attributes={"account": "账号A", "role": "角色A"}),
        tmp_path,
    )
    user_get = get_function("user_get")
    assert user_get is not None
    engine = SimpleNamespace(users_dir=tmp_path)
    assert user_get(engine, "用户A", "account") == "账号A"
    assert user_get(engine, "用户A", "missing") is None
