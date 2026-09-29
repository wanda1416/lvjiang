"""签发台账

离线授权撤不掉一张已经发出去的码，唯一的把手是下次不续——前提是你查得到这张码是
谁的。所以台账的价值全在「查得到」和「记得住」，测试围绕这两点。
"""

from datetime import date, timedelta

import pytest
from license_issuer import ledger


@pytest.fixture
def db(tmp_path):
    return tmp_path / "ledger.db"


def _add(db, seq=None, code_id=None, bind="none", serial=None, levels=("lv1",),
         expires=None, code="LVJ1.A.B", note=""):
    """写一条记录。不传流水号时自动取下一个，编号按绑定方式渲染。"""
    if seq is None:
        seq = ledger.next_seq(db)
    if code_id is None:
        code_id = ledger.make_code_id(bind, seq)
    return ledger.record_issue(
        seq=seq, code_id=code_id, bind=bind, serial=serial, levels=levels,
        expires=expires, code=code, note=note, path=db)


class TestRecording:
    def test_creates_database_on_first_write(self, db):
        """首次签发时建库，不需要事先初始化。"""
        assert not db.exists()
        _add(db)
        assert db.exists()
        assert len(ledger.list_records(path=db)) == 1

    def test_roundtrip_fields(self, db):
        expires = date(2030, 1, 1)
        _add(db, code_id="L42", bind="serial", serial="ABCDE",
             levels=("lv1",), expires=expires, code="LVJ1.X.Y", note="给张三")
        rec = ledger.list_records(path=db)[0]
        assert rec.code_id == "L42"
        assert rec.is_bound
        assert rec.serial == "ABCDE"
        assert rec.levels == ("lv1",)
        assert rec.expires == expires
        assert rec.note == "给张三"
        assert rec.code == "LVJ1.X.Y"

    def test_stores_the_code_itself(self, db):
        """用户弄丢码时从台账重发，不必重签——重签会多一个编号，台账就乱了。"""
        _add(db, code="LVJ1.PAYLOAD.SIGNATURE")
        assert ledger.list_records(path=db)[0].code == "LVJ1.PAYLOAD.SIGNATURE"

    def test_permanent_code_has_no_expiry(self, db):
        _add(db, expires=None)
        rec = ledger.list_records(path=db)[0]
        assert rec.expires is None
        assert not rec.expired

    def test_newest_first(self, db):
        _add(db, code_id="L1")
        _add(db, code_id="L2")
        assert [r.code_id for r in ledger.list_records(path=db)] == ["L2", "L1"]


class TestQuerying:
    def test_find_by_code_id(self, db):
        """用户只会报编号，靠它查回这张码的去向。"""
        _add(db, code_id="L7", note="给李四")
        _add(db, code_id="L8")
        found = ledger.find_by_code_id("L7", path=db)
        assert len(found) == 1
        assert found[0].note == "给李四"

    def test_find_missing_returns_empty(self, db):
        _add(db, code_id="L7")
        assert ledger.find_by_code_id("nope", path=db) == []

    def test_expired_flag(self, db):
        yesterday = date.today() - timedelta(days=1)
        tomorrow = date.today() + timedelta(days=1)
        _add(db, code_id="old", expires=yesterday)
        _add(db, code_id="new", expires=tomorrow)
        by_id = {r.code_id: r for r in ledger.list_records(path=db)}
        assert by_id["old"].expired
        assert not by_id["new"].expired

    def test_expires_today_is_not_expired(self, db):
        """到期当天仍然有效，与客户端的判定保持一致。"""
        _add(db, expires=date.today())
        assert not ledger.list_records(path=db)[0].expired

    def test_target_label(self, db):
        _add(db, code_id="b", bind="serial", serial="XYZ")
        _add(db, code_id="f", bind="none", serial=None)
        by_id = {r.code_id: r for r in ledger.list_records(path=db)}
        assert by_id["b"].target == "XYZ"
        assert by_id["f"].target == "免绑定"


def test_note_can_be_added_afterwards(db):
    """「发给谁」多半是签完才想起来写。"""
    row_id = _add(db, note="")
    ledger.update_note(row_id, "给王五", path=db)
    assert ledger.list_records(path=db)[0].note == "给王五"


class TestSeqAllocation:
    """流水号才是编号的真身：两类码共用一条序列，数字全局唯一"""

    def test_empty_ledger_starts_at_one(self, db):
        assert ledger.next_seq(db) == 1
        assert ledger.next_code_id("serial", db) == "B0001"
        assert ledger.next_code_id("none", db) == "F0001"

    def test_increments_from_max(self, db):
        _add(db, seq=1)
        _add(db, seq=7)
        assert ledger.next_seq(db) == 8

    def test_uses_max_not_count(self, db):
        """删过行之后条数会和流水号对不上，重复编号会让台账查不准。"""
        _add(db, seq=42)
        assert ledger.next_seq(db) == 43

    def test_sequence_is_shared_between_bind_modes(self, db):
        """绑机与免绑定共用一条序列，不会出现 B0003 与 F0003 两张不同的码。"""
        _add(db, bind="serial")
        _add(db, bind="none")
        _add(db, bind="serial")
        ids = [r.code_id for r in ledger.list_records(path=db)]
        assert ids == ["B0003", "F0002", "B0001"]

    def test_prefix_tells_the_kind(self, db):
        assert ledger.make_code_id("serial", 1) == "B0001"
        assert ledger.make_code_id("none", 1) == "F0001"

    def test_rolls_past_four_digits(self, db):
        """9999 之后自然进位，位数只补不截。"""
        assert ledger.make_code_id("serial", 9999) == "B9999"
        assert ledger.make_code_id("serial", 10000) == "B10000"

    def test_exists_check(self, db):
        _add(db, bind="serial")
        assert ledger.code_id_exists("B0001", db)
        assert ledger.code_id_exists("  B0001  ", db)
        assert not ledger.code_id_exists("F0001", db)
