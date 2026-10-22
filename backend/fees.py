from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR

from .domain import D, ONE, ZERO

MICRO = D(".000001")
CENT = D(".01")


@dataclass(frozen=True)
class FeeSchedule:
    fee_type: str = "quadratic"
    multiplier: D = D("1")
    verified_ms: int = 0
    valid_until_ms: int | None = None

    def __post_init__(self):
        if self.fee_type not in {"quadratic","quadratic_with_maker_fees","quadratic_with_combo_maker_fees"}:
            raise ValueError("unknown/flat fee needs explicit supported schedule; trading blocked")
        if not self.multiplier.is_finite() or self.multiplier < 0:
            raise ValueError("invalid fee multiplier")

    def fee(self, p, q, maker=False):
        if not ZERO < p < ONE or q <= ZERO:
            raise ValueError("invalid fee input")
        rate = D(".07")
        if maker:
            rate = {"quadratic":ZERO,"quadratic_with_maker_fees":D(".0175"),"quadratic_with_combo_maker_fees":D(".035")}[self.fee_type]
        return self.multiplier*rate*q*p*(ONE-p)


@dataclass
class FeeAccumulator:
    precision: D = CENT
    carried: D = ZERO

    def apply(self, revenue, model_fee):
        trade = model_fee.quantize(MICRO,rounding=ROUND_CEILING)
        raw = revenue-trade
        aligned = raw.quantize(self.precision,rounding=ROUND_FLOOR)
        rounding = raw-aligned
        self.carried += rounding
        rebate = min((self.carried//self.precision)*self.precision,
                     ((trade+rounding)//self.precision)*self.precision)
        self.carried -= rebate
        change = aligned+rebate
        return change,revenue-change


def expected_value(probability, price, count, fees: FeeSchedule, slippage=ZERO, exit_price=None):
    """Settlement-held BUY EV at executable ask, or SELL value vs holding.

    Fee upper bound and slippage are charged exactly once. No midpoint fills.
    """
    fee = fees.fee(price,count).quantize(CENT,rounding=ROUND_CEILING)
    net = (D(str(probability))-price-slippage)*count-fee
    return {"net_ev":str(net),"per_contract":str(net/count),"fee_upper_bound":str(fee),
            "breakeven_probability":str(price+slippage+fee/count)}
