"""Unrealized profit and loss of open positions at current prices.

Cost is shares times the trade price from the exports, in the instrument's own
currency. Trading 212 also charges FX conversion fees and settles in the
account currency, and neither is in this cost, so the figures here are price
returns and differ from the app's own return by those fees and by FX moves.

Per asset:
  value          = shares x price
  pnl            = value - cost
  pnl_pct        = pnl / cost
  weight_pct     = value / portfolio value          (share of the portfolio)
  contrib_pct    = pnl / portfolio cost             (sums to the total return)

Weights and totals need one currency, so mixed portfolios are converted into a
base currency at the current FX rate.
"""

from collections.abc import Callable
from decimal import Decimal

HUNDRED = Decimal(100)


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _pct(part: Decimal, whole: Decimal) -> Decimal | None:
    return part / whole * HUNDRED if whole else None


def compute(
    positions: list,
    symbols: dict[str, str],
    price_of: Callable[[str], dict],
    fx_of: Callable[[str, str], Decimal | float | None],
    base: str | None = None,
    overrides: dict[str, Decimal] | None = None,
) -> dict:
    """P&L rows plus totals.

    `symbols` maps ticker to a symbol, or a list tried in order. `price_of(symbol)` returns
    a scanner row with `close` and `currency`, empty when there is no price.
    `fx_of(src, dst)` returns how many dst one src buys. `overrides` fixes a
    ticker's price, in the position's currency, without asking `price_of`.
    """
    overrides = overrides or {}
    currencies = sorted({p.currency for p in positions})
    base = base or (currencies[0] if len(currencies) == 1 else "USD")
    rates: dict[str, Decimal | None] = {base: Decimal(1)}

    def rate(currency: str) -> Decimal | None:
        if currency not in rates:
            found = fx_of(currency, base)
            rates[currency] = _dec(found) if found else None
        return rates[currency]

    rows, missing = [], []
    for p in positions:
        # One symbol, or several to try in order: a feed first, then the primary listing.
        candidates = symbols.get(p.ticker) or []
        candidates = [candidates] if isinstance(candidates, str) else list(candidates)
        row = {
            "ticker": p.ticker,
            "symbol": candidates[0] if candidates else None,
            "currency": p.currency,
            "shares": p.shares,
            "entry": p.entry_price,
            "cost": p.cost,
            "price": None,
            "source": None,
        }
        if p.ticker in overrides:
            row.update(price=_dec(overrides[p.ticker]), source="override")
        else:
            row["why"] = "no symbol"
            for candidate in candidates:
                quote = price_of(candidate) or {}
                close, quoted_in = quote.get("close"), quote.get("currency")
                if close and quoted_in and quoted_in != p.currency:
                    row["why"] = f"{candidate} is quoted in {quoted_in}, the position in {p.currency}"
                elif close:
                    row.update(price=_dec(close), source=candidate)
                    row.pop("why", None)
                    break
                else:
                    row["why"] = f"no price for {candidate}"

        fx = rate(p.currency)
        if row["price"] is None or fx is None:
            if fx is None:
                row["why"] = f"no FX rate {p.currency}{base}"
            missing.append(row)
            rows.append(row)
            continue
        value = p.shares * row["price"]
        pnl = value - p.cost
        row.update(
            value=value,
            pnl=pnl,
            pnl_pct=_pct(pnl, p.cost),
            fx=fx,
            base_cost=p.cost * fx,
            base_value=value * fx,
            base_pnl=pnl * fx,
        )
        rows.append(row)

    priced = [r for r in rows if r.get("value") is not None]
    total_cost = sum((r["base_cost"] for r in priced), Decimal(0))
    total_value = sum((r["base_value"] for r in priced), Decimal(0))
    for r in priced:
        r["weight_pct"] = _pct(r["base_value"], total_value)
        r["contrib_pct"] = _pct(r["base_pnl"], total_cost)

    total_pnl = total_value - total_cost
    return {
        "base": base,
        "rows": rows,
        "missing": [r["ticker"] for r in missing],
        "fx": {c: r for c, r in rates.items() if c != base},
        "total": {
            "cost": total_cost,
            "value": total_value,
            "pnl": total_pnl,
            "pnl_pct": _pct(total_pnl, total_cost),
            "priced": len(priced),
            "positions": len(rows),
        },
    }


def to_json(result: dict) -> dict:
    """Decimals as rounded floats: money to 2 places, prices and shares to 6, percents to 2."""
    money = {"cost", "value", "pnl", "base_cost", "base_value", "base_pnl"}
    pct = {"pnl_pct", "weight_pct", "contrib_pct"}

    def convert(key, value):
        if isinstance(value, dict):
            return {k: convert(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(key, v) for v in value]
        if isinstance(value, Decimal):
            places = 2 if key in money | pct else 6
            return float(round(value, places))
        return value

    return convert(None, result)


def _fmt(value, places: int = 2, sign: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+,.{places}f}" if sign else f"{value:,.{places}f}"


def render_table(result: dict) -> str:
    base = result["base"]
    header = f"{'Ticker':8} {'Shares':>12} {'Entry':>10} {'Price':>10} {'Value':>12} {'P&L':>11} {'P&L %':>8} {'Weight %':>9} {'Contrib %':>10}"
    lines = [header, "-" * len(header)]
    for r in sorted(result["rows"], key=lambda r: -(r.get("base_value") or 0)):
        if r.get("value") is None:
            lines.append(f"{r['ticker']:8} {_fmt(r['shares'], 4):>12} {_fmt(r['entry']):>10} {'-':>10}  not priced: {r.get('why')}")
            continue
        cur = "" if r["currency"] == base else f" {r['currency']}"
        lines.append(
            f"{r['ticker']:8} {_fmt(r['shares'], 4):>12} {_fmt(r['entry']):>10} {_fmt(r['price']):>10} "
            f"{_fmt(r['value']):>12} {_fmt(r['pnl'], sign=True):>11} {_fmt(r['pnl_pct'], sign=True):>8} "
            f"{_fmt(r['weight_pct']):>9} {_fmt(r['contrib_pct'], sign=True):>10}{cur}"
        )
    t = result["total"]
    lines.append("-" * len(header))
    lines.append(
        f"{'Total':8} {'':>12} {'':>10} {'':>10} {_fmt(t['value']):>12} {_fmt(t['pnl'], sign=True):>11} "
        f"{_fmt(t['pnl_pct'], sign=True):>8} {_fmt(100 if t['priced'] else None):>9} {_fmt(t['pnl_pct'], sign=True):>10}"
    )
    lines.append(f"In {base}; cost {_fmt(t['cost'])}. {t['priced']} of {t['positions']} positions priced.")
    for currency, fx in result["fx"].items():
        lines.append(f"FX {currency}{base} = {fx if fx is not None else 'unavailable'}")
    lines.append("Price returns only: Trading 212's FX fees and FX moves since buying are not included.")
    return "\n".join(lines)
