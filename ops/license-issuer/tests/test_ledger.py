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


def _add(db, code_id="L1", bind="none", serial=None, levels=("lv1",),
         expires=None, code="LVJ1.A.B", note=""):
    return ledger.record_issue(
        code_id=code_id, bind=bind, serial=serial, levels=levels,
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
