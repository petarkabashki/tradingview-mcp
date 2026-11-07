"""Build the plan that `tv portfolio apply` executes."""

from t212_tv.pine import INDICATOR_NAME, render_pine
from t212_tv.positions import Position

DEFAULT_LAYOUT = "T212 Positions"
DEFAULT_WATCHLIST = "t212-positions"
# TradingView caps charts per tab by plan: this account is on Premium.
DEFAULT_PAGE_SIZE = 8
ENTRY_LINE = {"color": "#00C853", "width": 4, "style": 2, "tag": "T212 entry"}


def plan_pages(plan: dict) -> list[list[dict]]:
    """Pages are consecutive slices of the positions, so the plan stores only the size."""
    size = plan["page_size"]
    positions = plan["positions"]
    return [positions[i : i + size] for i in range(0, len(positions), size)]


def build_plan(
    positions: list[Position],
    symbols: dict[str, str],
    state: dict,
    layout_name: str = DEFAULT_LAYOUT,
    watchlist_name: str = DEFAULT_WATCHLIST,
    activate: bool = True,
    page_size: int = DEFAULT_PAGE_SIZE,
    feeds: list[dict] | None = None,
) -> dict:
    if not positions:
        raise ValueError("No open positions to plan")
    if not 1 <= page_size <= 16:
        raise ValueError(f"page_size must be between 1 and 16, got {page_size}")
    entries = [
        {
            "ticker": p.ticker,
            "tv_symbol": symbols[p.isin or p.ticker],
            "entry_price": float(p.entry_price),
            "currency": p.currency,
        }
        for p in positions
    ]
    feeds = feeds or []
    return {
        "version": 4,
        "watchlist": {"name": watchlist_name, "id": state.get("watchlist_id"), "activate": activate},
        "layout": {"name": layout_name, "id": state.get("layout_id")},
        "page_size": page_size,
        "positions": entries,
        "feeds": feeds,
        "line": ENTRY_LINE,
        "indicator": {"name": INDICATOR_NAME, "source": render_pine(entries, feeds, ENTRY_LINE)},
    }
