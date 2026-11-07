"""Reduce trades to open positions."""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from t212_tv.parser import ExportError, Trade

# Fractional shares in exports are rounded, so a full sale can leave dust.
DUST = Decimal("0.000001")
PRICE_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class Position:
    ticker: str
    isin: str
    name: str
    currency: str
    shares: Decimal
    entry_price: Decimal
    cost: Decimal
    first_buy: datetime
    trade_count: int


def _key(trade: Trade) -> str:
    return trade.isin or trade.ticker


def build_positions(trades: list[Trade]) -> tuple[list[Position], list[str]]:
    """Return open positions by ticker, and the tickers fully closed.

    Entry is pooled average cost, as Trading 212 reports it: buys move the
    average, sells only reduce the share count. Expects trades oldest first.
    """
    groups: dict[str, list[Trade]] = {}
    for trade in trades:
        groups.setdefault(_key(trade), []).append(trade)

    positions, closed = [], []
    for group in groups.values():
        position = _reduce(group)
        if position:
            positions.append(position)
        else:
            closed.append(group[0].ticker)
    return sorted(positions, key=lambda p: p.ticker), sorted(closed)


def _reduce(trades: list[Trade]) -> Position | None:
    last = trades[-1]
    currencies = {t.currency for t in trades}
    if len(currencies) > 1:
        raise ExportError(f"{last.ticker}: trades in several currencies: {sorted(currencies)}")

    shares = cost = Decimal(0)
    first_buy = None
    for trade in trades:
        if trade.side == "buy":
            shares += trade.shares
            cost += trade.shares * trade.price
            first_buy = first_buy or trade.time
            continue
        if trade.shares > shares + DUST:
            # The buy is outside these exports, so the average would be wrong.
            raise ExportError(f"{trade.ticker}: sold {trade.shares} but only {shares} held; export is incomplete")
        cost -= cost * (trade.shares / shares)
        shares -= trade.shares
        if shares <= DUST:
            shares = cost = Decimal(0)
            first_buy = None

    if shares <= DUST:
        return None
    return Position(
        ticker=last.ticker,
        isin=last.isin,
        name=last.name,
        currency=last.currency,
        shares=shares,
        entry_price=(cost / shares).quantize(PRICE_PLACES, rounding=ROUND_HALF_UP),
        cost=cost,
        first_buy=first_buy,
        trade_count=len(trades),
    )
