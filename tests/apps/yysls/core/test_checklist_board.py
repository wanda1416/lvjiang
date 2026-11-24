"""清单板身份：袅袅之音的落点不变，另一块板不能串用它的开关和同步策略。"""
from __future__ import annotations

from lvjiang.apps.yysls.core.checklist.board import ChecklistBoard
from lvjiang.apps.yysls.core.linger import booking
from lvjiang.apps.yysls.core.linger.board import LINGER_BOARD
from lvjiang.apps.yysls.core.linger.store import LingerStore


def test_linger_board_keeps_published_identity() -> None:
    assert LINGER_BOARD.id == "linger"
    assert LINGER_BOARD.document_kind == "linger"
    assert LINGER_BOARD.document_suffix == ".linger.json"
    assert LINGER_BOARD.catalog_file == "yysls/lingering.yaml"
    assert LINGER_BOARD.default_target == "stock:niaoniao"
    assert LINGER_BOARD.switch_key == "linger_booking"
    assert LINGER_BOARD.triggers_sync("stock") is False
    assert LINGER_BOARD.triggers_sync("quota") is True


def test_store_path_follows_the_board(tmp_path) -> None:
    other = ChecklistBoard(
        id="demo",
        title="示例",
        catalog_file="yysls/demo.yaml",
        document_kind="demo",
        document_suffix=".demo.json",
        default_target="stock:tongbao",
        sync_models=frozenset({"stock", "quota"}),
    )
    store = LingerStore("alice", tmp_path, board=other)
    assert store.path == tmp_path / "alice.demo.json"
    assert store.board.document_kind == "demo"


def test_other_board_can_sync_stock_without_changing_linger(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(booking, "profile_action", lambda *a, **kw: calls.append(kw))
    other = ChecklistBoard(
        id="demo",
        title="示例",
        catalog_file="yysls/demo.yaml",
        document_kind="demo",
        document_suffix=".demo.json",
        default_target="stock:tongbao",
        sync_models=frozenset({"stock", "quota"}),
    )

    booking.write_delta("alice", "stock:tongbao", 1, "示例来源", board=other)
    booking.write_delta("alice", "stock:niaoniao", 1, "侠迹")

    assert calls[0]["trigger_sync"] is True
    assert calls[1]["trigger_sync"] is False
