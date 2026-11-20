"""袅袅之音清单：内容加载、校验与随包内容的合计。

随包清单是游戏事实（地区 / 条目 / 枚数），改错不会报错、只会让页面显示一个
错的进度，所以这里直接对仓库里的实际文件做合计断言：每个地区的枚数与攻略口径
一致，当前合计 224（常驻 214 + 周期 10）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvjiang.apps.yysls.core.linger.catalog import load_catalog
from lvjiang.apps.yysls.core.linger.models import (
    LingerCatalog,
    build_catalog,
    catalog_from_doc,
    region_from_doc,
)
from lvjiang.core.config.resolver import ConfigResolver

SYSTEM_DIR = Path(__file__).resolve().parents[2] / "config" / "system"


def _resolver(tmp_path: Path) -> ConfigResolver:
    return ConfigResolver(
        system_dir=SYSTEM_DIR,
        local_dir=tmp_path / "local",
        dev_mode=True,
        track_content=False,
        config_dir=tmp_path,
    )


def test_shipped_catalog_matches_the_published_totals(tmp_path: Path) -> None:
    catalog = load_catalog(_resolver(tmp_path))

    # 与官方发布内容对拍；周期来源不进入常驻分母。
    assert {region.label: region.total for region in catalog.regions} == {
        "清河": 15,
        "开封": 26,
        "开封皇宫": 18,
        "河西": 30,
        "不见山": 36,
        "青州": 14,
        "江南": 30,
        "未央城": 19,
        "通用": 36,
    }
    # 总枚 224 含周期型 10（无我三排 3 + 逐战 2 + 百珍 2 + 不肝 1 + 地华 2）；
    # 固定口径 214（周期型按当期进度动态，不计入常驻分母的推进统计）。
    assert catalog.total == 224
    assert catalog.item_count == 107
    assert catalog.fixed_total == 214
    assert catalog.fixed_item_count == 100
    periodic = [item for region in catalog.regions for item in region.items if item.periodic]
    assert len(periodic) == 7
    assert sum(item.count for item in periodic) == 10
    assert [region.order for region in catalog.regions] == sorted(
        region.order for region in catalog.regions)


def test_every_shipped_item_has_stable_id_and_source(tmp_path: Path) -> None:
    catalog = load_catalog(_resolver(tmp_path))

    keys: set[str] = set()
    for region in catalog.regions:
        assert region.id.isascii() and region.id.islower(), region.id
        for item in region.items:
            key = f"{region.id}/{item.id}"
            assert key not in keys, f"条目键重复：{key}"
            keys.add(key)
            # id 是纯随机 uuid8（全数字也算合法——islower 对纯数字为 False）
            assert item.id.isascii() and re.fullmatch(r"[0-9a-f]{8}", item.id), key
            assert item.count >= 1
            assert item.label and item.source


def test_broken_catalog_leaves_an_empty_catalog(tmp_path: Path) -> None:
    """整份清单是一个文件：写坏时返回空目录（页面提示「清单未安装」），不抛异常。"""
    local = tmp_path / "local" / "yysls"
    local.mkdir(parents=True)
    (local / "lingering.yaml").write_text("regions: oops\n", encoding="utf-8")

    catalog = load_catalog(_resolver(tmp_path))

    assert catalog.regions == ()
    assert catalog.total == 0


def test_catalog_from_doc_rejects_bad_shape() -> None:
    with pytest.raises(ValueError):
        catalog_from_doc({}, source_name="lingering.yaml")
    with pytest.raises(ValueError):
        catalog_from_doc({"regions": []}, source_name="lingering.yaml")


def test_region_from_doc_rejects_bad_fields() -> None:
    with pytest.raises(ValueError):
        region_from_doc({"id": "a", "label": "A", "items": []}, source_name="a.yaml")
    with pytest.raises(ValueError):
        region_from_doc(
            {"id": "a", "label": "A", "items": [{"id": "x", "label": "X", "count": 0}]},
            source_name="a.yaml")
    with pytest.raises(ValueError):
        region_from_doc(
            {"id": "a", "label": "A",
             "items": [{"id": "x", "label": "X"}, {"id": "x", "label": "Y"}]},
            source_name="a.yaml")


def test_build_catalog_sorts_by_order_and_rejects_duplicates() -> None:
    late = region_from_doc(
        {"id": "b", "label": "B", "order": 20, "items": [{"id": "x", "label": "X"}]},
        source_name="b.yaml")
    early = region_from_doc(
        {"id": "a", "label": "A", "order": 10, "items": [{"id": "x", "label": "X"}]},
        source_name="a.yaml")

    catalog = build_catalog([late, early])
    assert [region.id for region in catalog.regions] == ["a", "b"]
    assert catalog.total == 2

    with pytest.raises(ValueError):
        build_catalog([early, early])


def _single_region(items: list[dict]):
    return build_catalog([
        region_from_doc({"id": "r", "label": "R", "items": items},
                        source_name="r.yaml"),
    ])


def test_display_name_joins_category_with_label() -> None:
    """展示名 = 类型：条目；没有类型就只显示条目（以武会友这类）。"""
    catalog = _single_region([
        {"id": "a", "category": "探游", "label": "寻刀记", "count": 1},
        {"id": "b", "label": "以武会友", "count": 8, "kind": "roster"},
    ])

    a, b = (catalog.item(k)[1] for k in ("a", "b"))
    assert a.display_name == "探游：寻刀记"
    assert b.display_name == "以武会友"


def test_source_defaults_to_display_name() -> None:
    """来源缺省回落到展示名（类型：条目），账本里的来源与页面看到的一致。"""
    catalog = _single_region([
        {"id": "a", "category": "残章", "label": "夜行刀", "count": 1},
        {"id": "b", "label": "地图探索", "count": 2, "source": "自定义来源"},
    ])

    a, b = (catalog.item(k)[1] for k in ("a", "b"))
    assert a.source == "残章：夜行刀"
    assert b.source == "自定义来源"


def test_detail_keeps_inner_newlines() -> None:
    """详情是多行文本：首尾空白去掉，中间换行要留（双击后按原样显示）。"""
    body = "第一行" + chr(10) + "第二行"
    catalog = _single_region([
        {"id": "a", "label": "A", "detail": "  " + body + "  "},
    ])

    assert catalog.item("a")[1].detail == body


    assert catalog.item("a")[1].detail == "第一行\n第二行"


def test_item_kind_parses_roster_and_legacy_named() -> None:
    catalog = _single_region([
        {"id": "a", "label": "A", "kind": "roster", "count": 2},
        {"id": "b", "label": "B", "named": True},
        {"id": "c", "label": "C"},
    ])

    kinds = {item.id: item.kind for item in catalog.region("r").items}
    assert kinds == {"a": "roster", "b": "roster", "c": "single"}


def test_quota_target_makes_item_periodic() -> None:
    """绑配额 target → 展示形态周期；kind 不被反推（保持 single）。"""
    catalog = _single_region([
        {"id": "a", "label": "A", "target": "quota:some_weekly"},
    ])
    item = catalog.item("a")[1]

    assert item.kind == "single"
    assert item.periodic is True


def test_kind_and_target_are_independent() -> None:
    """记法与入账目标互不反推：kind 保持声明值，periodic 形态由 target 推导。"""
    catalog = _single_region([
        {"id": "a", "label": "A", "kind": "periodic"},   # kind 原样保留
        {"id": "b", "label": "B", "target": "quota:x"},  # 未声明 kind 也不被改写
    ])
    a, b = (catalog.item(k)[1] for k in ("a", "b"))

    assert a.kind == "periodic" and not a.periodic   # 没绑配额 → 形态非周期
    assert b.kind == "single" and b.periodic         # 绑配额 → 形态周期


def test_fixed_totals_exclude_periodic_items() -> None:
    """219 那个分母只算常驻来源，周期型自己按周期走，不能混进去。"""
    catalog = _single_region([
        {"id": "a", "label": "A", "count": 2},
        {"id": "b", "label": "B", "count": 3, "target": "quota:some_weekly"},
    ])

    assert catalog.total == 5
    assert catalog.fixed_total == 2
    assert catalog.fixed_item_count == 1
    assert catalog.region("r").fixed_total == 2


def test_catalog_item_lookup_uses_region_and_item_ids() -> None:
    catalog = LingerCatalog(())
    assert catalog.item("ywh") is None

    region = region_from_doc(
        {"id": "hexi", "label": "河西",
         "items": [{"id": "ywh", "label": "以武会友", "count": 14}]},
        source_name="hexi.yaml")
    catalog = build_catalog([region])
    found = catalog.item("ywh")
    assert found is not None
    assert found[0].id == "hexi"
    assert found[1].count == 14
    assert catalog.item("hexi/missing") is None
