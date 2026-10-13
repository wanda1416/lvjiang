"""逐战购买保护礼盒优先、配额与实际成交记录，不操作真机。"""

from pathlib import Path

import pytest
from loguru import logger

from lvjiang.core.profile.repository import db_get_history, get_profile_db
from lvjiang.core.profile.service import profile_read
from lvjiang.workflows.builtins._registry import _FUNCTION_REGISTRY
from lvjiang.workflows.grammar import parse_file, parse_text
from lvjiang.workflows.grammar.ast_nodes import VarRef
from tests.workflows.test_automation_results import initialize, prepare
from tests.workflows.test_profile_registry import (
    _declare,
)
from tests.workflows.test_profile_registry import (
    isolated_profile_config as isolated_profile_config,
)


def test_bugan_claims_before_progress_and_rejects_regression(
    monkeypatch, isolated_profile_config,
):
    assert _declare(["bugan_of_week", "huaruizhi_of_week", "huaruizhi"]) == 0
    db = get_profile_db()
    db.upsert("tester", "quota", "bugan_of_week", 10000)
    db.upsert("tester", "quota", "huaruizhi_of_week", 1400)
    db.upsert("tester", "stock", "huaruizhi", 6000)
    engine, _ = prepare("purchase_bugan")
    engine.run_username = "tester"
    initialize(engine)

    helpers = Path(__file__).parents[2] / "config/system/workflows/subcall/game_profile.wf"
    engine._procs.update(parse_file(helpers).procs)
    engine._procs.update(parse_text(
        'def scan_and_cancel($action_name)\nreturn 0\nend\n').procs)
    raw = {"bugan_jindu": "13000/23000", "huaruizhi_jindu": "1432/4000"}
    events = []
    errors = []
    monkeypatch.setattr(logger, "error", lambda message: errors.append(str(message)))
    monkeypatch.setattr(engine, "_exec_click", lambda node: events.append(node.target.entity))
    monkeypatch.setattr(engine, "_exec_wait", lambda node: None)

    def scan(node):
        field = engine._resolve(node.fields[0])
        events.append("scan:" + field)
        engine.variables[node.target.name] = (
            "purchase" if field == "purchase" else {field: raw[field]}
        )

    monkeypatch.setattr(engine, "_exec_scan", scan)
    code = parse_text('call $progress = claim_and_sync_bugan_progress()\n').body
    engine._exec_body(code)
    assert events == ["scan:purchase", "purchase", "blank_area", "bugan_view",
                      "scan:bugan_jindu", "huaruizhi_view", "scan:huaruizhi_jindu"]
    assert engine.variables["progress"] == 13000
    assert profile_read("tester", "huaruizhi_of_week") == 1432
    assert profile_read("tester", "huaruizhi") == 6032
    assert db_get_history("tester", "stock", "huaruizhi")[0]["source"] == "日常"
    assert not errors

    engine._exec_body(code)
    assert profile_read("tester", "huaruizhi") == 6032
    raw.update(bugan_jindu="9000/23000", huaruizhi_jindu="1400/4000")
    engine._exec_body(code)
    assert len(errors) == 2
    assert any("本周不肝进度" in message for message in errors)
    assert any("本周花蕊织进度" in message for message in errors)
    assert profile_read("tester", "bugan_of_week") == 13000
    assert profile_read("tester", "huaruizhi_of_week") == 1432
    assert profile_read("tester", "huaruizhi") == 6032
    assert len(db_get_history("tester", "stock", "huaruizhi")) == 1


class ZhuzhanShop:
    def __init__(self, monkeypatch, *, balance=4000, boxes=0, nn=0,
                 has_nn=True, confirms=True, unread_stock=False):
        self.engine, _ = prepare("purchase_bugan")
        initialize(self.engine)
        self.engine._procs.update(parse_text(
            "def close_item_prompts()\nreturn 0\nend\n").procs)
        self.profile = {
            "huaruizhi": balance,
            "zhuzhanbox_of_halfseason": boxes,
            "nn_of_halfseason": nn,
        }
        self.stock = {"惊喜礼盒": 5 - boxes, "袅袅之音": 2 - nn}
        self.limits = {"惊喜礼盒": 5, "袅袅之音": 2}
        self.prices = {"惊喜礼盒": 200, "袅袅之音": 3000}
        self.has_nn = has_nn
        self.confirms = confirms
        self.unread_stock = unread_stock
        self.selected = ""
        self.quantity = 1
        self.purchases = []
        self.writes = []
        self.buttons = []
        self.game_balance = balance
        monkeypatch.setattr(self.engine, "_exec_find", self.find)
        monkeypatch.setattr(self.engine, "_exec_click", self.click)
        monkeypatch.setattr(self.engine, "_exec_scan", self.scan)
        monkeypatch.setattr(self.engine, "_exec_wait", lambda node: None)
        monkeypatch.setitem(_FUNCTION_REGISTRY, "profile_get", self.profile.get)
        monkeypatch.setitem(_FUNCTION_REGISTRY, "profile_inc", self.increment)

    def find(self, node):
        query = self.engine._resolve(node.by.target)
        self.engine.variables[node.var_name] = (
            "" if query == "袅袅之音" and not self.has_nn else query
        )

    def click(self, node):
        if isinstance(node.target, VarRef):
            self.selected = self.engine._resolve(node.target)
            self.quantity = 1
            return
        key = node.target.entity
        self.buttons.append(key)
        if key == "supply_max":
            self.quantity = min(self.stock[self.selected],
                                self.game_balance // self.prices[self.selected])
        elif key == "supply_buy":
            self.purchases.append((self.selected, self.quantity))
            if self.confirms:
                self.stock[self.selected] -= self.quantity
                self.game_balance -= self.quantity * self.prices[self.selected]

    def scan(self, node):
        fields = [self.engine._resolve(field) for field in node.fields]
        values = {
            "supply_item_name": self.selected,
            "supply_stock": ("" if self.unread_stock and not self.purchases
                             else f"{self.stock[self.selected]}/{self.limits[self.selected]}"),
        }
        self.engine.variables[node.target.name] = {field: values[field] for field in fields}

    def increment(self, key, quantity, source):
        self.writes.append((key, quantity))
        self.profile[key] += quantity
        unit_price = 200 if key == "zhuzhanbox_of_halfseason" else 3000
        self.profile["huaruizhi"] -= quantity * unit_price
        return self.profile[key]

    def run(self):
        self.engine._exec_body(parse_text("call buy_zhuzhan()\n").body)
        return self


@pytest.mark.parametrize(
    ("balance", "boxes", "nn", "expected"),
    [(4000, 0, 0, [("惊喜礼盒", 5), ("袅袅之音", 1)]),
     (10000, 2, 1, [("惊喜礼盒", 3), ("袅袅之音", 1)])],
)
def test_boxes_first_then_affordable_remaining_nn(monkeypatch, balance, boxes, nn, expected):
    shop = ZhuzhanShop(monkeypatch, balance=balance, boxes=boxes, nn=nn).run()
    assert shop.purchases == expected
    assert shop.buttons == ["cat_4", "supply_max", "supply_buy", "supply_max", "supply_buy"]
    assert shop.profile["zhuzhanbox_of_halfseason"] == 5
    assert shop.profile["nn_of_halfseason"] == nn + expected[-1][1]
    assert shop.profile["huaruizhi"] == balance - sum(
        quantity * shop.prices[name] for name, quantity in expected
    )


def test_missing_nn_on_homepage_does_not_buy_other_sound(monkeypatch):
    shop = ZhuzhanShop(monkeypatch, balance=7000, has_nn=False).run()
    assert shop.purchases == [("惊喜礼盒", 5)]
    assert shop.writes == [("zhuzhanbox_of_halfseason", 5)]
    records = shop.engine.output["result"]["items"]["zhuzhan_nn"]["records"]
    assert records[-1]["status"] == "not_found"


def test_unconfirmed_purchase_does_not_write_or_spend_stale_balance(monkeypatch):
    shop = ZhuzhanShop(monkeypatch, balance=7000, confirms=False).run()
    assert shop.purchases == [("惊喜礼盒", 5)]
    assert shop.writes == []
    assert shop.profile["huaruizhi"] == 7000
    assert shop.profile["zhuzhanbox_of_halfseason"] == 0


def test_stock_ocr_before_purchase_does_not_block_max_purchase(monkeypatch):
    shop = ZhuzhanShop(monkeypatch, balance=1000, unread_stock=True).run()
    assert shop.purchases == [("惊喜礼盒", 5)]
    assert shop.writes == [("zhuzhanbox_of_halfseason", 5)]
