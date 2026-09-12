"""隔离配置编辑会话不污染全局解析器，并通过公开 API 提交。"""

import yaml

from lvjiang.core.config.edit_session import ConfigEditSession
from lvjiang.core.config.resolver import ConfigResolver


def test_edit_session_isolated_until_commit(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    (system / "demo/entities").mkdir(parents=True)
    local.mkdir()
    (system / "demo/config.yaml").write_text("value: 1\n", encoding="utf-8")
    (system / "demo/entities/old.yaml").write_text(
        "key: old\nvalue: 1\n", encoding="utf-8")
    source = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)

    session = ConfigEditSession(
        source,
        merged_paths=("demo/config.yaml",),
        entity_dirs=("demo/entities",),
    )
    draft = session.resolver
    draft.save_merged("demo/config.yaml", {"value": 2})
    draft.delete_entity("demo/entities/old.yaml")
    draft.write_entity("demo/entities/new.yaml", "key: new\nvalue: 2\n")

    assert source.load_merged("demo/config.yaml") == {"value": 1}
    assert source.resolve_read("demo/entities/old.yaml") is not None
    assert source.resolve_read("demo/entities/new.yaml") is None

    session.commit()

    assert source.load_merged("demo/config.yaml") == {"value": 2}
    assert source.resolve_read("demo/entities/old.yaml") is None
    new_path = source.resolve_read("demo/entities/new.yaml")
    assert new_path is not None
    assert yaml.safe_load(new_path.read_text(encoding="utf-8")) == {
        "key": "new", "value": 2}
    session.close()


def test_close_discards_staged_changes(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    system.mkdir()
    local.mkdir()
    (system / "config.yaml").write_text("value: 1\n", encoding="utf-8")
    source = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)
    session = ConfigEditSession(
        source, merged_paths=("config.yaml",), entity_dirs=())

    session.resolver.save_merged("config.yaml", {"value": 9})
    session.close()

    assert source.load_merged("config.yaml") == {"value": 1}
