"""Typed canonical records. Decimal is mandatory on all accounting paths."""
from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

D = Decimal
ONE = D("1")
ZERO = D("0")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def canonical(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(StrictModel):
    mode: Literal["paper", "replay", "research"] = "paper"
    source: Literal["mock", "kalshi"] = "mock"
    live_enabled: Literal[False] = False  # Recorder cannot enable real execution.
    initial_cash: Decimal = D("1000")
    max_order_contracts: Decimal = D("10")
    max_bout_loss: Decimal = D("25")
    max_event_loss: Decimal = D("50")
    max_total_loss: Decimal = D("100")
    max_daily_loss: Decimal = D("25")
    daily_drawdown: Decimal = D("20")
    max_spread: Decimal = D(".10")
    slippage: Decimal = D(".01")
    max_participation: Decimal = Field(default=D(".25"), gt=0, le=D(".25"))
    order_latency_ms: int = Field(default=250, ge=0)
    max_book_age_ms: int = Field(default=5000, gt=0)
    max_fight_age_ms: int = Field(default=15000, gt=0)
    blackout_seconds: int = Field(default=10, ge=0)
    kalshi_poll_seconds: int = Field(default=3, ge=1)
    database: str = "data/paper.sqlite3"

    @model_validator(mode="after")
    def positive_caps(self):
        for name in ("initial_cash", "max_order_contracts", "max_bout_loss", "max_event_loss",
                     "max_total_loss", "max_daily_loss", "daily_drawdown", "max_spread"):
            if not getattr(self, name).is_finite() or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not self.slippage.is_finite() or self.slippage < 0:
            raise ValueError("slippage must be finite and nonnegative")
        return self


class PriceBand(StrictModel):
    start: Decimal
    end: Decimal
    step: Decimal = Field(gt=0)


class Market(BaseModel):
    """Tolerate additive exchange fields; require metadata for trading."""
    model_config = ConfigDict(extra="ignore")
    ticker: str
    event_ticker: str
    title: str = ""
    yes_sub_title: str = ""
    status: str
    close_time: str | None = None
    open_time: str | None = None
    price_ranges: list[PriceBand] = Field(default_factory=list)
    fractional_trading_enabled: bool = False
    result: str | None = None
    settlement_value_dollars: Decimal | None = None
    rules_primary: str = ""
    rules_secondary: str = ""

    def on_grid(self, yes_price: Decimal) -> bool:
        return any(b.start <= yes_price <= b.end and (yes_price - b.start) % b.step == 0
                   for b in self.price_ranges) and ZERO < yes_price < ONE


class FightEvent(StrictModel):
    bout_id: str
    provider_event_id: str
    kind: Literal["start", "round_start", "stats", "round_end", "pause", "resume", "finish", "heartbeat"]
    source_ts_ms: int
    seq: int = Field(ge=1)
    round: int = Field(default=1, ge=1, le=5)
    clock_remaining: int = Field(default=300, ge=0, le=300)
    # Cumulative snapshots, never add these fields as deltas.
    strikes_a: int = Field(default=0, ge=0)
    strikes_b: int = Field(default=0, ge=0)
    knockdowns_a: int = Field(default=0, ge=0)
    knockdowns_b: int = Field(default=0, ge=0)
    control_a: int = Field(default=0, ge=0)
    control_b: int = Field(default=0, ge=0)
    winner: Literal["a", "b", "draw", "nc"] | None = None
    snapshot: bool = False

    @model_validator(mode="after")
    def finish_has_result(self):
        if self.kind == "finish" and self.winner is None:
            raise ValueError("finish requires an official feed result")
        return self


class OrderIntent(StrictModel):
    id: UUID
    ticker: str = Field(min_length=1, max_length=200)
    outcome: Literal["yes", "no"]
    action: Literal["buy", "sell"] = "buy"
    count: Decimal = Field(gt=0, le=1000, decimal_places=2)
    limit_price: Decimal = Field(gt=0, lt=1, decimal_places=6)
    time_in_force: Literal["immediate_or_cancel"] = "immediate_or_cancel"

    @field_validator("count", "limit_price", mode="before")
    @classmethod
    def no_float_money(cls, value):
        if isinstance(value, float):
            raise ValueError("money and quantity must be decimal strings")
        return value


class V2Order(StrictModel):
    """Official event-market V2 uses bid/ask, YES-leg price, and count strings."""
    ticker: str
    client_order_id: str
    side: Literal["bid", "ask"]
    count: str
    price: str
    time_in_force: Literal["immediate_or_cancel"] = "immediate_or_cancel"
    self_trade_prevention_type: Literal["taker_at_cross"] = "taker_at_cross"
    cancel_order_on_pause: Literal[True] = True
    post_only: Literal[False] = False
    reduce_only: bool = False
    order_group_id: str

    @classmethod
    def from_intent(cls, intent: OrderIntent, group_id: str):
        bid = (intent.outcome == "yes") == (intent.action == "buy")
        return cls(ticker=intent.ticker, client_order_id=str(intent.id),
                   side="bid" if bid else "ask", count=format(intent.count, ".2f"),
                   price=format(intent.limit_price if intent.outcome == "yes" else ONE - intent.limit_price, ".6f"),
                   reduce_only=intent.action == "sell", order_group_id=group_id)


class V2OrderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    order_id: str
    fill_count: Decimal = Field(ge=0)
    remaining_count: Decimal = Field(ge=0)
    ts_ms: int
    average_fill_price: Decimal | None = None
    average_fee_paid: Decimal | None = None
