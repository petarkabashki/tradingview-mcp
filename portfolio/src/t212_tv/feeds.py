"""Alternative price feeds: where to watch a position outside US trading hours.

A feed turns a ticker into a TradingView symbol through a list of patterns. The
first pattern that passes verification wins. Verification exists because tickers
collide across markets: `BINANCE:STXUSDT.P` is the Stacks crypto token at about
0.34, not Seagate at about 460. A symbol that cannot be shown to be the same
stock is left out, so a feed never quietly charts the wrong asset.
"""

import json
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

SCANNER_URL = "https://scanner.tradingview.com/symbol"
SEARCH_URL = "https://symbol-search.tradingview.com/symbol_search/v3/"
HEADERS = {"User-Agent": "Mozilla/5.0", "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}
SCAN_FIELDS = "close,description,currency,type,exchange"
DOLLAR_QUOTES = {"USD", "USDT", "USDC"}
# Feeds drift from the primary close, and more so over a weekend: BOATS was 6% out on one Saturday.
MAX_GAP = 0.15
INDEX_FUTURES = ["CME_MINI:ES1!", "CME_MINI:NQ1!"]
FEED_NAME = re.compile(r"[a-z0-9_-]+")

VERIFIED = "verified"
LISTED = "listed"
PINNED = "pinned"
MISSING = "missing"
USABLE = {VERIFIED, LISTED, PINNED}

DEFAULT_FEEDS = {
    "boats": {"patterns": ["BOATS:{ticker}"], "watchlist": "t212-overnight", "extras": INDEX_FUTURES},
    "perp": {
        "patterns": ["BINANCE:{ticker}USDT.P", "BINANCE:{ticker}XUSDT.P"],
        "watchlist": "t212-perps",
        "extras": INDEX_FUTURES,
    },
}


def fetch_json(url: str, params: dict) -> dict | None:
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def scan(symbol: str) -> dict:
    """Latest data for a symbol, or empty when the scanner does not cover it."""
    try:
        return fetch_json(SCANNER_URL, {"symbol": symbol, "fields": SCAN_FIELDS, "no_404": "true"}) or {}
    except urllib.error.HTTPError:
        return {}


def search_exchange(ticker: str, exchange: str) -> list[dict]:
    params = {"text": ticker, "hl": "0", "exchange": exchange, "lang": "en", "search_type": "", "domain": "production", "start": 0}
    return (fetch_json(SEARCH_URL, params) or {}).get("symbols", [])


def _clean(text: str | None) -> str:
    return (text or "").replace("<em>", "").replace("</em>", "")


def _first_word(text: str | None) -> str:
    words = _clean(text).lower().split()
    return words[0] if words else ""


def check_symbol(symbol: str, reference: dict, scanner: Callable = scan, searcher: Callable = search_exchange) -> dict:
    """Is this symbol the stock? `reference` is the scanner row of the primary listing.

    A price match is the proof. The scanner has no data for some venues, BOATS
    among them, and those can only count as listed: an exact listing with a
    matching company name, its price unchecked.
    """
    row = scanner(symbol)
    close, base = row.get("close"), reference.get("close")
    if close and base:
        gap = close / base - 1
        report = {"symbol": symbol, "close": close, "gap": gap}
        if row.get("currency") not in DOLLAR_QUOTES | {reference.get("currency")}:
            return {**report, "status": MISSING, "why": f"quoted in {row.get('currency')}"}
        if abs(gap) > MAX_GAP:
            return {**report, "status": MISSING, "why": f"price {close} is {gap:+.0%} from {base}: another asset"}
        return {**report, "status": VERIFIED}

    exchange, _, ticker = symbol.partition(":")
    for found in searcher(ticker, exchange):
        same_listing = _clean(found.get("symbol")) == ticker and found.get("exchange") == exchange
        if same_listing and _first_word(found.get("description")) == _first_word(reference.get("description")):
            return {"symbol": symbol, "close": None, "gap": None, "status": LISTED}
    return {"symbol": symbol, "close": None, "gap": None, "status": MISSING, "why": "no price and no matching listing"}


def resolve_ticker(feed: dict, ticker: str, primary: str, scanner: Callable = scan, searcher: Callable = search_exchange) -> dict:
    pinned = feed.get("symbols", {}).get(ticker)
    if pinned:
        return {"symbol": pinned, "close": None, "gap": None, "status": PINNED}

    reference = scanner(primary)
    if not reference.get("close"):
        return {"symbol": None, "status": MISSING, "why": f"no reference price for {primary}"}

    last = {"symbol": None, "status": MISSING, "why": "no patterns"}
    for pattern in feed.get("patterns", []):
        last = check_symbol(pattern.format(ticker=ticker), reference, scanner, searcher)
        if last["status"] in USABLE:
            return last
    return last


def resolve_feed(feed: dict, positions: list[dict], scanner: Callable = scan, searcher: Callable = search_exchange) -> dict[str, dict]:
    return {p["ticker"]: resolve_ticker(feed, p["ticker"], p["tv_symbol"], scanner, searcher) for p in positions}


def load_feeds(path: Path) -> dict[str, dict]:
    """Built-in feeds overlaid by feeds.toml; `enabled = false` switches one off."""
    feeds = {name: dict(feed) for name, feed in DEFAULT_FEEDS.items()}
    if path.exists():
        with path.open("rb") as handle:
            for name, custom in tomllib.load(handle).get("feeds", {}).items():
                feeds[name] = {**feeds.get(name, {}), **custom}
    for name, feed in feeds.items():
        if not feed.get("patterns") and not feed.get("symbols"):
            raise ValueError(f"Feed {name} needs patterns or symbols")
        if not feed.get("watchlist"):
            raise ValueError(f"Feed {name} needs a watchlist name")
    return feeds


def _toml_value(value) -> str:
    # JSON strings, lists and booleans are valid TOML for the plain values used here.
    return json.dumps(value)


def save_custom(path: Path, custom: dict[str, dict]) -> None:
    """Write the user's feed settings. Rewrites the file, so comments do not survive."""
    lines = ["# Price feeds for t212-tv. Manage with `t212-tv feed ...`, or edit by hand.\n"]
    for name, feed in sorted(custom.items()):
        lines.append(f"[feeds.{name}]")
        pinned = feed.get("symbols")
        lines += [f"{key} = {_toml_value(value)}" for key, value in feed.items() if key != "symbols"]
        if pinned:
            lines.append(f"\n[feeds.{name}.symbols]")
            # Quoted, because a ticker such as BRK.B would otherwise read as a nested key.
            lines += [f"{_toml_value(ticker)} = {_toml_value(symbol)}" for ticker, symbol in sorted(pinned.items())]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def load_custom(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle).get("feeds", {})


def add_feed(path: Path, name: str, patterns: list[str], watchlist: str | None, extras: list[str]) -> None:
    if name == "primary":
        raise ValueError("primary means the normal listings, so it cannot be a feed name")
    if not FEED_NAME.fullmatch(name):
        raise ValueError(f"Feed names use lowercase letters, digits, - and _, got: {name}")
    bad = [p for p in patterns if "{ticker}" not in p]
    if bad:
        raise ValueError(f"A pattern needs {{ticker}}, as in OKX:{{ticker}}USDT.P, got: {', '.join(bad)}")
    custom = load_custom(path)
    feed = custom.get(name, {})
    feed.update(enabled=True)
    if patterns:
        feed["patterns"] = patterns
    if watchlist:
        feed["watchlist"] = watchlist
    elif name not in DEFAULT_FEEDS and "watchlist" not in feed:
        feed["watchlist"] = f"t212-{name}"
    if extras:
        feed["extras"] = extras
    custom[name] = feed
    save_custom(path, custom)


def remove_feed(path: Path, name: str) -> None:
    """A built-in feed is switched off; a custom one is forgotten."""
    custom = load_custom(path)
    if name in DEFAULT_FEEDS:
        custom[name] = {**custom.get(name, {}), "enabled": False}
    elif custom.pop(name, None) is None:
        raise ValueError(f"No feed named {name}")
    save_custom(path, custom)


def pin_symbol(path: Path, name: str, ticker: str, symbol: str | None) -> None:
    """Fix one ticker to one symbol, or clear the pin when no symbol is given."""
    custom = load_custom(path)
    if name not in custom and name not in DEFAULT_FEEDS:
        raise ValueError(f"No feed named {name}")
    feed = custom.setdefault(name, {})
    pinned = feed.setdefault("symbols", {})
    if symbol:
        pinned[ticker] = symbol
    else:
        pinned.pop(ticker, None)
    save_custom(path, custom)


def plan_feeds(feeds: dict[str, dict], state: dict) -> list[dict]:
    """The enabled feeds, as the plan carries them: only symbols that passed."""
    known = state.get("feeds", {})
    planned = []
    for name, feed in feeds.items():
        if not feed.get("enabled", True):
            continue
        saved = known.get(name, {})
        planned.append(
            {
                "name": name,
                "watchlist": {"name": feed["watchlist"], "id": saved.get("watchlist_id")},
                "symbols": {
                    ticker: row["symbol"]
                    for ticker, row in saved.get("rows", {}).items()
                    if row.get("status") in USABLE
                },
                "extras": feed.get("extras", []),
            }
        )
    return planned
