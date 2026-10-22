import sqlite3
from decimal import Decimal as D

import pytest

from backend.book import Book, BookStream
from backend.domain import Settings, Market, PriceBand
from backend.store import Store


def test_journal_immutable_persistent_and_raw(tmp_path):
    path = str(tmp_path / "events.db")
    store = Store(path)
    store.append("mock", "stats", {"a": 1}, raw=b'{ "a":1 }')
    assert store.verify()
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("DELETE FROM events")
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("UPDATE events SET payload='{}'")
    store.close(); store = Store(path)
    assert store.verify()
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_books_complement_and_unified_prices():
    b = Book(); b.snapshot([[".4", "10"]], [[".6", "20"]], 1, unified=True)
    assert b.quote() == (D(".4"), D(".6"))
    assert b.quote("no") == (D(".4"), D(".6"))
    assert b.delta("no", ".6", "-2", 2, unified=True)
    assert b.no[D(".4")] == 18
    assert not b.delta("no", ".6", "-200", 3, unified=True)


def test_subscription_sequence_gap_stales_all_markets():
    s = BookStream()
    for seq, ticker in [(1, "A"), (2, "B")]:
        assert s.apply({"type":"orderbook_snapshot", "sid":1, "seq":seq,
                        "msg":{"market_ticker":ticker,"yes_dollars_fp":[[".4","10"]],"no_dollars_fp":[[".6","20"]]}}, seq)
    assert not s.apply({"type":"orderbook_delta", "sid":1, "seq":4,"msg":{"market_ticker":"A"}}, 4)
    assert all(b.stale for b in s.books.values())


def test_defaults_and_market_grid():
    assert not Settings().live_enabled
    with pytest.raises(ValueError): Settings(live_enabled=True)
    m = Market(ticker="X",event_ticker="E",status="active",price_ranges=[PriceBand(start=".01",end=".99",step=".01")])
    assert m.on_grid(D(".42")) and not m.on_grid(D(".425"))
