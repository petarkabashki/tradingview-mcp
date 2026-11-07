"""Build the plan that `tv portfolio apply` executes."""

from t212_tv.positions import Position

DEFAULT_LAYOUT = "T212 Positions"
DEFAULT_WATCHLIST = "t212-positions"
# TradingView caps charts per tab by plan: this account is on Premium.
DEFAULT_MAX_CHARTS = 8
ENTRY_LINE = {"color": "#00C853", "width": 4, "style": 2, "tag": "T212 entry"}
GRIDS = [("s", 1), ("2h", 2), ("3h", 3), ("4", 4), ("6", 6), ("8", 8), ("10", 10), ("12", 12), ("14", 14), ("16", 16)]


def pick_grid(count: int) -> str:
    """Smallest grid holding every position."""
    for code, capacity in GRIDS:
        if capacity >= count:
            return code
    raise ValueError(f"{count} positions exceed the largest grid (16 charts)")


def page_name(base: str, page: int) -> str:
    """The first layout keeps the plain name, so a small portfolio has just that one."""
    return base if page == 0 else f"{base} {page + 1}"


def plan_positions(plan: dict) -> list[dict]:
    return [p for layout in plan["layouts"] for p in layout["positions"]]


def build_plan(
    positions: list[Position],
    symbols: dict[str, str],
    state: dict,
    layout_name: str = DEFAULT_LAYOUT,
    watchlist_name: str = DEFAULT_WATCHLIST,
    activate: bool = True,
    max_charts: int = DEFAULT_MAX_CHARTS,
) -> dict:
    if not positions:
        raise ValueError("No open positions to plan")
    if not 1 <= max_charts <= 16:
        raise ValueError(f"max_charts must be between 1 and 16, got {max_charts}")
    entries = [
        {
            "ticker": p.ticker,
            "tv_symbol": symbols[p.isin or p.ticker],
            "entry_price": float(p.entry_price),
            "currency": p.currency,
        }
        for p in positions
    ]
    known = state.get("layouts", {})
    pages = [entries[i : i + max_charts] for i in range(0, len(entries), max_charts)]
    return {
        "version": 2,
        "watchlist": {"name": watchlist_name, "id": state.get("watchlist_id"), "activate": activate},
        "layouts": [
            {
                "name": page_name(layout_name, index),
                "id": known.get(page_name(layout_name, index)),
                "grid": pick_grid(len(page)),
                "positions": page,
            }
            for index, page in enumerate(pages)
        ],
        "line": ENTRY_LINE,
    }
