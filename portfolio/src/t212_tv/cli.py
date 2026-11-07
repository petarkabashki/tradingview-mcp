"""Command line for the Trading 212 to TradingView sync."""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from t212_tv.notes import render, write_notes
from t212_tv.parser import ExportError, read_exports
from t212_tv.plan import DEFAULT_LAYOUT, DEFAULT_MAX_CHARTS, DEFAULT_WATCHLIST, build_plan, plan_positions
from t212_tv.positions import build_positions
from t212_tv.resolve import UnresolvedSymbols, load_overrides, resolve_all

REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = ".t212-tv-state.json"
PLAN_FILE = "t212-plan.json"
NOTES_FILE = "tradingview-notes.md"
OVERRIDES_FILE = "symbol-map.toml"


def run_tv(*args: str) -> tuple[bool, dict]:
    """Run the `tv` CLI. A failure still carries JSON, often with partial ids."""
    done = subprocess.run(
        ["node", str(REPO_ROOT / "src/cli/index.js"), *args],
        capture_output=True,
        text=True,
        timeout=300,
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
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(ingest: Path, state: dict) -> None:
    (ingest / STATE_FILE).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def load_positions(ingest: Path):
    sources = sorted(ingest.glob("*.csv"))
    if not sources:
        raise ExportError(f"No CSV exports in {ingest}")
    positions, closed = build_positions(read_exports(sources))
    return positions, closed, [p.name for p in sources]


def make_plan(args, state: dict, positions) -> dict:
    overrides = load_overrides(args.ingest / OVERRIDES_FILE)
    state["symbols"] = resolve_all(positions, search, overrides, state.get("symbols", {}))
    plan = build_plan(
        positions,
        state["symbols"],
        state,
        layout_name=args.layout,
        watchlist_name=args.watchlist,
        activate=not args.no_activate,
        max_charts=args.max_charts,
    )
    (args.ingest / PLAN_FILE).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return plan


def cmd_positions(args) -> int:
    positions, closed, sources = load_positions(args.ingest)
    for p in positions:
        print(f"{p.ticker:8} {p.shares:>16} @ {p.entry_price} {p.currency}  ({p.trade_count} trades)")
    print(f"{len(positions)} open, {len(closed)} closed, from {len(sources)} export(s)")
    return 0


def cmd_plan(args) -> int:
    state = load_state(args.ingest)
    positions, _, _ = load_positions(args.ingest)
    plan = make_plan(args, state, positions)
    save_state(args.ingest, state)
    for p in plan_positions(plan):
        print(f"{p['ticker']:8} {p['tv_symbol']:18} {p['entry_price']} {p['currency']}")
    grids = ", ".join(f"{layout['name']} (grid {layout['grid']})" for layout in plan["layouts"])
    print(f"Plan written to {args.ingest / PLAN_FILE}: {grids}")
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
    known = state.get("layouts", {})
    for layout in plan["layouts"]:
        layout["id"] = known.get(layout["name"])
    (args.ingest / PLAN_FILE).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def cmd_sync(args) -> int:
    state = load_state(args.ingest)
    positions, _, _ = load_positions(args.ingest)
    plan = make_plan(args, state, positions)
    save_state(args.ingest, state)

    ok, result = run_tv("portfolio", "apply", "--plan", str(args.ingest / PLAN_FILE))

    # Saved first: a partial run must still remember what it created.
    if (result.get("watchlist") or {}).get("id"):
        state["watchlist_id"] = result["watchlist"]["id"]
    for layout in result.get("layouts", []):
        state.setdefault("layouts", {})[layout["name"]] = layout["id"]
    state["lines"] = {p["symbol"]: p["line_id"] for p in result.get("panes", [])}
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
    print(f"Watchlist {result['watchlist']['action']}: {plan['watchlist']['name']} ({state['watchlist_id']})")
    print("Layouts: " + ", ".join(f"{layout['name']} ({layout['id']})" for layout in plan["layouts"]))
    print(f"Entry lines: {drawn} drawn, {len(result['panes']) - drawn} kept")
    print(f"Verification {verified}")
    print(f"Notes: {args.ingest / NOTES_FILE}")
    return 0 if check.get("success") else 1


def cmd_verify(args) -> int:
    extra = ["--reload"] if args.reload else []
    _, check = run_tv("portfolio", "verify", "--plan", str(args.ingest / PLAN_FILE), *extra)
    print(f"Verification {describe_check(check)}")
    return 0 if check.get("success") else 1


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
        "--max-charts",
        type=int,
        default=DEFAULT_MAX_CHARTS,
        help="Charts per layout; positions beyond this go to a further layout (default: %(default)s, Premium's limit)",
    )
    parser.add_argument("--no-activate", action="store_true", help="Create the list without making it active")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("positions", help="Show open positions; needs no TradingView").set_defaults(run=cmd_positions)
    commands.add_parser("plan", help="Resolve symbols and write the plan").set_defaults(run=cmd_plan)
    commands.add_parser("sync", help="Apply the plan, verify it and update the notes").set_defaults(run=cmd_sync)
    verify = commands.add_parser("verify", help="Compare the last plan with the live state")
    verify.add_argument("--reload", action="store_true", help="Reload the layout first, which proves it was saved")
    verify.set_defaults(run=cmd_verify)
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
