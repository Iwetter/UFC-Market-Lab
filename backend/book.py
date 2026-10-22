from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .domain import D, ONE, ZERO


@dataclass
class Book:
    yes: dict[Decimal, Decimal] = field(default_factory=dict)
    no: dict[Decimal, Decimal] = field(default_factory=dict)
    stale: bool = True
    updated_ms: int = 0
    clock: int | None = None

    def snapshot(self, yes, no, ts, clock=None, unified=False):
        def parse(levels, invert=False):
            result = {}
            for p, q in levels:
                price, quantity = D(p), D(q)
                if invert:
                    price = ONE - price
                if not ZERO <= price <= ONE or quantity < 0:
                    raise ValueError("invalid book level")
                if quantity:
                    result[price] = quantity
            return result
        self.yes = parse(yes)
        self.no = parse(no, unified)
        self.updated_ms, self.clock, self.stale = ts, clock, False
        self._check_cross()

    def _check_cross(self):
        if self.yes and self.no and max(self.yes) + max(self.no) >= ONE:
            self.stale = True

    def delta(self, side, price, quantity_delta, ts, clock=None, unified=False):
        if self.stale:
            return False
        if clock is not None and self.clock is not None and clock <= self.clock:
            return True  # REST/WS snapshot already includes this change.
        ladder = self.yes if side == "yes" else self.no
        price, quantity_delta = D(price), D(quantity_delta)
        if side == "no" and unified:
            price = ONE - price
        q = ladder.get(price, ZERO) + quantity_delta
        if q < 0 or not ZERO <= price <= ONE:
            self.stale = True
            return False
        if q:
            ladder[price] = q
        else:
            ladder.pop(price, None)
        self.updated_ms, self.clock = ts, clock
        self._check_cross()
        return not self.stale

    def bids(self, outcome):
        return sorted((self.yes if outcome == "yes" else self.no).items(), reverse=True)

    def asks(self, outcome):
        return sorted((ONE - p, q) for p, q in (self.no if outcome == "yes" else self.yes).items())

    def quote(self, outcome="yes"):
        bids, asks = self.bids(outcome), self.asks(outcome)
        return (bids[0][0] if bids else None, asks[0][0] if asks else None)

    def view(self):
        bid, ask = self.quote()
        return {"yes_bid": str(bid) if bid is not None else None,
                "yes_ask": str(ask) if ask is not None else None,
                "no_bid": str(ONE-ask) if ask is not None else None,
                "no_ask": str(ONE-bid) if bid is not None else None,
                "yes": [[str(p), str(q)] for p,q in self.bids("yes")],
                "no": [[str(p), str(q)] for p,q in self.bids("no")],
                "stale": self.stale, "updated_ms": self.updated_ms, "clock": self.clock}


class BookStream:
    """Sequence is subscription-wide, not per market. A gap stales all its books."""
    def __init__(self):
        self.books: dict[str, Book] = {}
        self.sequences: dict[int, int] = {}
        self.members: dict[int, set[str]] = {}
        self.gaps = 0

    def invalidate(self, sid=None):
        for ticker in self.members.get(sid, set()) if sid is not None else self.books:
            self.books[ticker].stale = True

    def apply(self, message, ts, unified=True):
        kind, sid, seq = message["type"], message.get("sid"), message.get("seq")
        msg = message.get("msg", {})
        if kind == "error" and msg.get("code") == 25:
            self.gaps += 1
            self.invalidate(sid)
            return False
        if sid is not None and seq is not None:
            old = self.sequences.get(sid)
            if old is not None and seq != old + 1:
                self.gaps += 1
                self.invalidate(sid)
                # Do not silently apply a delta after a duplicate or a gap.
                self.sequences[sid] = seq
                if kind != "orderbook_snapshot":
                    return False
            self.sequences[sid] = seq
        if kind not in {"orderbook_snapshot", "orderbook_delta"}:
            return True
        ticker = msg["market_ticker"]
        self.members.setdefault(sid, set()).add(ticker)
        book = self.books.setdefault(ticker, Book())
        if kind == "orderbook_snapshot":
            book.snapshot(msg.get("yes_dollars_fp", []), msg.get("no_dollars_fp", []), ts, msg.get("clock"), unified)
            return not book.stale
        return book.delta(msg["side"], msg["price_dollars"], msg["delta_fp"], ts, msg.get("clock"), unified)
