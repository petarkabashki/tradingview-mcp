---
name: t212-portfolio-sync
description: Mirror Trading 212 CSV exports into one paged TradingView layout with a watchlist, green dashed entry-price lines and out-of-hours feeds, and keep markdown notes on them. Use when the user adds or changes a Trading 212 export, asks to flip pages or change page size, asks to see prices outside US hours or manage feeds and providers, asks to update entry lines or the positions list, or asks what is being managed in TradingView.
---

# Trading 212 Portfolio Sync

You keep TradingView in step with the user's Trading 212 positions. The Python tool in `portfolio/` reads the CSVs and works out the positions. The `tv portfolio` commands do the TradingView writes.

## What it manages

| Artefact | Default | Notes |
|----------|---------|-------|
| Layout | `T212 Positions` | One layout. Positions fill pages of 4 or 8 charts, and a page flip swaps the symbols in place |
| Watchlist | `t212-positions` | Every open position; made the active list in the layout |
| Feed watchlists | `t212-overnight`, `t212-perps` | The same positions on another provider, plus `ES1!` and `NQ1!` |
| Entry lines | Green `#00C853`, width 4, dashed, labelled `T212 entry <price>` | The price is the pooled average cost of the open position |
| Notes | `<ingest>/tradingview-notes.md` | Assets, ids, pages, feeds and line style; rewritten on every sync |

Files in the ingest folder:

- `*.csv`: the Trading 212 exports. Overlapping exports are fine, rows are deduplicated by `ID`.
- `.t212-tv-state.json`: the ids of what the tool owns, plus the last feed check and page size. It only touches these.
- `t212-plan.json`: the last plan.
- `tradingview-notes.md`: the notes.
- `symbol-map.toml` (optional): your overrides from ISIN or ticker to TradingView symbol.
- `feeds.toml` (optional): your feeds and pinned symbols.

The ingest folder holds personal trade data. Never commit it, and never copy it into the repo.

## Step 1: Check the connection

```bash
node src/cli/index.js status
```

If it fails, ask the user to start TradingView with `--remote-debugging-port=9222`. Do not use `tv_launch` with its default `kill_existing`: that closes their running app.

## Step 2: Review the positions

```bash
uv run --project portfolio t212-tv --ingest <folder> positions
```

Stop and tell the user if this fails. It refuses an action type it does not recognise and a sale of more shares than the exports show as held, because guessing there would put lines at the wrong price. The usual cause of the second one is an export that starts after the first purchase, so ask for a longer export.

## Step 3: Preview, then sync

```bash
uv run --project portfolio t212-tv --ingest <folder> plan
uv run --project portfolio t212-tv --ingest <folder> sync
```

`plan` resolves symbols and writes the plan without changing TradingView. If a symbol cannot be resolved, add it to `symbol-map.toml`, for example:

```toml
[symbols]
US0378331005 = "NASDAQ:AAPL"
```

`sync` is idempotent: matching lines are kept, and a line at an old price or in an old style is replaced. It leaves the layout on page 1. Options, which go before the subcommand:

- `--page-size N`: charts per page, 4 or 8 in normal use. Defaults to 8, the Premium limit. It sticks until changed.
- `--layout NAME`, `--watchlist NAME`: use other names.
- `--no-activate`: leave the active watchlist alone.

## Step 4: Flip pages

```bash
uv run --project portfolio t212-tv --ingest <folder> page next
uv run --project portfolio t212-tv --ingest <folder> page prev
uv run --project portfolio t212-tv --ingest <folder> page 2
uv run --project portfolio t212-tv --ingest <folder> --page-size 4 page 1
```

`next` and `prev` wrap around. Each page puts its positions in the grid and draws any entry line that is missing, so a page heals itself. The result lists the entries on the page.

Grids fill column by column on an 8-chart page, so read a pane's symbol from the output and not from its position on screen.

From an agent, `portfolio_page` does the same with a `plan_path`, `page` and optional `feed`. Pass the page as a number, `next`, `prev` or `current`.

## Step 5: See prices outside US hours

A feed shows the same positions from another provider. Switch with `--feed`, and the feed stays on while you flip pages:

```bash
uv run --project portfolio t212-tv --ingest <folder> page --feed boats
uv run --project portfolio t212-tv --ingest <folder> page next
uv run --project portfolio t212-tv --ingest <folder> page --feed primary
```

| Feed | Symbols | Hours | Use |
|------|---------|-------|-----|
| `primary` | The listing, such as `NASDAQ:AAPL` | US sessions | The real price |
| `boats` | `BOATS:AAPL` | Overnight, 24/5 | Real US share trades, but thin |
| `perp` | `BINANCE:AAPLUSDT.P`, then `BINANCE:AAPLXUSDT.P` | 24/7 | A crypto perpetual tracking the stock, for reference only |

Tell the user what a feed is not:

- A perpetual trades on Binance and is quoted in USDT. It can drift from the stock and it is not tradable on Trading 212.
- BOATS is thin and closed at weekends, so a weekend price is Friday's.
- An entry line is the same on every feed. It is the Trading 212 average cost, not a price on that venue.

Manage feeds with these:

```bash
uv run --project portfolio t212-tv --ingest <folder> feed list
uv run --project portfolio t212-tv --ingest <folder> feed check [NAME]
uv run --project portfolio t212-tv --ingest <folder> feed add okx --pattern 'OKX:{ticker}USDT.P' --extra CME_MINI:ES1!
uv run --project portfolio t212-tv --ingest <folder> feed remove okx
uv run --project portfolio t212-tv --ingest <folder> feed pin perp BRK.B BINANCE:BRKBUSDT.P
uv run --project portfolio t212-tv --ingest <folder> feed pin perp BRK.B
```

`feed add` on a built-in feed changes its patterns and keeps the rest. `feed remove` on a built-in feed switches it off. `feed pin` with no symbol clears the pin. Run `sync` after any change to put it into TradingView.

`feed check` tests each candidate symbol against the listing's price:

| Status | Meaning |
|--------|---------|
| `verified` | The price is within 15% of the listing and the quote currency fits |
| `listed` | The venue lists the company, but TradingView gave no price to compare |
| `pinned` | The user set it; nothing was checked |
| `missing` | Nothing passed. The page shows the primary symbol for that position and says so |

Never relax this check to fill a gap. Tickers collide across asset classes: `STXUSDT.P` is the Stacks token at a fraction of Seagate's price, and the price test is what refuses it. Pin the right symbol instead.

## Step 6: Verify

```bash
uv run --project portfolio t212-tv --ingest <folder> verify --reload
```

`--reload` reloads the layout's tab first, so a pass shows the lines survived on the server and not only in the page. Every page is flipped through and your view is put back. Report any `problems` to the user as they are. For a visual check, bring the layout forward and take a screenshot:

```bash
node src/cli/index.js tab switch <index>
node src/cli/index.js screenshot -r full -o <name>
```

`screenshots/` is ignored by git. Keep it that way: these images show the user's positions.

## Rules

- Only write through `t212-tv` or `tv portfolio`. Each `tv` call attaches to whichever chart tab comes first, so a loop of `tv draw` or `tv symbol` calls can land on the wrong layout. `apply` and `page` run in one process and confirm the layout's id and name before each write.
- Leave the user's other layouts, watchlists and drawings alone.
- `tab_close` closes whichever tab the tab bar shows as active. Before closing, read `shell_tabs` from `tab_list` and confirm that is the tab you mean. `tab_switch` returns `shell_active: null` when it could not match the tab bar, for example when a layout is open twice: stop there.
- Never click an upgrade or purchase prompt. TradingView silently refuses a grid above the plan's limit and shows an upsell; the tool reports this as a grid error, and the answer is a lower `--page-size`.

## Known limits

- TradingView stores a line against a pane and a symbol, and it comes back when that pane shows that symbol. The first view of a page on a feed draws its lines and takes about 20 seconds. Later views take 3 to 8.
- Only lines on the symbol a pane currently shows can be read. If a position closes, or a change in page size moves it to another pane, its old line stays hidden on the old pane and symbol until that pane shows the symbol again. A line at the wrong price or in the wrong style is replaced only when its pane is showing its symbol.
- A page does not change the active watchlist. Pick `t212-overnight` or `t212-perps` in the watchlist panel to see those prices listed.
- TradingView's 24h session toggle is a per-chart setting on intraday timeframes. The tool does not set it.
- TradingView may report another feed for a symbol, such as `BATS:AMAT` for `NASDAQ:AMAT`, or `BOATS_LS:AMAT` for `BOATS:AMAT`. On a primary page these are compared by ticker. On a feed page the exchange must match too.
