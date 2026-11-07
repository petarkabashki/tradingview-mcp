"""Keep the markdown record of managed assets and TradingView artefacts."""

from pathlib import Path

from t212_tv.plan import plan_positions
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


def _layout_row(layout: dict) -> list:
    url = f"https://www.tradingview.com/chart/{layout['id']}/, " if layout["id"] else ""
    return ["Layout", layout["name"], layout["id"] or "not created", f"{url}grid `{layout['grid']}`"]


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
    lines_by_symbol = state.get("lines", {})
    symbols = {p["ticker"]: p["tv_symbol"] for p in plan_positions(plan)}
    places = {
        p["ticker"]: (layout["name"], index)
        for layout in plan["layouts"]
        for index, p in enumerate(layout["positions"])
    }

    artefacts = _table(
        ["Artefact", "Name", "ID", "Details"],
        [
            ["Watchlist", plan["watchlist"]["name"], state.get("watchlist_id") or "not created", f"{len(positions)} symbols"],
            *[_layout_row(layout) for layout in plan["layouts"]],
            ["Entry lines", f"tag `{line['tag']}`", "see Assets", f"{line['color']}, width {line['width']}, dashed"],
        ],
    )
    assets = _table(
        ["Layout", "Pane", "Ticker", "Name", "ISIN", "TradingView", "Shares", "Entry", "Cost", "First buy (UTC)", "Line"],
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
                lines_by_symbol.get(symbols[p.ticker], "none"),
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
