"""Read Trading 212 CSV exports into trades."""

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

TRADE_ACTION = re.compile(r"^(?:market|limit|stop|stop limit) (buy|sell)$", re.IGNORECASE)
TIME_COLUMNS = ("Time (UTC)", "Time")
REQUIRED_COLUMNS = {
    "Action",
    "ISIN",
    "Ticker",
    "Name",
    "ID",
    "No. of shares",
    "Price / share",
    "Currency (Price / share)",
}


class ExportError(ValueError):
    """An export can't be read safely, so no positions should come from it."""


@dataclass(frozen=True)
class Trade:
    id: str
    time: datetime
    side: str
    ticker: str
    isin: str
    name: str
    shares: Decimal
    price: Decimal
    currency: str


def _decimal(value: str, column: str, row: dict) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as err:
        raise ExportError(f"Row {row.get('ID')}: bad {column}: {value!r}") from err


def _parse_row(row: dict, time_column: str) -> Trade:
    side = TRADE_ACTION.match(row["Action"].strip()).group(1).lower()
    return Trade(
        id=row["ID"],
        time=datetime.fromisoformat(row[time_column].strip()),
        side=side,
        ticker=row["Ticker"].strip(),
        isin=row["ISIN"].strip(),
        name=row["Name"].strip(),
        shares=_decimal(row["No. of shares"], "No. of shares", row),
        price=_decimal(row["Price / share"], "Price / share", row),
        currency=row["Currency (Price / share)"].strip(),
    )


def _is_ignorable(row: dict) -> bool:
    """Cash movements and dividends don't change share counts."""
    action = row["Action"].strip().lower()
    return not row["Ticker"].strip() or action.startswith("dividend")


def read_export(path: Path) -> list[Trade]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        time_column = next((c for c in TIME_COLUMNS if c in columns), None)
        missing = REQUIRED_COLUMNS - columns
        if missing or not time_column:
            needed = sorted(missing) or ["Time (UTC)"]
            raise ExportError(f"{path.name}: missing columns: {', '.join(needed)}")

        trades = []
        for row in reader:
            if TRADE_ACTION.match(row["Action"].strip()):
                trades.append(_parse_row(row, time_column))
            elif not _is_ignorable(row):
                # Splits and transfers change share counts, so guessing here
                # would put a wrong entry price on the chart.
                raise ExportError(f"{path.name}: unhandled action {row['Action']!r} on {row['Ticker']}")
        return trades


def read_exports(paths: list[Path]) -> list[Trade]:
    """Merge exports, oldest first. Overlapping date ranges repeat rows, so IDs dedupe them."""
    by_id: dict[str, Trade] = {}
    for path in sorted(paths):
        for trade in read_export(path):
            by_id.setdefault(trade.id, trade)
    return sorted(by_id.values(), key=lambda t: (t.time, t.id))
