"""Command line for the Trading 212 to TradingView sync."""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

from t212_tv.feeds import USABLE, add_feed, load_feeds, pin_symbol, plan_feeds, remove_feed, resolve_feed, resolve_ticker
from t212_tv.notes import render, write_notes
from t212_tv.parser import ExportError, read_exports
from t212_tv.plan import DEFAULT_LAYOUT, DEFAULT_PAGE_SIZE, DEFAULT_WATCHLIST, build_plan, plan_pages
from t212_tv.positions import build_positions
from t212_tv.resolve import UnresolvedSymbols, load_overrides, resolve_all

REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = ".t212-tv-state.json"
PLAN_FILE = "t212-plan.json"
NOTES_FILE = "tradingview-notes.md"
OVERRIDES_FILE = "symbol-map.toml"
FEEDS_FILE = "feeds.toml"
# Reading every page after a reload is slow, so this outlasts a large portfolio.
TV_TIMEOUT = 600


def run_tv(*args: str) -> tuple[bool, dict]:
    """Run the `tv` CLI. A failure still carries JSON, often with partial ids."""
    done = subprocess.run(
        ["node", str(REPO_ROOT / "src/cli/index.js"), *args],
        capture_output=True,
        text=True,
        timeout=TV_TIMEOUT,
    )
    output = done.stdout.strip() or done.stderr.strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        payload = {"success": False, "error": output[-500:] or f"tv exited {done.returncode}"}
    return done.returncode == 0 and payload.get("success", False), payload


def search(ticker: str) -> list[dict]:
    ok, payload = run_tv("search", ticker)
    if not ok:
        raise RuntimeError(f"Symbol search for {ticker} failed: {payload.get('error')}")
    return payload["results"]


def load_state(ingest: Path) -> dict:
    path = ingest / STATE_FILE
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    # Earlier versions kept one layout per page: the first is the paged layout now.
    legacy = state.pop("layouts", None)
    if legacy and not state.get("layout_id"):
        state["layout_id"] = legacy.get(DEFAULT_LAYOUT)
    return state


def save_state(ingest: Path, state: dict) -> None:
    (ingest / STATE_FILE).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def load_positions(ingest: Path):
    sources = sorted(ingest.glob("*.csv"))
    if not sources:
        raise ExportError(f"No CSV exports in {ingest}")
    positions, closed = build_positions(read_exports(sources))
    return positions, closed, [p.name for p in sources]


def base_entries(state: dict, positions) -> list[dict]:
    return [{"ticker": p.ticker, "tv_symbol": state["symbols"][p.isin or p.ticker]} for p in positions]


def refresh_feeds(args, state: dict, positions, force: bool = False) -> None:
    """Check tickers no feed has seen yet, or every ticker when forced.

    A failed lookup is not fatal: positions still sync, and that feed just has
    fewer symbols until the next check.
    """
    entries = base_entries(state, positions)
    tickers = {e["ticker"] for e in entries}
    feeds = load_feeds(args.ingest / FEEDS_FILE)
    for name, feed in feeds.items():
        if not feed.get("enabled", True):
            continue
        saved = state.setdefault("feeds", {}).setdefault(name, {})
        rows = {t: r for t, r in saved.get("rows", {}).items() if t in tickers}
        for entry in entries:
            ticker = entry["ticker"]
            pinned = feed.get("symbols", {}).get(ticker)
            if not force and ticker in rows and (not pinned or rows[ticker]["symbol"] == pinned):
                continue
            try:
                rows[ticker] = resolve_ticker(feed, ticker, entry["tv_symbol"])
            except (urllib.error.URLError, TimeoutError) as err:
                print(f"warning: {name} {ticker}: {err}", file=sys.stderr)
        saved["rows"] = rows


def write_plan(args, plan: dict) -> None:
    (args.ingest / PLAN_FILE).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def page_size(args, state: dict) -> int:
    """A size given once sticks, so a later plain sync does not undo it."""
    if args.page_size is not None:
        state["page_size"] = args.page_size
    return state.get("page_size", DEFAULT_PAGE_SIZE)


def make_plan(args, state: dict, positions) -> dict:
    overrides = load_overrides(args.ingest / OVERRIDES_FILE)
    state["symbols"] = resolve_all(positions, search, overrides, state.get("symbols", {}))
    refresh_feeds(args, state, positions)
    plan = build_plan(
        positions,
        state["symbols"],
        state,
        layout_name=args.layout,
        watchlist_name=args.watchlist,
        activate=not args.no_activate,
        page_size=page_size(args, state),
        feeds=plan_feeds(load_feeds(args.ingest / FEEDS_FILE), state),
    )
    write_plan(args, plan)
    return plan


def cmd_positions(args) -> int:
    positions, closed, sources = load_positions(args.ingest)
    for p in positions:
        print(f"{p.ticker:8} {p.shares:>16} @ {p.entry_price} {p.currency}  ({p.trade_count} trades)")
    print(f"{len(positions)} open, {len(closed)} closed, from {len(sources)} export(s)")
    return 0


def print_pages(plan: dict) -> None:
    for index, page in enumerate(plan_pages(plan), start=1):
        print(f"Page {index}: " + ", ".join(p["ticker"] for p in page))


def cmd_plan(args) -> int:
    state = load_state(args.ingest)
    positions, _, _ = load_positions(args.ingest)
    plan = make_plan(args, state, positions)
    save_state(args.ingest, state)
    for p in plan["positions"]:
        print(f"{p['ticker']:8} {p['tv_symbol']:18} {p['entry_price']} {p['currency']}")
    print_pages(plan)
    print(f"Plan written to {args.ingest / PLAN_FILE}: {plan['layout']['name']}, {plan['page_size']} per page")
    return 0


def write_status(args, state: dict, plan: dict, verified: str) -> None:
    positions, closed, sources = load_positions(args.ingest)
    synced_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    block = render(positions, plan, state, closed, sources, synced_at, verified)
    write_notes(args.ingest / NOTES_FILE, block)


def describe_check(payload: dict) -> str:
    if payload.get("success"):
        return "passed after reloading the layout" if payload.get("reloaded") else "passed"
    return "FAILED: " + "; ".join(payload.get("problems") or [str(payload.get("error"))])


def refresh_plan_ids(args, state: dict, plan: dict) -> None:
    plan["watchlist"]["id"] = state.get("watchlist_id")
    plan["layout"]["id"] = state.get("layout_id")
    known = state.get("feeds", {})
    for feed in plan["feeds"]:
        feed["watchlist"]["id"] = known.get(feed["name"], {}).get("watchlist_id")
    write_plan(args, plan)


def remember_result(state: dict, result: dict) -> None:
    """Saved before anything else: a partial run must still remember what it created."""
    for wl in result.get("watchlists", []):
        if wl["feed"] == "primary":
            state["watchlist_id"] = wl["id"]
        else:
            state.setdefault("feeds", {}).setdefault(wl["feed"], {})["watchlist_id"] = wl["id"]
    if (result.get("layout") or {}).get("id"):
        state["layout_id"] = result["layout"]["id"]
    state["lines"] = {p["symbol"]: p["line_id"] for p in result.get("panes", [])}


def cmd_sync(args) -> int:
    state = load_state(args.ingest)
    positions, _, _ = load_positions(args.ingest)
    plan = make_plan(args, state, positions)
    save_state(args.ingest, state)

    ok, result = run_tv("portfolio", "apply", "--plan", str(args.ingest / PLAN_FILE))
    remember_result(state, result)
    save_state(args.ingest, state)
    refresh_plan_ids(args, state, plan)

    if not ok:
        verified = describe_check(result)
        write_status(args, state, plan, verified)
        print(verified, file=sys.stderr)
        return 1

    _, check = run_tv("portfolio", "verify", "--plan", str(args.ingest / PLAN_FILE), "--reload")
    verified = describe_check(check)
    write_status(args, state, plan, verified)
    drawn = sum(p["action"] == "drawn" for p in result["panes"])
    for wl in result["watchlists"]:
        print(f"Watchlist {wl['action']}: {wl['name']} ({wl['id']})")
    print(f"Layout: {plan['layout']['name']} ({state['layout_id']}), {plan['page_size']} per page")
    print_pages(plan)
    print(f"Entry lines: {drawn} drawn, {len(result['panes']) - drawn} kept")
    print(f"Verification {verified}")
    print(f"Notes: {args.ingest / NOTES_FILE}")
    return 0 if check.get("success") else 1


def cmd_verify(args) -> int:
    extra = ["--reload"] if args.reload else []
    _, check = run_tv("portfolio", "verify", "--plan", str(args.ingest / PLAN_FILE), *extra)
    print(f"Verification {describe_check(check)}")
    return 0 if check.get("success") else 1


def cmd_page(args) -> int:
    if args.page_size is not None:
        state = load_state(args.ingest)
        positions, _, _ = load_positions(args.ingest)
        make_plan(args, state, positions)
        save_state(args.ingest, state)

    extra = ["--feed", args.feed] if args.feed else []
    ok, result = run_tv("portfolio", "page", args.page, "--plan", str(args.ingest / PLAN_FILE), *extra)
    if not ok:
        print(f"error: {result.get('error')}", file=sys.stderr)
        return 1
    print(f"Page {result['page']} of {result['pages']} on {result['feed']}, {result['drawn']} line(s) drawn")
    for pane in result["panes"]:
        note = "  (feed has no symbol, showing primary)" if pane["fallback"] else ""
        print(f"  {pane['pane'] + 1}. {pane['symbol']:24} entry {pane['price']}{note}")
    return 0


def print_rows(name: str, rows: dict) -> None:
    for ticker, row in rows.items():
        gap = f"{row['gap']:+.2%}" if row.get("gap") is not None else "n/a"
        why = f"  {row['why']}" if row.get("why") else ""
        print(f"{name:10} {ticker:8} {row.get('symbol') or '-':26} {row['status']:9} {gap:>8}{why}")


def cmd_feed_list(args) -> int:
    state = load_state(args.ingest)
    for name, feed in load_feeds(args.ingest / FEEDS_FILE).items():
        enabled = "on" if feed.get("enabled", True) else "off"
        rows = state.get("feeds", {}).get(name, {}).get("rows", {})
        usable = sum(r["status"] in USABLE for r in rows.values())
        patterns = ", ".join(feed.get("patterns", [])) or "pinned symbols only"
        print(f"{name:10} {enabled:3} {feed['watchlist']:18} {usable}/{len(rows)} symbols  {patterns}")
    return 0


def cmd_feed_check(args) -> int:
    state = load_state(args.ingest)
    positions, _, _ = load_positions(args.ingest)
    state["symbols"] = resolve_all(positions, search, load_overrides(args.ingest / OVERRIDES_FILE), state.get("symbols", {}))
    entries = base_entries(state, positions)
    feeds = load_feeds(args.ingest / FEEDS_FILE)
    if args.name and args.name not in feeds:
        raise ValueError(f"No feed named {args.name}")
    for name, feed in feeds.items():
        if not feed.get("enabled", True) or args.name not in (None, name):
            continue
        rows = resolve_feed(feed, entries)
        state.setdefault("feeds", {}).setdefault(name, {})["rows"] = rows
        print_rows(name, rows)
    save_state(args.ingest, state)
    print("Run `t212-tv sync` to update the feed watchlists.")
    return 0


def cmd_feed_add(args) -> int:
    add_feed(args.ingest / FEEDS_FILE, args.name, args.pattern, args.list_name, args.extra)
    print(f"Saved feed {args.name}. Run `t212-tv feed check {args.name}`, then `t212-tv sync`.")
    return 0


def cmd_feed_remove(args) -> int:
    remove_feed(args.ingest / FEEDS_FILE, args.name)
    print(f"Feed {args.name} is off. Its TradingView watchlist was left in place; delete it there if unwanted.")
    return 0


def cmd_feed_pin(args) -> int:
    pin_symbol(args.ingest / FEEDS_FILE, args.name, args.ticker, args.symbol)
    print(f"{args.name} {args.ticker}: {args.symbol or 'pin cleared'}. Run `t212-tv feed check {args.name}`, then `t212-tv sync`.")
    return 0


def add_feed_commands(commands) -> None:
    feed = commands.add_parser("feed", help="Manage price feeds for out-of-hours tracking").add_subparsers(dest="feed_command", required=True)
    feed.add_parser("list", help="Show feeds and how many symbols passed").set_defaults(run=cmd_feed_list)

    check = feed.add_parser("check", help="Verify feed symbols against the primary price")
    check.add_argument("name", nargs="?")
    check.set_defaults(run=cmd_feed_check)

    add = feed.add_parser("add", help="Add a feed, or change one")
    add.add_argument("name")
    add.add_argument("--pattern", action="append", default=[], help="For example OKX:{ticker}USDT.P; repeat to try several")
    add.add_argument("--list-name", help="Watchlist name (default t212-<name>)")
    add.add_argument("--extra", action="append", default=[], help="Further symbol for the list, such as CME_MINI:ES1!")
    add.set_defaults(run=cmd_feed_add)

    remove = feed.add_parser("remove", help="Turn a feed off")
    remove.add_argument("name")
    remove.set_defaults(run=cmd_feed_remove)

    pin = feed.add_parser("pin", help="Fix one ticker to one symbol; leave the symbol out to clear it")
    pin.add_argument("name")
    pin.add_argument("ticker")
    pin.add_argument("symbol", nargs="?")
    pin.set_defaults(run=cmd_feed_pin)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="t212-tv", description=__doc__)
    parser.add_argument(
        "--ingest",
        type=Path,
        default=os.environ.get("T212_INGEST"),
        help="Folder holding the CSV exports (or set T212_INGEST)",
    )
    parser.add_argument("--layout", default=DEFAULT_LAYOUT)
    parser.add_argument("--watchlist", default=DEFAULT_WATCHLIST)
    parser.add_argument(
        "--page-size",
        type=int,
        help=f"Charts per page, usually 4 or 8; sticks once given (default: {DEFAULT_PAGE_SIZE}, Premium's limit)",
    )
    parser.add_argument("--no-activate", action="store_true", help="Create the list without making it active")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("positions", help="Show open positions; needs no TradingView").set_defaults(run=cmd_positions)
    commands.add_parser("plan", help="Resolve symbols and write the plan").set_defaults(run=cmd_plan)
    commands.add_parser("sync", help="Apply the plan, verify it and update the notes").set_defaults(run=cmd_sync)
    verify = commands.add_parser("verify", help="Compare the last plan with the live state")
    verify.add_argument("--reload", action="store_true", help="Reload the layout first, which proves it was saved")
    verify.set_defaults(run=cmd_verify)
    page = commands.add_parser("page", help="Flip the layout to a page or feed")
    page.add_argument("page", nargs="?", default="current", help="next, prev, current or a page number")
    page.add_argument("--feed", help="A feed name, or primary; keeps the feed showing when left out")
    page.set_defaults(run=cmd_page)
    add_feed_commands(commands)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.ingest:
        parser.error("--ingest or T212_INGEST is required")
    args.ingest = Path(args.ingest)
    try:
        return args.run(args)
    except (ExportError, UnresolvedSymbols, ValueError, RuntimeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
