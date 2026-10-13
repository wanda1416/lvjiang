"""逐战购买保护礼盒优先、配额与实际成交记录，不操作真机。"""

import pytest

from lvjiang.workflows.builtins._registry import _FUNCTION_REGISTRY
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.grammar.ast_nodes import VarRef
from tests.workflows.test_automation_results import initialize, prepare


class ZhuzhanShop:
    def __init__(self, monkeypatch, *, balance=4000, boxes=0, nn=0,
                 has_nn=True, confirms=True):
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
        self.selected = ""
        self.quantity = 1
        self.purchases = []
        self.writes = []
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
        if key == "add_one":
            self.quantity += 1
        elif key == "purchase":
            self.purchases.append((self.selected, self.quantity))
            if self.confirms:
                self.stock[self.selected] -= self.quantity

    def scan(self, node):
        fields = [self.engine._resolve(field) for field in node.fields]
        values = {
            "supply_item_name": self.selected,
            "supply_stock": f"{self.stock[self.selected]}/{self.limits[self.selected]}",
            "total_price": str(self.quantity * self.prices[self.selected]),
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
