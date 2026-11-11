"""武学 / 流派 / 玩法：字典结构的键序、分层持久化与排序入口。

三张配置都是「名称 → 定义」的映射，**键序就是展示顺序**。

系统配置（开发）模式写的就是 system，顺序可以直接拖动调整；用户模式写的是
local 覆盖，而键序不参与 diff，所以用户侧不给排序入口：

- 改一个系统条目 → local 只存那一个键（加载时按 key 覆盖 system）；
- 新增条目 → local 只多一个键，追加在末尾；
- 系统后续新增的键照常进合并视图，系统条目不允许被删除；
- 顺序无法被用户改写。
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

pytest.importorskip("PyQt6.QtWidgets")

from lvjiang.apps.yysls.config import game_config_loader
from lvjiang.apps.yysls.config.game_config_files import (
    load_game_config,
    save_game_config,
)
from lvjiang.apps.yysls.ui.game_settings import factory_guard
from lvjiang.apps.yysls.ui.game_settings.order_list import reorder_mapping
from lvjiang.core.config.resolver import (
    DELETABLE_PATHS,
    PROTECTED_LIST_PATHS,
    REGISTRY_LIST_IDENTITY,
    REGISTRY_LIST_PATHS,
    ConfigResolver,
    compute_diff,
    merge_doc,
)

_REL = "yysls/game_config/schools.yaml"
_SYSTEM_CONFIG = Path("config/system/yysls/game_config")


def _shipped(name: str) -> dict:
    return yaml.safe_load((_SYSTEM_CONFIG / name).read_text(encoding="utf-8"))


def _save_merged(base: dict, desired: dict) -> dict:
    """等同 resolver.save_merged 在用户模式下的 diff 计算。"""
    return compute_diff(
        base, desired,
        REGISTRY_LIST_PATHS.get(_REL, ()), "",
        DELETABLE_PATHS.get(_REL, ()),
        PROTECTED_LIST_PATHS.get(_REL),
        REGISTRY_LIST_IDENTITY.get(_REL, {}))


def test_shipped_configs_are_name_keyed_maps():
    """结构契约：三份配置都是 名称→定义 的映射，不是列表。

    翻回列表而面板仍按 dict 读时，武学/玩法面板会直接空掉（
    `_entries()` 迭代 dict 的键、过滤掉所有字符串）。这条用例专门拦这种
    半截改动。
    """
    for filename, key in (("schools.yaml", "schools"),
                          ("martial_arts.yaml", "martial_arts"),
                          ("playstyles.yaml", "playstyles")):
        document = _shipped(filename)
        assert isinstance(document[key], dict), f"{filename} 的 {key} 必须是映射"
        assert document[key], f"{filename} 的 {key} 不能为空"
        assert all(isinstance(entry, dict) for entry in document[key].values())


def test_parsers_keep_key_order_and_skip_bad_entries(monkeypatch):
    class _Capture:
        def __init__(self) -> None:
            self.errors: list[str] = []

        def error(self, message, *args, **kwargs) -> None:
            self.errors.append(str(message))

        def warning(self, message, *args, **kwargs) -> None:
            pass

    capture = _Capture()

    class _Sink:
        _martial_arts: dict = {}
        _playstyles: dict = {}
        _schools: dict = {}

    sink = _Sink()
    game_config_loader.parse_arts(sink, {
        "martial_arts": {"乙": {"weapon": "剑", "attr": "鸣金"},
                         "甲": {"weapon": "枪", "attr": "鸣金"}},
        "playstyles": {"丁": {"arts": ["乙", "甲"]},
                       "丙": {"arts": []}},
    })
    assert list(sink._martial_arts) == ["乙", "甲"], "键序即展示顺序"
    assert list(sink._playstyles) == ["丁", "丙"]
    assert sink._playstyles["丁"]["arts"] == ["乙", "甲"]

    monkeypatch.setattr(game_config_loader, "logger", capture)
    game_config_loader.parse_schools(sink, {"schools": [{"name": "甲"}]})
    assert sink._schools == {}
    assert capture.errors, "旧列表结构必须报错"
    assert "名称→定义" in capture.errors[0], "错误信息要说明期望结构"
    assert "重启" in capture.errors[0], "错误信息要说明怎么生效"


def test_user_edit_stores_only_that_key():
    """改一个流派 → local 只存该键，其余键不被整份覆盖。"""
    base = _shipped("schools.yaml")
    desired = yaml.safe_load(yaml.safe_dump(base, allow_unicode=True))
    desired["schools"]["鸣金·虹"]["attr"] = "裂石"
    desired["schools"]["自建流派"] = {"attr": "鸣金"}

    diff = _save_merged(base, desired)
    assert list(diff) == ["schools"]
    assert list(diff["schools"]) == ["鸣金·虹", "自建流派"]

    merged = merge_doc(base, diff)
    assert merged["schools"]["鸣金·虹"]["attr"] == "裂石"
    assert list(merged["schools"])[-1] == "自建流派", "用户新增追加在末尾"
    assert list(merged["schools"])[:-1] == list(base["schools"])


def test_system_updates_stay_visible_and_system_entry_cannot_be_deleted():
    """系统后续新增/改值仍进得来；系统条目在用户层删不掉。"""
    base = _shipped("schools.yaml")
    desired = yaml.safe_load(yaml.safe_dump(base, allow_unicode=True))
    desired["schools"]["鸣金·虹"]["attr"] = "裂石"
    diff = _save_merged(base, desired)

    later = yaml.safe_load(yaml.safe_dump(base, allow_unicode=True))
    later["schools"]["系统新增流派"] = {"attr": "破竹"}
    merged = merge_doc(later, diff)
    assert "系统新增流派" in merged["schools"], "系统新增不能被 local 冻住"
    assert merged["schools"]["鸣金·虹"]["attr"] == "裂石"

    trimmed = yaml.safe_load(yaml.safe_dump(base, allow_unicode=True))
    trimmed["schools"].pop("鸣金·虹")
    assert _save_merged(base, trimmed) == {}, "删除系统流派应被白名单拦下"
    assert "鸣金·虹" in merge_doc(base, _save_merged(base, trimmed))["schools"]


def test_key_order_is_not_part_of_the_diff():
    """换键序不产生 diff：用户层写不出来，所以用户模式不给排序入口。"""
    base = _shipped("schools.yaml")
    reversed_doc = yaml.safe_load(yaml.safe_dump(base, allow_unicode=True))
    reversed_doc["schools"] = dict(reversed(list(reversed_doc["schools"].items())))
    assert _save_merged(base, reversed_doc) == {}


# ── 随包顺序契约：属性分组 → 流派内主副 → 玩法跟随流派 ────────

_ATTR_GROUP_ORDER = ["鸣金", "裂石", "牵丝", "破竹"]


def test_shipped_schools_are_grouped_by_attr():
    """流派按属性分段排列，段序固定：鸣金 → 裂石 → 牵丝 → 破竹。

    同一属性被拆成两段就说明有人按名字/武器重排过。
    """
    attrs = [str(cfg.get("attr") or "")
             for cfg in _shipped("schools.yaml")["schools"].values()]
    segments: list[str] = []
    for attr in attrs:
        if not segments or segments[-1] != attr:
            assert attr not in segments, f"属性分组被打断：{attrs}"
            segments.append(attr)
    assert segments == _ATTR_GROUP_ORDER


def test_shipped_martial_arts_follow_school_main_sub_order():
    """武学顺序 = 按流派顺序展开「主 → 副」后去重。"""
    expected: list[str] = []
    for cfg in _shipped("schools.yaml")["schools"].values():
        for side in ("main", "sub"):
            art = (cfg.get(side) or {}).get("martial_art")
            if art and art not in expected:
                expected.append(art)
    actual = list(_shipped("martial_arts.yaml")["martial_arts"])
    assert actual == expected


def test_shipped_playstyles_follow_school_group_order():
    """玩法按流派分段，段的先后跟随流派顺序（段内顺序自由）。"""
    schools = list(_shipped("schools.yaml")["schools"])
    groups: list[str] = []
    for cfg in _shipped("playstyles.yaml")["playstyles"].values():
        school = str(cfg.get("school") or "")
        if not groups or groups[-1] != school:
            assert school not in groups, "同一流派的玩法被拆成了多段"
            groups.append(school)

    bound = [school for school in groups if school]
    assert bound, "随包玩法都应绑定流派"
    ranks = [schools.index(school) for school in bound]
    assert ranks == sorted(ranks), f"玩法分段顺序与流派顺序不一致：{bound}"



# ── 排序入口：开发模式可拖，用户模式不给 ────────────────────


@pytest.fixture
def dev_mode(monkeypatch):
    """面板按系统配置（开发）模式构造：列表可排序。"""
    monkeypatch.setattr(factory_guard, "is_user_mode", lambda: False)


@pytest.fixture
def user_mode(monkeypatch):
    monkeypatch.setattr(factory_guard, "is_user_mode", lambda: True)


def _move(list_widget, source: int, target: int) -> None:
    item = list_widget.takeItem(source)
    list_widget.insertItem(target, item)
    list_widget.setCurrentItem(item)


def _resolver(tmp_path: Path) -> ConfigResolver:
    system, local = tmp_path / "system", tmp_path / "local"
    for root in (system, local):
        root.mkdir()
    for source_file in _SYSTEM_CONFIG.glob("*.yaml"):
        target = system / "yysls/game_config" / source_file.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source_file.read_bytes())
    return ConfigResolver(system_dir=system, local_dir=local, dev_mode=True,
                          config_dir=tmp_path)


def test_reorder_mapping_only_changes_key_order(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.game_settings.order_list.logger.warning",
        lambda message, *args, **kwargs: warnings.append(str(message)))

    source = {"乙": {"v": 2}, "甲": {"v": 1}, "坏数据": {"v": 3}}
    ordered = reorder_mapping(source, ["甲", "乙"])

    assert list(ordered) == ["甲", "乙", "坏数据"], "没有列表行的键保留在末尾"
    assert ordered["甲"] is source["甲"], "只改键序，值原样引用"
    assert warnings, "畸形键要留日志"


def test_system_mode_drag_rewrites_key_order(qtbot, dev_mode):
    """开发模式：拖动后键序按新行序写回，字段值不变。"""
    from lvjiang.apps.yysls.ui.game_settings.school_panel import SchoolPanel

    saved: list[dict] = []
    data = {"schools": {
        "甲": {"attr": "鸣金", "main": {"martial_art": "无名剑法"}},
        "乙": {"attr": "裂石"},
        "丙": {"attr": "破竹"},
    }}
    panel = SchoolPanel(data=data, on_changed=lambda: saved.append(deepcopy(data)))
    qtbot.addWidget(panel)
    panel._refresh_list(select="甲")

    assert panel._school_list.is_reorderable(), "开发模式必须能排序"
    _move(panel._school_list, 0, 2)
    panel._school_list.order_changed.emit()

    assert list(data["schools"]) == ["乙", "丙", "甲"]
    assert data["schools"]["甲"]["attr"] == "鸣金", "字段随条目走"
    assert saved, "重排应触发保存"
    assert panel._school_list.currentItem().text() == "甲", "选中项保持"


def test_system_mode_drag_for_martial_arts_and_playstyles(qtbot, dev_mode):
    from lvjiang.apps.yysls.ui.game_settings.martial_art_panel import (
        MartialArtPanel,
    )
    from lvjiang.apps.yysls.ui.game_settings.playstyle_panel import (
        PlaystylePanel,
    )

    arts_data = {"martial_arts": {
        "乙": {"weapon": "剑", "attr": "鸣金"},
        "甲": {"weapon": "枪", "attr": "鸣金"},
    }}
    arts_saved: list[dict] = []
    arts = MartialArtPanel(data=arts_data,
                           on_changed=lambda: arts_saved.append(deepcopy(arts_data)))
    qtbot.addWidget(arts)
    _move(arts._list, 0, 1)
    arts._list.order_changed.emit()
    assert list(arts_data["martial_arts"]) == ["甲", "乙"]
    assert arts._list.currentItem().text() == "乙"
    assert arts_saved

    arts._write("甲", "枪", "裂石")
    assert list(arts_data["martial_arts"]) == ["甲", "乙"], "编辑字段不能重排"

    styles_data = {"playstyles": {
        "甲": {"school": "", "arts": []},
        "乙": {"school": "", "arts": []},
    }}
    styles_saved: list[dict] = []
    styles = PlaystylePanel(
        data=styles_data,
        on_changed=lambda: styles_saved.append(deepcopy(styles_data)))
    qtbot.addWidget(styles)
    _move(styles._list, 0, 1)
    styles._list.order_changed.emit()
    assert list(styles_data["playstyles"]) == ["乙", "甲"]
    assert styles._list.currentItem().text() == "甲"
    assert styles_saved


def test_user_mode_has_no_reorder_entry(qtbot, user_mode):
    """用户模式：列表不可拖，也不写配置（键序写不进 local）。"""
    from lvjiang.apps.yysls.ui.game_settings.school_panel import SchoolPanel

    saved: list[dict] = []
    data = {"schools": {"甲": {"attr": "鸣金"}, "乙": {"attr": "裂石"}}}
    panel = SchoolPanel(data=data, on_changed=lambda: saved.append(deepcopy(data)))
    qtbot.addWidget(panel)
    panel._refresh_list(select="甲")

    assert not panel._school_list.is_reorderable()
    assert panel._school_list.toolTip()

    _move(panel._school_list, 0, 1)
    panel._on_order_changed()
    assert list(data["schools"]) == ["甲", "乙"]
    assert saved == []


def test_reorder_only_save_reaches_the_system_file(tmp_path):
    """纯排序也要落盘：dict 相等判定认为没变化，必须按文本比对键序。"""
    resolver = _resolver(tmp_path)
    data = load_game_config(resolver)
    names = list(data["schools"])
    _ = names

    data["schools"] = dict(reversed(list(data["schools"].items())))
    written = save_game_config(data, resolver)

    assert "yysls/game_config/schools.yaml" in written, "纯排序必须写盘"
    saved = yaml.safe_load(
        (resolver.system_dir / "yysls/game_config/schools.yaml")
        .read_text(encoding="utf-8"))
    assert list(saved["schools"]) == list(data["schools"])

