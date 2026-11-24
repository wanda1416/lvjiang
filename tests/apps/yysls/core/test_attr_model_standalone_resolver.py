"""属性模型管理器指向独立目录时使用单目录解析器，不碰客户端锁与索引。"""


def test_standalone_models_use_their_own_lock_without_client_index(tmp_path):
    from lvjiang.apps.yysls.core.attr_model.manager import AttrModelManager
    first = AttrModelManager(tmp_path / "first")._resolver
    second = AttrModelManager(tmp_path / "second")._resolver
    assert first.config_dir == tmp_path / "first"
    assert second.config_dir == tmp_path / "second"
    first.write_entity("sample.yaml", "name: 示例\n")
    second.write_entity("sample.yaml", "name: 第二个\n")
    assert not (tmp_path / "first/session/config.lock").exists()
    assert not (tmp_path / "second/session/config.lock").exists()
    assert not (tmp_path / "first/session/content.json").exists()
    assert not (tmp_path / "second/session/content.json").exists()
