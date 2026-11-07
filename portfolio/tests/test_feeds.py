"""Feed tests use fake lookups: nothing here reaches the network."""

import pytest

from t212_tv.feeds import (
    LISTED,
    MISSING,
    PINNED,
    VERIFIED,
    add_feed,
    check_symbol,
    load_custom,
    load_feeds,
    pin_symbol,
    plan_feeds,
    remove_feed,
    resolve_feed,
    resolve_ticker,
)

STOCK = {"close": 100.0, "currency": "USD", "description": "Acme Holdings"}
PERP = {"patterns": ["X:{ticker}USDT.P", "X:{ticker}XUSDT.P"], "watchlist": "perps"}


def scanner(rows):
    return lambda symbol: rows.get(symbol, {})


def no_search(_ticker, _exchange):
    return []


def test_a_close_price_verifies():
    row = check_symbol("X:AUSDT.P", STOCK, scanner({"X:AUSDT.P": {"close": 101.0, "currency": "USDT"}}), no_search)
    assert row["status"] == VERIFIED
    assert row["gap"] == pytest.approx(0.01)


def test_a_namesake_at_another_price_is_refused():
    row = check_symbol("X:AUSDT.P", STOCK, scanner({"X:AUSDT.P": {"close": 0.34, "currency": "USDT"}}), no_search)
    assert row["status"] == MISSING
    assert "another asset" in row["why"]


def test_a_foreign_quote_is_refused():
    row = check_symbol("X:A", STOCK, scanner({"X:A": {"close": 100.0, "currency": "EUR"}}), no_search)
    assert row["status"] == MISSING


def test_a_venue_without_prices_can_still_be_listed():
    found = [{"symbol": "<em>A</em>", "exchange": "BOATS", "description": "Acme Holdings Inc"}]
    row = check_symbol("BOATS:A", STOCK, scanner({}), lambda _t, _e: found)
    assert row["status"] == LISTED


def test_a_listing_for_another_company_is_refused():
    found = [{"symbol": "A", "exchange": "BOATS", "description": "Other Corp"}]
    assert check_symbol("BOATS:A", STOCK, scanner({}), lambda _t, _e: found)["status"] == MISSING


def test_the_second_pattern_catches_a_collision():
    rows = {
        "NYSE:A": STOCK,
        "X:AUSDT.P": {"close": 0.34, "currency": "USDT"},
        "X:AXUSDT.P": {"close": 99.0, "currency": "USDT"},
    }
    row = resolve_ticker(PERP, "A", "NYSE:A", scanner(rows), no_search)
    assert (row["symbol"], row["status"]) == ("X:AXUSDT.P", VERIFIED)


def test_a_pin_is_trusted():
    feed = {**PERP, "symbols": {"A": "Y:A"}}
    assert resolve_ticker(feed, "A", "NYSE:A", scanner({}), no_search)["status"] == PINNED


def test_no_reference_price_means_no_symbol():
    assert resolve_ticker(PERP, "A", "NYSE:A", scanner({}), no_search)["status"] == MISSING


def test_resolve_feed_covers_every_position():
    rows = {"NYSE:A": STOCK, "X:AUSDT.P": {"close": 100.0, "currency": "USDT"}}
    entries = [{"ticker": "A", "tv_symbol": "NYSE:A"}, {"ticker": "B", "tv_symbol": "NYSE:B"}]
    result = resolve_feed(PERP, entries, scanner(rows), no_search)
    assert {t: r["status"] for t, r in result.items()} == {"A": VERIFIED, "B": MISSING}


def test_built_in_feeds_exist_without_a_file(tmp_path):
    assert set(load_feeds(tmp_path / "feeds.toml")) == {"boats", "perp"}


def test_add_and_remove_a_custom_feed(tmp_path):
    path = tmp_path / "feeds.toml"
    add_feed(path, "okx", ["OKX:{ticker}USDT.P"], None, ["CME_MINI:ES1!"])
    feed = load_feeds(path)["okx"]
    assert (feed["watchlist"], feed["extras"]) == ("t212-okx", ["CME_MINI:ES1!"])
    remove_feed(path, "okx")
    assert "okx" not in load_feeds(path)


def test_a_built_in_feed_is_switched_off_not_forgotten(tmp_path):
    path = tmp_path / "feeds.toml"
    remove_feed(path, "boats")
    assert load_feeds(path)["boats"]["enabled"] is False
    add_feed(path, "boats", [], None, [])
    assert load_feeds(path)["boats"]["enabled"] is True


def test_changing_a_built_in_feed_keeps_its_other_settings(tmp_path):
    path = tmp_path / "feeds.toml"
    add_feed(path, "perp", ["OKX:{ticker}USDT.P"], None, [])
    feed = load_feeds(path)["perp"]
    assert (feed["patterns"], feed["watchlist"]) == (["OKX:{ticker}USDT.P"], "t212-perps")


def test_pins_survive_a_round_trip_and_can_be_cleared(tmp_path):
    path = tmp_path / "feeds.toml"
    pin_symbol(path, "perp", "BRK.B", "BINANCE:BRKBUSDT.P")
    assert load_custom(path)["perp"]["symbols"] == {"BRK.B": "BINANCE:BRKBUSDT.P"}
    pin_symbol(path, "perp", "BRK.B", None)
    assert not load_custom(path)["perp"].get("symbols")


@pytest.mark.parametrize(
    "name, patterns, message",
    [("primary", [], "primary"), ("Bad Name", [], "lowercase"), ("okx", ["OKX:AAA"], "ticker")],
)
def test_bad_feeds_are_refused(tmp_path, name, patterns, message):
    with pytest.raises(ValueError, match=message):
        add_feed(tmp_path / "feeds.toml", name, patterns, None, [])


def test_a_feed_without_patterns_or_symbols_is_refused(tmp_path):
    path = tmp_path / "feeds.toml"
    path.write_text('[feeds.empty]\nwatchlist = "x"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="patterns or symbols"):
        load_feeds(path)


def test_the_plan_carries_only_symbols_that_passed():
    state = {
        "feeds": {
            "perp": {
                "watchlist_id": 5,
                "rows": {
                    "A": {"symbol": "X:AUSDT.P", "status": VERIFIED},
                    "B": {"symbol": "X:BUSDT.P", "status": MISSING},
                    "C": {"symbol": "X:C", "status": LISTED},
                },
            }
        }
    }
    [feed] = plan_feeds({"perp": PERP, "off": {**PERP, "enabled": False}}, state)
    assert feed["watchlist"] == {"name": "perps", "id": 5}
    assert feed["symbols"] == {"A": "X:AUSDT.P", "C": "X:C"}
