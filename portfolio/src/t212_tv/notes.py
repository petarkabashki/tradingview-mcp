"""Keep the markdown record of managed assets and TradingView artefacts."""

from pathlib import Path

from t212_tv.plan import plan_pages
from t212_tv.positions import Position

BEGIN = "<!-- t212-tv:begin (generated, edits here are replaced on sync) -->"
END = "<!-- t212-tv:end -->"
TITLE = "# TradingView portfolio notes\n\nAnything outside the generated block is yours and is kept.\n"


def _cell(value) -> str:
    return str(value).replace("|", "\\|")


def _table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _layout_row(plan: dict) -> list:
    layout = plan["layout"]
    url = f"https://www.tradingview.com/chart/{layout['id']}/, " if layout["id"] else ""
    details = f"{url}{len(plan_pages(plan))} page(s) of {plan['page_size']}"
    return ["Layout", layout["name"], layout["id"] or "not created", details]


def _feed_rows(plan: dict, state: dict) -> list[list]:
    rows = []
    for feed in plan["feeds"]:
        missing = sorted(set(p["ticker"] for p in plan["positions"]) - set(feed["symbols"]))
        details = f"{len(feed['symbols'])} symbols" + (f", missing {', '.join(missing)}" if missing else "")
        feed_id = state.get("feeds", {}).get(feed["name"], {}).get("watchlist_id") or "not created"
        rows.append([f"Feed `{feed['name']}`", feed["watchlist"]["name"], feed_id, details])
    return rows


def _feed_table(plan: dict, state: dict) -> str:
    known = state.get("feeds", {})
    names = [feed["name"] for feed in plan["feeds"]]

    def cell(name: str, ticker: str) -> str:
        row = known.get(name, {}).get("rows", {}).get(ticker)
        if not row:
            return "unchecked"
        if not row.get("symbol") or row.get("status") == "missing":
            return "none"
        return f"{row['symbol']} ({row['status']})"

    return _table(["Ticker", *names], [[p["ticker"], *[cell(n, p["ticker"]) for n in names]] for p in plan["positions"]])


def render(
    positions: list[Position],
    plan: dict,
    state: dict,
    closed: list[str],
    sources: list[str],
    synced_at: str,
    verified: str,
) -> str:
    line = plan["line"]
    symbols = {p["ticker"]: p["tv_symbol"] for p in plan["positions"]}
    places = {
        p["ticker"]: (page, pane)
        for page, entries in enumerate(plan_pages(plan), start=1)
        for pane, p in enumerate(entries, start=1)
    }

    artefacts = _table(
        ["Artefact", "Name", "ID", "Details"],
        [
            ["Watchlist", plan["watchlist"]["name"], state.get("watchlist_id") or "not created", f"{len(positions)} symbols"],
            _layout_row(plan),
            *_feed_rows(plan, state),
            ["Entry lines", plan["indicator"]["name"], "private Pine script", f"{line['color']}, width {line['width']}, dashed, any held symbol"],
        ],
    )
    assets = _table(
        ["Page", "Pane", "Ticker", "Name", "ISIN", "TradingView", "Shares", "Entry", "Cost", "First buy (UTC)"],
        [
            [
                *places[p.ticker],
                p.ticker,
                p.name,
                p.isin,
                symbols[p.ticker],
                f"{p.shares.normalize():f}",
                f"{p.entry_price.normalize():f} {p.currency}",
                f"{p.cost:.2f} {p.currency}",
                p.first_buy.strftime("%Y-%m-%d %H:%M"),
            ]
            for p in positions
        ],
    )
    body = [
        BEGIN,
        "## Sync status",
        f"- Last sync: {synced_at}\n- Verification: {verified}\n- Source exports: {', '.join(sources)}",
        "## TradingView artefacts",
        artefacts,
        "## Assets",
        assets,
        "Entry is pooled average cost across the exports above, in the quote currency, before FX fees. "
        "A position bought before the earliest export would differ from the Trading 212 average.",
    ]
    if plan["feeds"]:
        body += [
            "## Out-of-hours feeds",
            _feed_table(plan, state),
            "verified: price matched the primary close. listed: exact listing with the same company name, "
            "price unchecked. pinned: set by hand. Perpetuals quote USDT or USDC, are not tradable on "
            "Trading 212, and are a reference price only.",
        ]
    if closed:
        body += ["## Closed in these exports", ", ".join(closed)]
    return "\n\n".join(body + [END])


def write_notes(path: Path, block: str) -> None:
    """Replace only the generated block, so hand-written notes survive."""
    if not path.exists():
        path.write_text(f"{TITLE}\n{block}\n", encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    start, end = text.find(BEGIN), text.find(END)
    if start == -1 or end == -1:
        path.write_text(f"{text.rstrip()}\n\n{block}\n", encoding="utf-8")
        return
    path.write_text(text[:start] + block + text[end + len(END) :], encoding="utf-8")
