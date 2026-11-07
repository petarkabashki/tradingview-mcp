from datetime import datetime
from decimal import Decimal

import pytest

from t212_tv.pnl import compute, render_table, to_json
from t212_tv.positions import Position

D = Decimal


def position(ticker, shares, entry, currency="USD"):
    shares, entry = D(shares), D(entry)
    return Position(ticker, "", ticker, currency, shares, entry, shares * entry, datetime(2026, 1, 1), 1)


def prices(table):
    return lambda symbol: table.get(symbol, {})


def no_fx(src, dst):
    raise AssertionError(f"no FX needed, asked for {src}{dst}")


def test_per_asset_and_portfolio_figures():
    held = [position("AAA", "10", "100"), position("BBB", "4", "50")]
    quotes = {"X:AAA": {"close": 110, "currency": "USD"}, "X:BBB": {"close": 40, "currency": "USD"}}
    result = compute(held, {"AAA": "X:AAA", "BBB": "X:BBB"}, prices(quotes), no_fx)
    a, b = result["rows"]

    assert a["value"] == D(1100) and a["pnl"] == D(100) and a["pnl_pct"] == D(10)
    assert b["value"] == D(160) and b["pnl"] == D(-40) and b["pnl_pct"] == D(-20)
    # Weight is by market value: 1100 of 1260.
    assert round(a["weight_pct"], 4) == D("87.3016")
    assert a["weight_pct"] + b["weight_pct"] == D(100)
    # Contributions are P&L over total cost (1200), so they add up to the total return.
    assert a["contrib_pct"] + b["contrib_pct"] == result["total"]["pnl_pct"] == D(5)
    assert result["total"] == {"cost": D(1200), "value": D(1260), "pnl": D(60), "pnl_pct": D(5), "priced": 2, "positions": 2}
    assert result["base"] == "USD" and result["missing"] == []


def test_feed_symbol_falls_back_to_primary_when_it_has_no_price():
    held = [position("AAA", "1", "100")]
    quotes = {"PRIMARY:AAA": {"close": 120, "currency": "USD"}}
    row = compute(held, {"AAA": ["BOATS:AAA", "PRIMARY:AAA"]}, prices(quotes), no_fx)["rows"][0]
    assert row["price"] == D(120) and row["source"] == "PRIMARY:AAA"


def test_feed_symbol_wins_when_it_has_a_price():
    held = [position("AAA", "1", "100")]
    quotes = {"PERP:AAA": {"close": 105, "currency": "USDT"}, "PRIMARY:AAA": {"close": 120, "currency": "USD"}}
    # A USDT quote is a different currency from the USD position, so it is not used.
    row = compute(held, {"AAA": ["PERP:AAA", "PRIMARY:AAA"]}, prices(quotes), no_fx)["rows"][0]
    assert row["source"] == "PRIMARY:AAA"

    quotes["PERP:AAA"]["currency"] = "USD"
    row = compute(held, {"AAA": ["PERP:AAA", "PRIMARY:AAA"]}, prices(quotes), no_fx)["rows"][0]
    assert row["source"] == "PERP:AAA" and row["price"] == D(105)


def test_unpriced_position_is_reported_and_left_out_of_totals():
    held = [position("AAA", "2", "100"), position("BBB", "1", "50")]
    result = compute(held, {"AAA": "X:AAA", "BBB": "X:BBB"}, prices({"X:AAA": {"close": 150, "currency": "USD"}}), no_fx)
    assert result["missing"] == ["BBB"]
    assert result["rows"][1]["why"] == "no price for X:BBB"
    assert result["total"]["value"] == D(300) and result["total"]["priced"] == 1
    assert result["rows"][0]["weight_pct"] == D(100)


def test_override_skips_the_lookup():
    held = [position("AAA", "2", "100")]
    row = compute(held, {"AAA": "X:AAA"}, prices({}), no_fx, overrides={"AAA": D("90")})["rows"][0]
    assert row["pnl"] == D(-20) and row["source"] == "override"


def test_mixed_currencies_convert_into_the_base():
    held = [position("AAA", "1", "100", "USD"), position("CCC", "10", "10", "EUR")]
    quotes = {"X:AAA": {"close": 100, "currency": "USD"}, "X:CCC": {"close": 12, "currency": "EUR"}}
    fx = {("EUR", "USD"): 2}
    result = compute(held, {"AAA": "X:AAA", "CCC": "X:CCC"}, prices(quotes), lambda s, d: fx.get((s, d)))
    eur = result["rows"][1]
    assert result["base"] == "USD" and result["fx"] == {"EUR": D(2)}
    # Percent return is in the instrument's currency; weights use base values.
    assert eur["pnl"] == D(20) and eur["pnl_pct"] == D(20) and eur["base_value"] == D(240)
    assert eur["weight_pct"] == D(240) / D(340) * 100
    assert result["total"]["cost"] == D(300) and result["total"]["pnl"] == D(40)


def test_missing_fx_rate_marks_the_row():
    held = [position("CCC", "1", "10", "EUR")]
    result = compute(held, {"CCC": "X:CCC"}, prices({"X:CCC": {"close": 11, "currency": "EUR"}}), lambda s, d: None, base="USD")
    assert result["missing"] == ["CCC"] and result["rows"][0]["why"] == "no FX rate EURUSD"


def test_json_and_table_render():
    held = [position("AAA", "3", "100"), position("BBB", "1", "50")]
    result = compute(held, {"AAA": "X:AAA", "BBB": "X:BBB"}, prices({"X:AAA": {"close": 101.5, "currency": "USD"}}), no_fx)
    payload = to_json(result)
    assert payload["rows"][0]["pnl"] == 4.5 and payload["rows"][0]["pnl_pct"] == 1.5
    table = render_table(result)
    assert "AAA" in table and "not priced: no price for X:BBB" in table and "1 of 2 positions priced" in table


@pytest.mark.parametrize("bad", ["AAA", "AAA=abc"])
def test_cli_rejects_bad_price_override(bad):
    from t212_tv.cli import parse_prices

    with pytest.raises(ValueError):
        parse_prices([bad])
