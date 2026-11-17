import json
from unittest.mock import patch

import pytest

from lvjiang.core.config.content_split import ensure_content_split
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.layout import storage
from lvjiang.core.scene import registry as registry_module
from lvjiang.core.scene.config import load_scene_manifest
from lvjiang.core.scene.definition import SceneRegistry


def write(p, s):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def system_tree(root):
    write(root / "scenes/activity.yaml", "name: Activity\n")
    write(
        root / "scenes/activity/activity_main.yaml",
        "key: activity_main\nname: System\nregions:\n  - key: btn\n    name: Button\n    type: func\n",
    )
    write(
        root / "scenes/_meta.json",
        json.dumps({"groups": [{"key": "activity", "scenes": ["activity_main"]}]}),
    )
    write(root / "layouts/desktop.yaml", "name: Desktop\nenv: desktop\n")
    write(root / "layouts/_meta.json", '{"layouts":["desktop"]}')
    write(
        root / "layouts/desktop/activity/activity_main.json",
        json.dumps(
            {
                "regions": [
                    {
                        "key": "btn",
                        "name": "Button",
                        "x_ratio": 0.1,
                        "y_ratio": 0.2,
                        "w_ratio": 0.3,
                        "h_ratio": 0.4,
                    }
                ]
            }
        ),
    )


@pytest.mark.parametrize("with_layout_manifest", [False, True])
def test_existing_local_coordinate_override_survives_upgrade(
    tmp_path, with_layout_manifest
):
    system, local = tmp_path / "system", tmp_path / "local"
    system_tree(system)
    write(
        local / "layouts/desktop/activity_main.json",
        json.dumps(
            {
                "regions": [
                    {
                        "key": "btn",
                        "name": "Button",
                        "x_ratio": 0.75,
                        "y_ratio": 0.2,
                        "w_ratio": 0.15,
                        "h_ratio": 0.4,
                    }
                ]
            }
        ),
    )
    if with_layout_manifest:
        write(
            local / "layouts.yaml",
            "layouts:\n  desktop:\n    name: Desktop\n    env: desktop\n",
        )
    ensure_content_split(system, local)
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    manifest = load_scene_manifest(resolver)
    group = next(k for k, items in manifest.groups.items() if "activity_main" in items)
    reg = SceneRegistry(
        resolver=resolver,
        scene_order=manifest.order,
        group_config=manifest.groups,
        group_names=manifest.group_names,
    )
    with (
        patch.object(registry_module, "get_registry", return_value=reg),
        patch.object(storage, "get_resolver", return_value=resolver),
        patch.object(storage, "_scene_group_of", return_value=group),
    ):
        layout = storage.load_layout_by_key("desktop")
    actual = layout.regions["activity_main"][0].x_ratio
    print(
        "aggregate=",
        with_layout_manifest,
        "loaded_x=",
        actual,
        "flat_exists=",
        (local / "layouts/desktop/activity_main.json").exists(),
        "wrong_group_exists=",
        (local / "layouts/desktop/general/activity_main.json").exists(),
    )
    assert actual == 0.75, "升级必须保留用户坐标覆盖"
