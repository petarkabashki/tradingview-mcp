---
name: t212-portfolio-sync
description: Mirror Trading 212 CSV exports into TradingView as a watchlist, layouts and green dashed entry-price lines, and keep markdown notes on them. Use when the user adds or changes a Trading 212 export, asks to update entry lines or the positions list, or asks what is being managed in TradingView.
---

# Trading 212 Portfolio Sync

You keep TradingView in step with the user's Trading 212 positions. The Python tool in `portfolio/` reads the CSVs and works out the positions. The `tv portfolio` commands do the TradingView writes.

## What it manages

| Artefact | Default | Notes |
|----------|---------|-------|
| Watchlist | `t212-positions` | Every open position; made the active list in each managed layout |
| Layouts | `T212 Positions`, `T212 Positions 2`, ... | One chart per position, with a further layout past `--max-charts` |
| Entry lines | Green `#00C853`, width 4, dashed, labelled `T212 entry <price>` | The price is the pooled average cost of the open position |
| Notes | `<ingest>/tradingview-notes.md` | Assets, ids, panes and line style; rewritten on every sync |

Files in the ingest folder:

- `*.csv`: the Trading 212 exports. Overlapping exports are fine, rows are deduplicated by `ID`.
- `.t212-tv-state.json`: the ids of what the tool owns. It only touches these.
- `t212-plan.json`: the last plan.
- `tradingview-notes.md`: the notes.
- `symbol-map.toml` (optional): your overrides from ISIN or ticker to TradingView symbol.

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

`sync` is idempotent: matching lines are kept, and a line at an old price or in an old style is replaced. Options, which go before the subcommand:

- `--max-charts N`: charts per layout. Defaults to 8, the Premium limit. Lower it on a smaller plan.
- `--layout NAME`, `--watchlist NAME`: use other names.
- `--no-activate`: leave the active watchlist alone.

## Step 4: Verify

```bash
uv run --project portfolio t212-tv --ingest <folder> verify --reload
```

`--reload` reloads the managed tabs first, so a pass shows the lines survived on the server and not only in the page. Report any `problems` to the user as they are. For a visual check, bring a layout forward and take a screenshot:

```bash
node src/cli/index.js tab switch <index>
node src/cli/index.js screenshot -r full -o <name>
```

`screenshots/` is ignored by git. Keep it that way: these images show the user's positions.

## Rules

- Only write through `t212-tv sync` or `tv portfolio apply`. Each `tv` call attaches to whichever chart tab comes first, so a loop of `tv draw` or `tv symbol` calls can land on the wrong layout. `apply` runs in one process and confirms the layout's id and name before each write.
- Leave the user's other layouts, watchlists and drawings alone.
- Never click an upgrade or purchase prompt. TradingView silently refuses a grid above the plan's limit and shows an upsell; `apply` reports this as a grid error, and the answer is a lower `--max-charts`.

## Known limits

- Lines belong to the pane they were drawn on, so a chart cloned from another does not show them.
- Only lines on the symbol a pane currently shows can be read. If a position closes or moves to another pane, its old line stays on that pane's previous symbol, hidden until the pane shows that symbol again.
- TradingView may report another feed for a symbol, such as `BATS:AMAT` for `NASDAQ:AMAT`. Panes are therefore compared by ticker.
