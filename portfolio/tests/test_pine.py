"""The generated indicator must key entries so only the held instrument gets a line."""

import pytest

from t212_tv.pine import lookup_rows, render_pine

LINE = {"color": "#00C853", "width": 4, "style": 2, "tag": "T212 entry"}
POSITIONS = [
    {"ticker": "AAA", "tv_symbol": "NASDAQ:AAA", "entry_price": 10.5, "currency": "USD"},
    {"ticker": "BBB", "tv_symbol": "NYSE:BBB", "entry_price": 20.0, "currency": "EUR"},
]
FEEDS = [
    {"name": "boats", "symbols": {"AAA": "BOATS:AAA"}},
    {"name": "perp", "symbols": {"AAA": "BINANCE:AAAUSDT.P", "BBB": "BINANCE:BBBXUSDT.P"}},
]


def test_a_listing_matches_on_ticker_and_currency():
    listed, _ = lookup_rows(POSITIONS, [])
    assert listed == {"AAA|USD": 10.5, "BBB|EUR": 20.0}


def test_a_feed_on_the_same_ticker_needs_no_row():
    _, other = lookup_rows(POSITIONS, FEEDS)
    assert other == {"AAAUSDT.P": 10.5, "BBBXUSDT.P": 20.0}


def test_source_declares_the_name_and_style():
    source = render_pine(POSITIONS, FEEDS, LINE)
    assert 'indicator("T212 Entries", overlay=true' in source
    assert "line.style_dashed, width=4" in source
    assert "color entryColor = #00C853" in source
    assert 'entryLabel.set_text("T212 entry " + str.tostring(entry))' in source


def test_feed_lookup_is_left_out_when_there_is_nothing_to_look_up():
    source = render_pine(POSITIONS, [], LINE)
    assert "feed(" not in source
    assert 'float entry = listed(syminfo.ticker + "|" + syminfo.currency)' in source


def test_an_unsafe_symbol_never_reaches_the_source():
    bad = [{"ticker": "AAA", "tv_symbol": 'NASDAQ:A"A', "entry_price": 1.0, "currency": "USD"}]
    with pytest.raises(ValueError, match="Pine string"):
        render_pine(bad, [], LINE)
