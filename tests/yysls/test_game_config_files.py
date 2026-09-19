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
        remote_dir=remote,
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
    assert basic["content_version"] == equipment["content_version"] == 1


def test_legacy_levels_and_seasons_file_is_renamed_in_local_and_remote(tmp_path: Path):
    """levels_and_seasons.yaml 改名为 seasons.yaml：本地/远程层旧文件首次加载时
    原地改名，用户自定义的等级/赛季不丢。"""
    resolver = _resolver(tmp_path)
    legacy = resolver.local_dir / "yysls/game_config/levels_and_seasons.yaml"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(yaml.safe_dump({
        "content_version": 1,
        "season_configs": [{"season_number": 99, "start_date": "2030-01-01",
                            "equip_level": 130}],
    }, allow_unicode=True), encoding="utf-8")

    data = load_game_config(resolver)

    assert not legacy.exists()
    assert (resolver.local_dir / "yysls/game_config/seasons.yaml").is_file()
    assert any(int(item.get("season_number", 0)) == 99
               for item in data["season_configs"])


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
