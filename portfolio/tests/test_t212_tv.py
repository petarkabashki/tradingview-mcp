"""Tests use synthetic exports only: real ones hold personal trade data."""

import json
from argparse import Namespace
from datetime import datetime
from decimal import Decimal

import pytest

from t212_tv.cli import STATE_FILE, load_state, page_size, remember_result
from t212_tv.notes import BEGIN, END, render, write_notes
from t212_tv.parser import ExportError, read_exports
from t212_tv.plan import build_plan, plan_pages
from t212_tv.positions import build_positions
from t212_tv.resolve import UnresolvedSymbols, pick_symbol, resolve_all

HEADER = (
    "Action,Time (UTC),ISIN,Ticker,Name,ID,No. of shares,Price / share,"
    "Currency (Price / share),Exchange rate,Total,Currency (Total)\n"
)


def row(action, time, ticker, row_id, shares, price, isin=None, currency="USD"):
    isin = isin if isin is not None else f"US{ticker}"
    return f"{action},{time},{isin},{ticker},{ticker} Inc,{row_id},{shares},{price},{currency},1,0,GBP\n"


def export(tmp_path, *rows, name="a.csv"):
    path = tmp_path / name
    path.write_text(HEADER + "".join(rows), encoding="utf-8")
    return path


def positions_of(*paths):
    return build_positions(read_exports(list(paths)))


def test_deposits_are_ignored(tmp_path):
    path = export(
        tmp_path,
        "Deposit,2026-01-01 09:00:00,,,,dep1,,,,,1000,GBP\n",
        row("Market buy", "2026-01-01 10:00:00", "AAA", "1", "2", "100"),
    )
    open_positions, closed = positions_of(path)
    assert [p.ticker for p in open_positions] == ["AAA"]
    assert closed == []


def test_buys_are_averaged_by_shares(tmp_path):
    path = export(
        tmp_path,
        row("Market buy", "2026-01-01 10:00:00", "AAA", "1", "1", "100"),
        row("Limit buy", "2026-01-02 10:00:00", "AAA", "2", "3", "200"),
    )
    assert positions_of(path)[0][0].entry_price == Decimal("175")


def test_sell_reduces_shares_but_not_entry(tmp_path):
    path = export(
        tmp_path,
        row("Market buy", "2026-01-01 10:00:00", "AAA", "1", "4", "100"),
        row("Market sell", "2026-01-02 10:00:00", "AAA", "2", "3", "500"),
    )
    position = positions_of(path)[0][0]
    assert position.shares == Decimal("1")
    assert position.entry_price == Decimal("100")


def test_buying_after_a_full_close_starts_a_new_entry(tmp_path):
    path = export(
        tmp_path,
        row("Market buy", "2026-01-01 10:00:00", "AAA", "1", "2", "100"),
        row("Market sell", "2026-01-02 10:00:00", "AAA", "2", "2", "150"),
        row("Market buy", "2026-01-03 10:00:00", "AAA", "3", "1", "300"),
    )
    position = positions_of(path)[0][0]
    assert position.entry_price == Decimal("300")
    assert position.first_buy == datetime(2026, 1, 3, 10)


def test_closed_position_is_reported_not_kept(tmp_path):
    path = export(
        tmp_path,
        row("Market buy", "2026-01-01 10:00:00", "AAA", "1", "2", "100"),
        row("Market sell", "2026-01-02 10:00:00", "AAA", "2", "2", "150"),
    )
    assert positions_of(path) == ([], ["AAA"])


def test_overlapping_exports_do_not_double_count(tmp_path):
    shared = row("Market buy", "2026-01-01 10:00:00", "AAA", "1", "2", "100")
    first = export(tmp_path, shared, name="a.csv")
    second = export(tmp_path, shared, row("Market buy", "2026-01-02 10:00:00", "AAA", "2", "2", "200"), name="b.csv")
    position = positions_of(first, second)[0][0]
    assert position.shares == Decimal("4")
    assert position.entry_price == Decimal("150")


def test_sale_without_its_buy_stops_the_run(tmp_path):
    path = export(tmp_path, row("Market sell", "2026-01-02 10:00:00", "AAA", "1", "2", "150"))
    with pytest.raises(ExportError, match="incomplete"):
        positions_of(path)


def test_unknown_share_changing_action_stops_the_run(tmp_path):
    path = export(tmp_path, row("Stock split open", "2026-01-02 10:00:00", "AAA", "1", "2", "150"))
    with pytest.raises(ExportError, match="unhandled action"):
        positions_of(path)


def test_dividends_are_ignored(tmp_path):
    path = export(
        tmp_path,
        row("Market buy", "2026-01-01 10:00:00", "AAA", "1", "1", "100"),
        row("Dividend (Ordinary)", "2026-01-02 10:00:00", "AAA", "2", "1", "0.5"),
    )
    assert positions_of(path)[0][0].entry_price == Decimal("100")


def test_missing_column_stops_the_run(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Action,Ticker\nMarket buy,AAA\n", encoding="utf-8")
    with pytest.raises(ExportError, match="missing columns"):
        read_exports([path])


def result(symbol, exchange, kind="stock"):
    return {"symbol": symbol, "exchange": exchange, "type": kind, "full_name": f"{exchange}:{symbol}"}


def one_position(tmp_path, currency="USD"):
    path = export(tmp_path, row("Market buy", "2026-01-01 10:00:00", "AAA", "1", "1", "100", currency=currency))
    return positions_of(path)[0][0]


def test_symbol_needs_exact_ticker_on_a_primary_exchange(tmp_path):
    position = one_position(tmp_path)
    results = [result("AAA", "NASDAQ"), result("AAAX", "NYSE"), result("AAA", "OTC"), result("AAA", "NYSE", "fund")]
    assert pick_symbol(position, results) == ("NASDAQ:AAA", "")


def test_adr_counts_as_a_share_listing_on_its_primary_exchange(tmp_path):
    position = one_position(tmp_path)
    results = [result("AAA", "NASDAQ", "dr"), result("AAA", "BME"), result("AAA", "BYMA", "dr")]
    assert pick_symbol(position, results) == ("NASDAQ:AAA", "")


def test_ambiguous_symbol_is_not_guessed(tmp_path):
    position = one_position(tmp_path)
    symbol, why = pick_symbol(position, [result("AAA", "NASDAQ"), result("AAA", "NYSE")])
    assert symbol is None
    assert "ambiguous" in why


def test_unknown_currency_is_not_guessed(tmp_path):
    symbol, why = pick_symbol(one_position(tmp_path, "CHF"), [result("AAA", "SIX")])
    assert symbol is None
    assert "CHF" in why


def test_unresolved_symbols_stop_the_run(tmp_path):
    position = one_position(tmp_path)
    with pytest.raises(UnresolvedSymbols, match="AAA"):
        resolve_all([position], lambda _: [], {}, {})


def test_override_beats_search(tmp_path):
    position = one_position(tmp_path)

    def no_search(_):
        raise AssertionError("search should not run")

    assert resolve_all([position], no_search, {"AAA": "NYSE:AAA"}, {}) == {"USAAA": "NYSE:AAA"}


def test_plan_carries_known_ids_and_line_style(tmp_path):
    position = one_position(tmp_path)
    state = {"watchlist_id": 7, "layout_id": "abc"}
    plan = build_plan([position], {"USAAA": "NASDAQ:AAA"}, state)
    assert plan["version"] == 4
    assert plan["watchlist"]["id"] == 7
    assert plan["layout"] == {"name": "T212 Positions", "id": "abc"}
    assert plan["line"] == {"color": "#00C853", "width": 4, "style": 2, "tag": "T212 entry"}
    assert plan["positions"][0]["entry_price"] == 100.0
    assert plan["indicator"]["name"] == "T212 Entries"
    assert '"AAA|USD" => 100.0' in plan["indicator"]["source"]


def several_positions(tmp_path, tickers):
    rows = [row("Market buy", "2026-01-01 10:00:00", t, str(i), "1", "100") for i, t in enumerate(tickers)]
    return positions_of(export(tmp_path, *rows))[0]


@pytest.mark.parametrize("page_size, sizes", [(8, [8, 2]), (4, [4, 4, 2])])
def test_positions_fill_pages_on_one_layout(tmp_path, page_size, sizes):
    positions = several_positions(tmp_path, [f"T{i:02}" for i in range(10)])
    symbols = {p.isin: f"NASDAQ:{p.ticker}" for p in positions}
    plan = build_plan(positions, symbols, {}, page_size=page_size)
    assert [len(page) for page in plan_pages(plan)] == sizes
    assert plan["layout"]["name"] == "T212 Positions"


@pytest.mark.parametrize("page_size", [0, 17])
def test_impossible_page_size_is_refused(tmp_path, page_size):
    with pytest.raises(ValueError, match="page_size"):
        build_plan([one_position(tmp_path)], {"USAAA": "NASDAQ:AAA"}, {}, page_size=page_size)


def test_state_from_before_paging_keeps_the_first_layout(tmp_path):
    (tmp_path / STATE_FILE).write_text(
        json.dumps({"layouts": {"T212 Positions": "first", "T212 Positions 2": "second"}}), encoding="utf-8"
    )
    state = load_state(tmp_path)
    assert state["layout_id"] == "first"
    assert "layouts" not in state


def test_page_size_sticks_once_given():
    state = {}
    assert page_size(Namespace(page_size=4), state) == 4
    assert page_size(Namespace(page_size=None), state) == 4


def test_page_size_defaults_to_the_premium_limit():
    assert page_size(Namespace(page_size=None), {}) == 8


def test_a_partial_run_remembers_what_it_created():
    state = {}
    remember_result(
        state,
        {
            "watchlists": [{"feed": "primary", "id": 1}, {"feed": "boats", "id": 2}],
            "layout": {"id": "abc"},
            "panes": [{"symbol": "NASDAQ:AAA"}],
        },
    )
    assert state["watchlist_id"] == 1
    assert state["feeds"]["boats"]["watchlist_id"] == 2
    assert state["layout_id"] == "abc"
    assert "lines" not in state


def notes_block(tmp_path, feeds=None, feed_state=None):
    position = one_position(tmp_path)
    state = {"watchlist_id": 7, "layout_id": "abc", "feeds": feed_state or {}}
    plan = build_plan([position], {"USAAA": "NASDAQ:AAA"}, state, feeds=feeds)
    return render([position], plan, state, [], ["a.csv"], "2026-01-01 00:00 UTC", "passed")


def test_notes_record_assets_and_artefacts(tmp_path):
    block = notes_block(tmp_path)
    for expected in ("https://www.tradingview.com/chart/abc/", "t212-positions", "NASDAQ:AAA", "T212 Entries", "100 USD"):
        assert expected in block


def test_notes_record_pages_and_feeds(tmp_path):
    feeds = [{"name": "perp", "watchlist": {"name": "t212-perps", "id": 5}, "symbols": {}, "extras": []}]
    feed_state = {"perp": {"watchlist_id": 5, "rows": {"AAA": {"symbol": None, "status": "missing"}}}}
    block = notes_block(tmp_path, feeds=feeds, feed_state=feed_state)
    for expected in ("1 page(s) of 8", "Feed `perp`", "t212-perps", "missing AAA", "reference price only"):
        assert expected in block


def test_notes_keep_handwritten_text(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text(f"my thesis\n\n{BEGIN}\nold\n{END}\n\nfollow up\n", encoding="utf-8")
    write_notes(path, f"{BEGIN}\nnew\n{END}")
    text = path.read_text(encoding="utf-8")
    assert "my thesis" in text
    assert "follow up" in text
    assert "old" not in text
    assert "new" in text


def test_notes_are_created_when_missing(tmp_path):
    path = tmp_path / "notes.md"
    write_notes(path, f"{BEGIN}\nnew\n{END}")
    assert path.read_text(encoding="utf-8").startswith("# TradingView portfolio notes")
