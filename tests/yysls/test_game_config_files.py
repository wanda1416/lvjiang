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
        "levels_and_seasons.yaml",
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
        (resolver.system_dir / "yysls/game_config/basic.yaml").read_text())
    equipment = yaml.safe_load(
        (resolver.system_dir / "yysls/game_config/equipment.yaml").read_text())
    assert basic["basic_config"]["equipment_cooldown_days"] == 9
    assert "weapon_types" not in basic
    assert equipment["weapon_types"][0]["name"] == "测试武器"
    assert "basic_config" not in equipment
    assert basic["content_version"] == equipment["content_version"] == 1
