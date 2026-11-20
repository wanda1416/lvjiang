"""清单覆盖：用户对 ``lingering.yaml`` 的增删改走 local 同名层。

这里只测覆盖层的行为（改值 / 新增 / 隐藏 / 恢复 / 批量绑定），经真实的
resolver + 合并层落盘——**local 文件名与结构必须和 system 一致**，只是优先级
更高；不存在另一套「覆盖条目」格式。
"""
from __future__ import annotations

import re
import yaml
from pathlib import Path

import pytest

from lvjiang.apps.yysls.core.linger import overrides as ov
from lvjiang.apps.yysls.core.linger.models import catalog_from_doc
from lvjiang.core.config.resolver import ConfigResolver

BASE_DOC = {
    "content_version": 1,
    "qinghe": {
        "label": "清河", "order": 10,
        "items": [
            {"id": "a", "label": "甲", "count": 2},
            {"id": "b", "label": "乙", "count": 1, "source": "来源乙"},
        ],
    },
    "hexi": {
        "label": "河西", "order": 20,
        "items": [{"id": "c", "label": "丙", "count": 3}],
    },
}


@pytest.fixture
def resolver(tmp_path: Path, monkeypatch) -> ConfigResolver:
    """独立 resolver：system 用临时文件（内容可自定义），local 在 tmp 下。"""
    system = tmp_path / "system"
    (system / "yysls").mkdir(parents=True)
    (system / "yysls" / "lingering.yaml").write_text(
        yaml.dump(BASE_DOC, allow_unicode=True), encoding="utf-8")
    result = ConfigResolver(
        system_dir=system,
        local_dir=tmp_path / "local",
        dev_mode=False,
        track_content=False,
        config_dir=tmp_path / "config",
    )
    monkeypatch.setattr(ov, "get_resolver", lambda: result)
    return result


def _local_doc(resolver: ConfigResolver) -> dict:
    path = resolver.local_dir / "yysls" / "lingering.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _effective(resolver: ConfigResolver):
    return catalog_from_doc(resolver.load_merged(ov.LINGER_FILE),
                            source_name=ov.LINGER_FILE)


def test_rename_only_writes_the_changed_item(resolver: ConfigResolver) -> None:
    """local 与 system 同名同结构，只存被改的条目（注册表增量）。"""
    ov.upsert_item("a", {"label": "改名"})

    local = _local_doc(resolver)
    items = local["qinghe"]["items"]
    assert items == {"__added__": [{"id": "a", "label": "改名", "count": 2}]}
    # 只动 qinghe；其它地区不进 local
    assert "hexi" not in local


def test_add_item_creates_new_entry_and_survives_merge(resolver: ConfigResolver) -> None:
    key = ov.add_item("hexi", {"label": "新增来源", "count": 2})

    assert re.fullmatch(r"[0-9a-f]{8}", key)
    catalog = _effective(resolver)
    hexi = catalog.region("hexi")
    assert [item.id for item in hexi.items] == ["c", key]


def test_remove_marks_hidden_not_deleted(resolver: ConfigResolver) -> None:
    """系统条目不允许真删：local 写 hidden，合并后这条不再出现。"""
    ov.remove_item("b")

    catalog = _effective(resolver)
    assert [item.id for item in catalog.region("qinghe").items] == ["a"]
    # hidden 只是标记：local 里保留整条并打标，而不是把条目删掉
    local_items = _local_doc(resolver)["qinghe"]["items"]["__added__"]
    assert any(entry.get("hidden") for entry in local_items)


def test_revert_restores_system_version(resolver: ConfigResolver) -> None:
    ov.upsert_item("a", {"label": "改名"})
    ov.revert_item("a")

    catalog = _effective(resolver)
    assert catalog.item("a")[1].label == "甲"
    assert _local_doc(resolver) == {}


def test_revert_user_item_removes_it(resolver: ConfigResolver) -> None:
    key = ov.add_item("qinghe", {"label": "临时来源", "count": 1})
    ov.revert_item(key)

    catalog = _effective(resolver)
    assert [item.id for item in catalog.region("qinghe").items] == ["a", "b"]
    assert _local_doc(resolver) == {}


def test_batch_target_binds_and_unbinds(resolver: ConfigResolver) -> None:
    changed = ov.set_batch_target(["a", "b"], "quota:niaoniao_of_week")
    assert changed == 2
    catalog = _effective(resolver)
    # 只改入账目标，不动记法（记法与目标是独立决定）
    assert [catalog.item(k)[1].target for k in ("a", "b")] == [
        "quota:niaoniao_of_week", "quota:niaoniao_of_week"]
    assert [catalog.item(k)[1].kind for k in ("a", "b")] == ["single", "single"]

    ov.set_batch_target(["a", "b"], "")
    catalog = _effective(resolver)
    assert [catalog.item(k)[1].kind for k in ("a", "b")] == [
        "single", "single"]
    assert _local_doc(resolver) == {}


def test_default_target_roundtrip(resolver: ConfigResolver) -> None:
    assert ov.read_default_target() == "stock:niaoniao"  # 未设置时回落系统默认

    ov.set_default_target("stock:other")
    assert ov.read_default_target() == "stock:other"
    assert _local_doc(resolver)["default_target"] == "stock:other"

    ov.set_default_target("")
    assert ov.read_default_target() == "stock:niaoniao"
    assert _local_doc(resolver) == {}


def test_system_update_still_reaches_user_layer(resolver: ConfigResolver) -> None:
    """local 只存改动：system 后来新增的条目必须照常出现在合并视图里。"""
    ov.upsert_item("a", {"label": "改名"})

    system_path = resolver.system_dir / "yysls" / "lingering.yaml"
    doc = yaml.safe_load(system_path.read_text(encoding="utf-8"))
    doc["qinghe"]["items"].append({"id": "new", "label": "新条目", "count": 1})
    system_path.write_text(yaml.dump(doc, allow_unicode=True), encoding="utf-8")

    catalog = _effective(resolver)
    ids = [item.id for item in catalog.region("qinghe").items]
    assert ids == ["a", "b", "new"]
    assert catalog.item("a")[1].label == "改名"


def test_user_created_category_appears_in_choices(resolver: ConfigResolver) -> None:
    """用户输入新分类保存条目后，它就是可选分类之一（无需任何注册步骤）。"""
    ov.add_item("qinghe", {"label": "自定义来源", "count": 1, "category": "我的分类"})

    catalog = _effective(resolver)
    assert "我的分类" in ov.known_categories(catalog)
    entry = [i for i in catalog.region("qinghe").items if i.label == "自定义来源"]
    assert entry[0].display_name == "我的分类：自定义来源"


def test_rename_category_touches_every_item(resolver: ConfigResolver) -> None:
    from lvjiang.apps.yysls.core.linger.models import LingerItem
    ov.add_item("qinghe", {"label": "甲一", "count": 1, "category": "旧分类"})
    ov.add_item("qinghe", {"label": "甲二", "count": 1, "category": "旧分类"})

    changed = ov.rename_category("旧分类", "新分类")

    assert changed == 2
    catalog = _effective(resolver)
    names = [i.display_name for i in catalog.region("qinghe").items
             if i.category == "新分类"]
    assert sorted(names) == ["新分类：甲一", "新分类：甲二"]
    assert "旧分类" not in ov.known_categories(catalog)


def test_remove_category_returns_items_to_plain(resolver: ConfigResolver) -> None:
    ov.add_item("qinghe", {"label": "乙一", "count": 1, "category": "临时分类"})

    changed = ov.remove_category("临时分类")

    assert changed == 1
    catalog = _effective(resolver)
    entry = [i for i in catalog.region("qinghe").items if i.label == "乙一"]
    assert entry[0].category == ""
    assert entry[0].display_name == "乙一"


def test_user_created_category_appears_in_choices(resolver: ConfigResolver) -> None:
    """用户输入新分类保存条目后，它就是可选分类之一（无需任何注册步骤）。"""
    ov.add_item("qinghe", {"label": "自定义来源", "count": 1, "category": "我的分类"})

    catalog = _effective(resolver)
    assert "我的分类" in ov.known_categories(catalog)
    entry = [i for i in catalog.region("qinghe").items if i.label == "自定义来源"]
    assert entry[0].display_name == "我的分类：自定义来源"


def test_rename_category_touches_every_item(resolver: ConfigResolver) -> None:
    from lvjiang.apps.yysls.core.linger.models import LingerItem
    ov.add_item("qinghe", {"label": "甲一", "count": 1, "category": "旧分类"})
    ov.add_item("qinghe", {"label": "甲二", "count": 1, "category": "旧分类"})

    changed = ov.rename_category("旧分类", "新分类")

    assert changed == 2
    catalog = _effective(resolver)
    names = [i.display_name for i in catalog.region("qinghe").items
             if i.category == "新分类"]
    assert sorted(names) == ["新分类：甲一", "新分类：甲二"]
    assert "旧分类" not in ov.known_categories(catalog)


def test_remove_category_returns_items_to_plain(resolver: ConfigResolver) -> None:
    ov.add_item("qinghe", {"label": "乙一", "count": 1, "category": "临时分类"})

    changed = ov.remove_category("临时分类")

    assert changed == 1
    catalog = _effective(resolver)
    entry = [i for i in catalog.region("qinghe").items if i.label == "乙一"]
    assert entry[0].category == ""
    assert entry[0].display_name == "乙一"


def test_move_item_writes_order_only(resolver: ConfigResolver) -> None:
    """拖动排序只产生顺序增量：local 里是 __order__，条目内容不重复存。"""
    ov.move_item("b", "a")

    local = _local_doc(resolver)
    assert local["qinghe"]["items"] == {"__order__": ["b", "a"]}
    catalog = _effective(resolver)
    assert [i.id for i in catalog.region("qinghe").items] == ["b", "a"]


def test_move_item_to_end(resolver: ConfigResolver) -> None:
    """拖到地区末尾 = 挪到最后一条后面。"""
    ov.move_item("a", None)

    catalog = _effective(resolver)
    assert [i.id for i in catalog.region("qinghe").items] == ["b", "a"]


def test_move_down_to_after_neighbor(resolver: ConfigResolver) -> None:
    """相邻下移：a 拖到 b 后面 == 顺序对调，不是原地不动。"""
    ov.move_item("a", "c")  # hexi 有 c，跨地区必须被拒绝
    ov.move_item("c", None)

    catalog = _effective(resolver)
    assert [i.id for i in catalog.region("qinghe").items] == ["a", "b"]
    assert [i.id for i in catalog.region("hexi").items] == ["c"]


def test_move_cross_region_is_rejected(resolver: ConfigResolver) -> None:
    assert ov.move_item("a", "c") is False
    assert _local_doc(resolver) == {}


def test_move_restored_system_new_items_stay_visible(resolver: ConfigResolver) -> None:
    """用户排过序后 system 新增的条目必须仍然出现（排在后面）。"""
    ov.move_item("b", "a")

    system_path = resolver.system_dir / "yysls" / "lingering.yaml"
    doc = yaml.safe_load(system_path.read_text(encoding="utf-8"))
    doc["qinghe"]["items"].append({"id": "new", "label": "新条目", "count": 1})
    system_path.write_text(yaml.dump(doc, allow_unicode=True), encoding="utf-8")

    catalog = _effective(resolver)
    ids = [i.id for i in catalog.region("qinghe").items]
    assert ids == ["b", "a", "new"]


def test_move_back_to_system_order_clears_local(resolver: ConfigResolver) -> None:
    """拖回系统顺序时顺序增量消失，local 回到干净状态。"""
    ov.move_item("b", "a")
    ov.move_item("a", "b")

    assert _local_doc(resolver) == {}
