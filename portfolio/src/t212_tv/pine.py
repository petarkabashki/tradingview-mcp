"""Generate the Pine indicator that draws an entry line for any held symbol.

A TradingView drawing stays with one pane and one symbol, so it cannot follow a
symbol picked from the watchlist. A study stays with the pane and reruns on every
symbol, so this one holds all the entry prices and looks up the chart's symbol.

The output holds personal trade data. It belongs in the git-ignored plan and in
a private TradingView script, never in the repo.
"""

import re

INDICATOR_NAME = "T212 Entries"
LINE_STYLES = {0: "line.style_solid", 1: "line.style_dotted", 2: "line.style_dashed"}
_KEY = re.compile(r"^[A-Z0-9_.!|-]+$")


def _ticker(tv_symbol: str) -> str:
    return tv_symbol.split(":")[-1]


def _switch(signature: str, rows: dict[str, float]) -> list[str]:
    for key in rows:
        if not _KEY.fullmatch(key):
            raise ValueError(f"Cannot put {key!r} in a Pine string")
    cases = [f'        "{key}" => {price!r}' for key, price in rows.items()]
    return [f"{signature} =>", "    switch key", *cases, "        => na", ""]


def lookup_rows(positions: list[dict], feeds: list[dict]) -> tuple[dict[str, float], dict[str, float]]:
    """Listings match on ticker and currency, so a same-named foreign listing stays bare.

    A feed on the same ticker, like BOATS, is covered by the listing's row. The
    rest quote in USDT or similar, so they match on their own ticker.
    """
    listed = {f"{_ticker(p['tv_symbol'])}|{p['currency']}": p["entry_price"] for p in positions}
    prices = {p["ticker"]: p["entry_price"] for p in positions}
    plain = {_ticker(p["tv_symbol"]) for p in positions}
    other = {
        _ticker(symbol): prices[ticker]
        for feed in feeds
        for ticker, symbol in feed["symbols"].items()
        if _ticker(symbol) not in plain
    }
    return listed, other


def render_pine(positions: list[dict], feeds: list[dict], line: dict, name: str = INDICATOR_NAME) -> str:
    listed, other = lookup_rows(positions, feeds)
    body = [
        "//@version=6",
        f'indicator("{name}", overlay=true, max_lines_count=1, max_labels_count=1)',
        "",
        *_switch("listed(string key)", listed),
    ]
    if other:
        body += _switch("feed(string key)", other)
        body += [
            'float listedEntry = listed(syminfo.ticker + "|" + syminfo.currency)',
            "float entry = na(listedEntry) ? feed(syminfo.ticker) : listedEntry",
        ]
    else:
        body.append('float entry = listed(syminfo.ticker + "|" + syminfo.currency)')

    style = LINE_STYLES[line["style"]]
    body += [
        f"color entryColor = {line['color']}",
        "",
        'plot(entry, "Entry", entryColor, display=display.price_scale, editable=false)',
        "",
        f"var line entryLine = line.new(na, na, na, na, extend=extend.both, color=entryColor, style={style}, width={line['width']})",
        'var label entryLabel = label.new(na, na, "", style=label.style_label_lower_right, '
        "color=color.new(entryColor, 100), textcolor=entryColor, size=size.normal)",
        "",
        "if barstate.islast and not na(entry)",
        "    entryLine.set_xy1(bar_index - 1, entry)",
        "    entryLine.set_xy2(bar_index, entry)",
        "    entryLabel.set_xy(bar_index, entry)",
        f'    entryLabel.set_text("{line["tag"]} " + str.tostring(entry))',
        "",
    ]
    return "\n".join(body)
