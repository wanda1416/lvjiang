"""燕云七文件游戏配置的物理边界。"""

from pathlib import Path

import yaml

from lvjiang.apps.yysls.config.game_config_files import (
    GAME_CONFIG_FILES,
    load_game_config,
    save_game_config,
)
from lvjiang.core.config.resolver import ConfigResolver


def _resolver(tmp_path: Path) -> ConfigResolver:
    system = tmp_path / "system"
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    for root in (system, local, remote):
        root.mkdir()
    source = Path("config/system/yysls/game_config")
    for source_file in source.glob("*.yaml"):
        target = system / "yysls/game_config" / source_file.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source_file.read_bytes())
    return ConfigResolver(
        system_dir=system,
        local_dir=local,
        dev_mode=True,
    )


def test_game_config_has_exactly_seven_peer_files():
    assert len(GAME_CONFIG_FILES) == 7
    assert {Path(path).name for path in GAME_CONFIG_FILES} == {
        "basic.yaml",
        "seasons.yaml",
        "affixes.yaml",
        "equipment.yaml",
        "martial_arts.yaml",
        "schools.yaml",
        "playstyles.yaml",
    }
    assert all(Path(path).parent.as_posix() == "yysls/game_config"
               for path in GAME_CONFIG_FILES)


def test_save_routes_sections_to_their_owner_files(tmp_path):
    resolver = _resolver(tmp_path)
    data = load_game_config(resolver)
    data["basic_config"]["equipment_cooldown_days"] = 9
    data["weapon_types"][0]["name"] = "测试武器"

    save_game_config(data, resolver)

    basic = yaml.safe_load(
        (resolver.system_dir / "yysls/game_config/basic.yaml").read_text(encoding="utf-8"))
    equipment = yaml.safe_load(
        (resolver.system_dir / "yysls/game_config/equipment.yaml").read_text(encoding="utf-8"))
    assert basic["basic_config"]["equipment_cooldown_days"] == 9
    assert "weapon_types" not in basic
    assert equipment["weapon_types"][0]["name"] == "测试武器"
    assert "basic_config" not in equipment


def test_save_only_writes_files_whose_content_changed(tmp_path):
    """只改一个领域时其余六份不写。

    无条件写七份会让「只改了词条别名」也因另一个文件在编辑器打开后被别的
    窗口保存，而整次保存要求覆盖确认。
    """
    resolver = _resolver(tmp_path)
    data = load_game_config(resolver)
    data["basic_config"]["equipment_cooldown_days"] = 9

    written = save_game_config(data, resolver)

    assert written == ["yysls/game_config/basic.yaml"]


def test_save_reports_every_changed_file(tmp_path):
    resolver = _resolver(tmp_path)
    data = load_game_config(resolver)
    data["basic_config"]["equipment_cooldown_days"] = 9
    data["schools"]["鸣金·虹"]["transmute_pool"] = ["最大外功攻击"]

    written = save_game_config(data, resolver)

    assert written == [
        "yysls/game_config/basic.yaml",
        "yysls/game_config/schools.yaml",
    ]


def test_save_is_noop_when_nothing_changed(tmp_path):
    resolver = _resolver(tmp_path)
    data = load_game_config(resolver)
    before = {
        rel_path: (resolver.system_dir / rel_path).stat().st_mtime_ns
        for rel_path in GAME_CONFIG_FILES
    }

    assert save_game_config(data, resolver) == []

    assert {
        rel_path: (resolver.system_dir / rel_path).stat().st_mtime_ns
        for rel_path in GAME_CONFIG_FILES
    } == before


def test_every_top_level_section_is_owned_by_one_file(tmp_path):
    """顶层键必须全部登记归属；未登记的键保存时会被静默丢弃。"""
    resolver = _resolver(tmp_path)
    for rel_path, sections in GAME_CONFIG_FILES.items():
        document = yaml.safe_load(
            (resolver.system_dir / rel_path).read_text(encoding="utf-8")) or {}
        unknown = set(document) - {"content_version"} - set(sections)
        assert not unknown, f"{rel_path} 有未登记的顶层键: {sorted(unknown)}"


def test_old_season_file_is_diagnosed_without_renaming(tmp_path: Path, monkeypatch):
    resolver = _resolver(tmp_path)
    messages = []
    monkeypatch.setattr(
        "lvjiang.apps.yysls.config.game_config_files.logger.warning", messages.append)
    for root in (resolver.local_dir,):
        legacy = root / "yysls/game_config/levels_and_seasons.yaml"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("season_configs: []\n", encoding="utf-8")
    load_game_config(resolver)
    for root in (resolver.local_dir,):
        assert (root / "yysls/game_config/levels_and_seasons.yaml").read_text(
            encoding="utf-8") == "season_configs: []\n"
        assert not (root / "yysls/game_config/seasons.yaml").exists()
    assert len(messages) == 1
    assert all("手工改名" in message and "勿直接覆盖" in message for message in messages)


def test_equip_display_lives_in_session_settings_not_game_config(monkeypatch):
    from lvjiang.apps.yysls.config.equip_display import (
        DEFAULTS,
        load_equip_display,
        save_equip_display,
    )

    store: dict = {}
    monkeypatch.setattr(
        "lvjiang.apps.yysls.config.equip_display.load_settings", lambda: store)
    monkeypatch.setattr(
        "lvjiang.apps.yysls.config.equip_display.save_settings",
        lambda values: store.update(values))

    assert load_equip_display() == DEFAULTS
    assert DEFAULTS == {
        "name_font_size": 13, "level_font_size": 12, "affix_font_size": 12,
        "card_min_height": 180, "grid_columns": 4,
    }
    save_equip_display({"affix_font_size": 14, "grid_columns": 5, "junk": 1})
    assert store == {"equip_display": {"affix_font_size": 14, "grid_columns": 5}}
    assert load_equip_display()["affix_font_size"] == 14
    assert load_equip_display()["name_font_size"] == 13
    from lvjiang.apps.yysls.config.game_config_files import GAME_CONFIG_SECTION_FILES

    assert "equip_display" not in GAME_CONFIG_SECTION_FILES
