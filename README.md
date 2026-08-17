# fpl-squad-optimizer

A Fantasy Premier League (FPL) team forecasting tool. Given a target
gameweek, it recommends the 15-man squad, starting XI, formation,
captain, and vice-captain that maximize projected points — subject to the
real FPL budget and squad-composition rules — using live data from the
official FPL API.

```
$ python -m fpl_forecast --gameweek 5

=== FPL Squad Recommendation — Gameweek 5 ===

Formation: 3-4-3  |  Budget used: £99.5m / £100.0m
Projected starting-XI xPts: 61.42

Captain:      M.Salah (9.87 xPts)
Vice-Captain: Haaland (9.41 xPts)
...
```

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/maxefunk/fpl-squad-optimizer
cd fpl-squad-optimizer
python3 -m venv .venv
source .venv/bin/activate
pip install -e . -r requirements.txt
```

## Usage

```bash
# Recommend a squad for a specific gameweek
python -m fpl_forecast --gameweek 5

# Default: targets the next unplayed gameweek
python -m fpl_forecast

# Configurable budget / per-club limit
python -m fpl_forecast --gameweek 5 --budget 95.5 --max-per-club 2

# Bypass the local cache and refetch everything from the FPL API
python -m fpl_forecast --gameweek 5 --refresh

# Also write a standalone HTML pitch-view report
python -m fpl_forecast --gameweek 5 --html squad.html
```

`--html` writes a self-contained report (inline CSS, no external
requests) with the starting XI laid out on a pitch by position, captain/
vice-captain badges, a budget bar, the bench in sub order, and the
reasoning behind the top picks — open the file directly in a browser.

API responses are cached to `data/cache/` (gitignored) so repeat runs are
fast and don't hammer the FPL API. `--refresh` forces a live refetch.

### Hosted report (GitHub Pages)

If you'd rather not run the CLI yourself, `.github/workflows/publish-report.yml`
regenerates the HTML report daily (and on manual trigger) and publishes it
to GitHub Pages, so you can just visit a URL. It always targets the next
unplayed gameweek — no gameweek number needs to be updated as the season
progresses. To turn it on (one-time, repo-admin only):

1. **Settings → Pages → Build and deployment → Source: "GitHub Actions"**.
2. Run the workflow once manually: **Actions → Publish squad report → Run workflow**
   (optionally pass a specific `gameweek`; leave blank for the next unplayed one).
3. The report will be live at `https://<owner>.github.io/fpl-squad-optimizer/`
   and refresh automatically every day at 09:00 UTC after that.

### Backtesting

`scripts/backtest.py` runs the model against a **completed** gameweek and
compares the recommended starting XI's actual points to the actual
"Dream Team" benchmark for that gameweek:

```bash
python scripts/backtest.py --gameweek 4
```

See [Backtesting caveats](#backtesting-caveats) below — this is a useful
sanity check, not a rigorous historical evaluation.

### Tests

```bash
pytest
```

## How it works

### Data sources (official FPL API, no auth required)

| Endpoint | Used for |
|---|---|
| `bootstrap-static/` | Players, teams, prices, positions, season-to-date stats, current gameweek |
| `fixtures/?event={gw}` | Fixture list + FDR (`team_h_difficulty` / `team_a_difficulty`) for the target gameweek |
| `element-summary/{id}/` | Per-player gameweek-by-gameweek history (recency-weighted form) and past-season summaries |
| `team/set-piece-notes/` | Penalty/free-kick/corner-taker notes, folded in as a small MID/FWD bonus |
| `event/{gw}/live/` | Actual per-player stats for a completed gameweek (backtesting) |
| `dream-team/{gw}/` | The actual best-performing XI for a completed gameweek (backtest benchmark) |
| `event-status/` | Whether a gameweek's bonus points are finalized (backtest sanity check) |

### Scoring model (xPts)

There's no publicly available ground-truth "expected points" to train a
regression against, and a full match-simulation model is out of scope for
a v1 tool, so this uses a **transparent, tunable weighted composite**
rather than a fitted statistical model. The full implementation is in
[`src/fpl_forecast/scoring.py`](src/fpl_forecast/scoring.py); constants
live in [`src/fpl_forecast/constants.py`](src/fpl_forecast/constants.py).

For each player and target gameweek:

1. **Fixture-adjusted model component** (45% weight) — assumes a full 90
   minutes and combines:
   - Attacking threat: the player's per-90 expected goals/assists
     (`expected_goals_per_90` / `expected_assists_per_90` from the API
     when available, else actual goals/assists per-90 as a fallback),
     converted to points using FPL's own goal-scoring values per position
     (GK/DEF = 6, MID = 5, FWD = 4; assists = 3 for all positions).
   - A **simplified independent-Poisson clean-sheet/goals model**: each
     team's expected goals for/against in the fixture is estimated from
     FPL's own `strength_attack_*` / `strength_defence_*` team ratings
     (scaled relative to the league average), and clean-sheet probability
     is `P(0 goals conceded) = e^-λ` under that Poisson assumption. This
     also scales the attacking-threat estimate up or down for fixture
     difficulty, and (for GK/DEF) applies FPL's "-1 point per 2 goals
     conceded" deduction as an expected-value term.
   - Clean-sheet points (GK/DEF = 4, MID = 1), GK save points
     (`saves_per_90 / 3`), and the 2-point full-appearance value.
2. **Recency-weighted form component** (35% weight) — a weighted average
   of actual points over the last 6 gameweeks (from `element-summary`
   history), most-recent GW weighted highest
   (`[0.32, 0.24, 0.18, 0.13, 0.08, 0.05]`). Falls back to the API's own
   `form` field if per-GW history isn't available.
3. **Season-long prior** (20% weight) — season `points_per_game`, to
   stabilize small samples (e.g. a player who just returned from injury).

The three components are blended, then the whole thing is **scaled by an
availability probability** (see below) and nudged by a small set-piece-duty
bonus (`+0.35` for a primary penalty taker, `+0.15` for a corner/free-kick
taker, MID/FWD only).

**Minutes reliability / rotation risk**: availability probability comes
from, in priority order: (1) the API's own `chance_of_playing_next_round`
if set; (2) the fraction of the last 6 gameweeks where the player started
(≥60 minutes), from `element-summary` history; (3) a conservative default
based on season-long minutes for players with no recent history. This is
what keeps the optimizer from recommending nailed-off bench players just
because their per-90 stats look good in a small sample.

**Double/blank gameweeks**: a player's team may have 0, 1, or 2 fixtures
in the target gameweek. Blank-gameweek players score 0 xPts (the optimizer
will only pick them as cheap bench filler); double-gameweek players sum
their model component across both fixtures.

**Set-piece duty** (`team/set-piece-notes/`): this endpoint returns
free-text notes per team rather than structured taker IDs, so the parser
does a best-effort, case-insensitive name match against the text following
"Penalties" / "Corners" / "Free Kicks" headers. See
[Known limitations](#known-limitations).

### Optimization

A proper constrained MILP solver (**PuLP**, using the bundled CBC solver),
not a greedy heuristic — see
[`src/fpl_forecast/optimizer.py`](src/fpl_forecast/optimizer.py). One
integer program selects the 15-man squad **and** the starting XI
simultaneously, maximizing total starting-XI xPts subject to:

- Exactly 15 players: 2 GK, 5 DEF, 5 MID, 3 FWD
- Total squad cost ≤ budget (default £100.0m, configurable via `--budget`)
- At most 3 players from any one real-world club (`--max-per-club`)
- A valid starting XI: 1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD, 11 total, and every
  starter must be in the 15-man squad

Captain and vice-captain are **not** decision variables in the MILP — per
spec, they're simply the highest- and second-highest-xPts players in the
optimizer's chosen starting XI, assigned after the solve. Bench order is
the 3 outfield bench players sorted by xPts (highest first = first sub),
then the bench goalkeeper last.

## Known limitations

- **Heuristic scoring, not a fitted model.** The xPts weights (45/35/20,
  the set-piece bonus sizes, the Poisson calibration constant) are
  reasonable starting points, not fit to historical accuracy. The
  `scripts/backtest.py` script is provided to sanity-check and tune them
  against completed gameweeks.
- **Set-piece-note parsing is a text heuristic.** `team/set-piece-notes/`
  returns free-text, not structured taker IDs; the name-matching parser
  can miss or misattribute duties if the source text's format changes or
  uses a different name form than `web_name`/`second_name`.
- **No live injury-news scraping.** Availability relies on the FPL API's
  own `chance_of_playing_next_round`/`status` fields and recent-minutes
  history, not press conferences or breaking news. **Flagged as a future
  improvement.**
- **No multi-gameweek transfer planning.** This is single-gameweek squad
  selection from scratch — it doesn't know about a squad you already own,
  free transfers, hits, chips, or future-gameweek planning. **Explicitly
  out of scope for v1.**
- **No betting/wagering functionality of any kind.** **Out of scope.**
- **Backtesting caveats**: `scripts/backtest.py` reconstructs each
  player's inputs from only pre-gameweek `element-summary` history to
  avoid the most obvious lookahead bias (using a gameweek's own result to
  predict it), and uses that gameweek's own price where available.
  However, it still uses **current-day** team strength ratings and FDR
  (the FPL API doesn't expose historical values), and does not simulate
  FPL's automatic substitutions — if the recommended captain didn't play,
  the captain bonus falls back to the vice-captain (matching real FPL
  behavior), but a blank/injured non-captain starter is not auto-subbed
  in from the bench. Treat backtest results as a rough sanity check, not
  a rigorous historical accuracy measurement.
- **Double/blank gameweeks** are handled (see above), but the model
  doesn't attempt to project further than the target gameweek, so it
  can't help decide whether to bank a double-gameweek player for a future
  week.
