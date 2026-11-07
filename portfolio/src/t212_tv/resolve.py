"""Map Trading 212 tickers to exact TradingView symbols.

A wrong match draws a real entry price on the wrong chart, so anything other
than one clear candidate stops the run instead of picking the likeliest.
"""

import tomllib
from collections.abc import Callable
from pathlib import Path

from t212_tv.positions import Position

PRIMARY_EXCHANGES = {
    "USD": {"NASDAQ", "NYSE", "AMEX"},
    "GBP": {"LSE"},
    "GBX": {"LSE"},
    "EUR": {"XETR", "EURONEXT", "BME", "MIL"},
}

# "dr" covers ADRs: ARM and other foreign companies list in the US this way.
SHARE_TYPES = {"stock", "dr"}

Search = Callable[[str], list[dict]]


class UnresolvedSymbols(ValueError):
    def __init__(self, failures: dict[str, str]):
        self.failures = failures
        lines = [f"  {ticker}: {why}" for ticker, why in failures.items()]
        super().__init__("Could not resolve:\n" + "\n".join(lines))


def load_overrides(path: Path) -> dict[str, str]:
    """Read `symbol-map.toml`: a [symbols] table keyed by ISIN or ticker."""
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8")).get("symbols", {})


def pick_symbol(position: Position, results: list[dict]) -> tuple[str | None, str]:
    """Return (symbol, "") for exactly one candidate, or (None, reason)."""
    exchanges = PRIMARY_EXCHANGES.get(position.currency)
    if exchanges is None:
        return None, f"no primary exchanges known for {position.currency}"

    candidates = {
        r["full_name"]
        for r in results
        if r.get("symbol") == position.ticker
        and r.get("type") in SHARE_TYPES
        and r.get("exchange") in exchanges
    }
    if len(candidates) == 1:
        return candidates.pop(), ""
    if candidates:
        return None, f"ambiguous: {sorted(candidates)}"
    return None, f"no {position.currency} stock listing among {len(results)} results"


def resolve_all(
    positions: list[Position],
    search: Search,
    overrides: dict[str, str],
    known: dict[str, str],
) -> dict[str, str]:
    """Return {isin: tv_symbol}. Overrides win, then earlier resolutions, then search."""
    resolved, failures = {}, {}
    for position in positions:
        key = position.isin or position.ticker
        override = overrides.get(position.isin) or overrides.get(position.ticker)
        if override or key in known:
            resolved[key] = override or known[key]
            continue
        symbol, why = pick_symbol(position, search(position.ticker))
        if symbol:
            resolved[key] = symbol
        else:
            failures[position.ticker] = why
    if failures:
        raise UnresolvedSymbols(failures)
    return resolved
