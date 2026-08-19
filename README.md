# fpl-squad-optimizer

A Fantasy Premier League (FPL) team forecasting tool. Given a target
gameweek, it recommends the 15-man squad, starting XI, formation,
captain, and vice-captain that maximize projected points — subject to the
real FPL budget and squad-composition rules — using live data from the
official FPL API.

```
$ python -m fpl_forecast recommend --gameweek 5

=== FPL Squad Recommendation — Gameweek 5 ===

Formation: 3-4-3  |  Budget used: £99.5m / £100.0m
Projected starting-XI points: 61.42

Captain:      M.Salah (9.87 proj. pts)
Vice-Captain: Haaland (9.41 proj. pts)
...
```

Beyond the one-off recommendation, it can also track a squad you actually
own week to week — accumulated points and transfer suggestions — see
[Team tracking & transfers](#team-tracking--transfers).

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

The CLI is split into subcommands. `recommend` is the one-off "build me a
squad from scratch" mode from the original spec; the rest (`save-team`,
`record`, `transfers`, `status`) track a squad you actually own across
gameweeks — see [Team tracking & transfers](#team-tracking--transfers).

```bash
# Recommend a squad for a specific gameweek
python -m fpl_forecast recommend --gameweek 5

# Default: targets the next unplayed gameweek
python -m fpl_forecast recommend

# Configurable budget / per-club limit
python -m fpl_forecast recommend --gameweek 5 --budget 95.5 --max-per-club 2

# Bypass the local cache and refetch everything from the FPL API
python -m fpl_forecast recommend --gameweek 5 --refresh

# Also write a standalone HTML pitch-view report
python -m fpl_forecast recommend --gameweek 5 --html squad.html
```

`--html` writes a self-contained report (inline CSS, no external
requests) with:

- the starting XI laid out on a pitch by position, captain/vice-captain
  badges, a budget bar, and the bench in sub order;
- availability% and "rotation risk" / "limited data" flags on every card;
- this gameweek's full fixture list (every match, not just squad clubs);
- each squad club's next 5 gameweeks of fixtures, colour-coded by FDR, plus
  a full 20-team version of the same for spotting a transfer target's run;
- a "who else was in the mix" table per position (top 8 by projected
  points, with the squad's actual picks highlighted, including each
  player's ownership%) so you can see the alternatives, not just the
  final XI;
- a separate "most selected players" table (top 8 by ownership% per
  position, from the FPL API's `selected_by_percent`) alongside our own
  projection for the same players, so you can see where this tool agrees
  or disagrees with the crowd -- entirely independent of its own picks;
- the reasoning behind the top picks, plus a score-breakdown bar chart
  showing how each top pick's model/form/season components stack up;
- a glossary explaining the terminology (projected points, FDR,
  availability%, clean sheet %, ownership%, fixture run, etc.).

Open the file directly in a browser.

API responses are cached to `data/cache/` (gitignored) so repeat runs are
fast and don't hammer the FPL API. `--refresh` forces a live refetch.

### Team tracking & transfers

`recommend` builds a squad from scratch every time. From gameweek 2 onward
you'll usually want the tool to track the squad you actually own instead,
and suggest transfers against it rather than starting over. This is a
small local JSON file (default `my_team.json`, gitignored) plus four
subcommands:

```bash
# Week 1: build and save your starting squad
python -m fpl_forecast save-team --gameweek 1

# After GW1 is played: record its actual points
python -m fpl_forecast record --gameweek 1

# Ahead of GW2: see suggested transfers (dry run -- nothing is saved yet)
python -m fpl_forecast transfers --gameweek 2

# Happy with the suggestion? Apply it (updates my_team.json to the new squad)
python -m fpl_forecast transfers --gameweek 2 --apply

# After GW2 is played: record it, and repeat from `transfers` each week
python -m fpl_forecast record --gameweek 2

# Check accumulated points, free transfers, bank, and current squad any time
python -m fpl_forecast status
```

`transfers` solves one MILP (`optimize_transfers` in
[`src/fpl_forecast/optimizer.py`](src/fpl_forecast/optimizer.py)) that
picks the number of transfers itself rather than being told how many to
make: `transfers_made` is expressed as `15 - (owned players kept)`, and a
`hits` variable lower-bounded by `transfers_made - free_transfers` is
subtracted from the objective at 4 points each. Since the solver
maximizes, `hits` settles exactly at `max(0, transfers_made -
free_transfers)` at the optimum -- so 0, 1, 2+ transfers are all
considered in the same solve and weighed against their real points cost,
not chosen by a separate heuristic. Free transfers roll over up to a cap
of 2 if unused (the classic FPL rule; some recent seasons allow banking
up to 5 -- see `FREE_TRANSFER_CAP` in `constants.py` if your league uses
a different cap). `--max-transfers` caps how many transfers are considered
at all; `--hit-cost` overrides the -4 assumption.

Only the starting XI's actual points count toward the tracked total
(matching real FPL scoring), plus the captain's points doubled, falling
back to the vice-captain if the captain didn't play. This does **not**
simulate FPL's automatic substitutions for a blank non-captain starter --
same simplification `scripts/backtest.py` makes. Transfer budget is
computed as the owned squad's value **at today's prices** plus your
banked money, which does not model FPL's actual sell-price rule (you
sell for less than the current price if it's risen a lot since you
bought, via a profit cap) -- see [Known limitations](#known-limitations).

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
| `bootstrap-static/` | Players, teams, prices, positions, season-to-date stats, ownership % (`selected_by_percent`), current gameweek |
| `fixtures/` | Full-season fixture list + FDR (`team_h_difficulty` / `team_a_difficulty`) -- fetched once and filtered locally for the target gameweek's scoring, the fixture-run factor, and the HTML report's fixture ticker/full gameweek list |
| `element-summary/{id}/` | Per-player gameweek-by-gameweek history (recency-weighted form) and past-season summaries |
| `team/set-piece-notes/` | Penalty/free-kick/corner-taker notes, folded in as a small MID/FWD bonus |
| `event/{gw}/live/` | Actual per-player stats for a completed gameweek (backtesting) |
| `dream-team/{gw}/` | The actual best-performing XI for a completed gameweek (backtest benchmark) |
| `event-status/` | Whether a gameweek's bonus points are finalized (backtest sanity check) |

### Scoring model (Proj. Pts)

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
availability probability** (see below), nudged by a small set-piece-duty
bonus (`+0.35` for a primary penalty taker, `+0.15` for a corner/free-kick
taker, MID/FWD only), and adjusted by a small **fixture-run factor** (see
below).

**Availability = fitness × squad role, multiplied together, not one
overriding the other.** An earlier version of this model treated the
API's `chance_of_playing_next_round` as authoritative whenever it was set
-- but that field only flags injury/fitness doubts, not squad-role
competition: FPL reports 100 there for every fully-fit player, *including
a fully-fit backup goalkeeper who rarely plays*. So it's now one of two
factors multiplied together:
- **Fitness factor**: `chance_of_playing_next_round / 100` if the API has
  set it, else 1.0 (no doubt flagged).
- **Squad-role factor**: is this player actually in the matchday XI most
  weeks, from actual minutes -- the fraction of the last 6 gameweeks
  they started (≥60 minutes), from `element-summary` history; or, **when
  there's no current-season history yet (most notably gameweek 1 of a new
  season)**, how many games-equivalent (`minutes / 90`) they played in the
  most recently completed season, from `element-summary`'s `history_past`
  — ≥25 games reads as nailed-on (0.80), ≥12 as a rotation-squad player
  (0.55), ≥3 as occasional cameos only (0.30), and less as unproven
  (0.15); or a conservative default from current season-long minutes if
  neither is available.

Multiplying the two means a fringe player who happens to be fully fit
still reads as unlikely to feature, and an injury-doubtful starter still
reads as clearly more likely to play than a fit bench option.

**Ownership caps a stale squad-role signal.** For goalkeepers especially,
clean-sheet probability (the biggest driver of their score) is team-level
and identical whether the starter or backup plays — the squad-role factor
above is the *only* thing telling them apart, and it can be wrong: a
backup keeper's `history_past` minutes can land in the "nailed" tier
(e.g. he covered an injury, or played a full season at a different club)
even though he's clearly not first-choice now. Below 2% ownership
(`selected_by_percent`), the squad-role factor is capped on a ramp down
to 0.15 at 0% owned — thousands of FPL managers price in "will this
player actually start" faster and more reliably than a minutes-based
heuristic can. The cap only ever pulls an estimate *down*, never up, and
a player with no ownership data at all isn't capped (no signal shouldn't
override a real one). This is a real trade-off: a genuinely nailed player
the crowd hasn't caught onto yet (a true differential) would also get
capped — but recommending a guaranteed-bench player as your starting
goalkeeper is the worse failure mode of the two.

**Small-sample confidence shrinkage applies to attacking threat too, not
just points-per-game/form.** A player with only a couple of big cameos
shouldn't get the same trust as one with a near-full season behind them —
otherwise a single hot 90 minutes (a big per-90 goals/assists rate from a
tiny sample) can make a fringe player's *model component* look like a
proven one, which is exactly what let one such player outscore established
players into the captaincy in early testing. Below ~900 minutes (10 full
matches) of whichever data backs the number (current season if available,
else last season's, discounted 15% for being a year old), points-per-game/
form are blended toward a neutral baseline (2.0 ppg) and **attacking
threat is shrunk multiplicatively toward 0** (there's no sensible neutral
prior for an unproven per-90 rate other than "don't assume they'll keep
producing at that clip"), in proportion to how little playing time backs
them (`confidence = minutes / 900`, capped at 1.0). The HTML report
surfaces this as a "limited data" flag below 30% confidence.

**When there's no current-season data at all (gameweek 1), attacking
threat falls back to last season's actual per-90 goals/assists rate**
(from `history_past`) rather than assuming 0 — without this, a proven
120-goals-a-season striker and a genuine fringe player would look equally
blank at the start of a season. This is what lets an established player
still project strongly at gameweek 1 despite having zero current-season
minutes, while a one-cameo fringe player does not.

**Fixture-run factor**: a small nudge (±15% max) to the model component
based on the average FDR of the ~3 gameweeks *after* the target one (from
the same fixture data used for the HTML report's ticker). A player who
looks great this week but faces a brutal run right after is worth
slightly less, since swapping them back out again soon costs a transfer
or a hit — a lightweight way of considering near-term fixture swings
without doing full multi-gameweek transfer planning (see
[Known limitations](#known-limitations)).

**Double/blank gameweeks**: a player's team may have 0, 1, or 2 fixtures
in the target gameweek. Blank-gameweek players score 0 projected points
(the optimizer will only pick them as cheap bench filler); double-gameweek
players sum their model component across both fixtures.

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
simultaneously, maximizing total starting-XI projected points (plus a
small secondary term, see below) subject to:

- Exactly 15 players: 2 GK, 5 DEF, 5 MID, 3 FWD
- Total squad cost ≤ budget (default £100.0m, configurable via `--budget`)
- At most 3 players from any one real-world club (`--max-per-club`)
- A valid starting XI: 1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD, 11 total, and every
  starter must be in the 15-man squad

Captain and vice-captain are **not** decision variables in the MILP — per
spec, they're simply the highest- and second-highest-projected-points
players in the optimizer's chosen starting XI, assigned after the solve.
Bench order is the 3 outfield bench players sorted by projected points
(highest first = first sub), then the bench goalkeeper last.

**Bench quality**: the objective is *only* starting-XI points, so on its
own the optimizer has zero preference among bench slots (they don't count
towards it at all) and will happily fill them with the cheapest possible
fodder — technically optimal, but not always a squad you'd want to own,
since if a starter blanks you'd rather have a credible backup than a
guaranteed zero. A small secondary term (`BENCH_QUALITY_WEIGHT = 0.05`,
outfield bench only — a backup goalkeeper genuinely should be as cheap as
possible, since it rarely plays regardless of quality) rewards outfield
bench points highly enough to prefer a same-priced credible option over
pure fodder, but far too small to ever trade away starting-XI quality for
it (see the regression test asserting the starting XI is byte-for-byte
identical with the weight on vs. off).

`optimize_transfers` (used by the `transfers` subcommand) is the same
MILP with a `hits` variable and a linear `transfers_made` expression
added — see [Team tracking & transfers](#team-tracking--transfers) above
for how that embeds the -4-per-hit tradeoff directly into the objective
instead of choosing a transfer count heuristically.

`optimize_transfers` (used by the `transfers` subcommand) is the same
MILP with a `hits` variable and a linear `transfers_made` expression
added — see [Team tracking & transfers](#team-tracking--transfers) above
for how that embeds the -4-per-hit tradeoff directly into the objective
instead of choosing a transfer count heuristically.

## Known limitations

- **The ownership credibility cap (see "Ownership caps a stale squad-role
  signal" above) can suppress a genuine low-owned differential**, not just
  the stale-minutes-signal case it's meant to catch — there's no way to
  tell the two apart from ownership% alone. Deliberately accepted: an
  over-cautious differential pick is a smaller failure than recommending
  a guaranteed-bench player as your starting XI, but it is a real cost.
- **Team strength ratings (and therefore FDR / clean-sheet estimates) can
  lag real squad changes.** `strength_attack_*`/`strength_defence_*` come
  straight from the FPL API, which derives them largely from recent
  results — they're slow to reflect a summer of transfer activity. A team
  that strengthened significantly (or weakened) in the close season will
  still look like last season's version of itself in the fixture
  difficulty and clean-sheet numbers until enough of the new season has
  been played to move the ratings. The HTML report now surfaces this
  caveat directly next to the fixture ticker rather than only in this
  README.
- **New signings and players new to the Premier League have essentially
  no usable signal.** The model leans on `element-summary` history
  (this season) and `history_past` (last completed PL season) for
  form/availability. A player arriving from abroad, or promoted from a
  lower league, has neither — they fall through to the lowest-confidence
  defaults (see "Small-sample confidence shrinkage" and the past-season
  availability tiers above) rather than being actively researched. This
  is a real, currently-unsolved gap: the free FPL API simply doesn't
  expose non-PL history.
- **Heuristic scoring, not a fitted model.** The scoring weights (45/35/20,
  the set-piece bonus sizes, the Poisson calibration constant, the fixture-
  run nudge) are reasonable starting points, not fit to historical
  accuracy. The `scripts/backtest.py` script is provided to sanity-check
  and tune them against completed gameweeks.
- **Set-piece-note parsing is a text heuristic.** `team/set-piece-notes/`
  returns free-text, not structured taker IDs; the name-matching parser
  can miss or misattribute duties if the source text's format changes or
  uses a different name form than `web_name`/`second_name`.
- **No live injury-news scraping.** Availability relies on the FPL API's
  own `chance_of_playing_next_round`/`status` fields and recent-minutes
  history, not press conferences or breaking news. **Flagged as a future
  improvement.**
- **Transfer suggestions are one gameweek at a time, not full multi-week
  planning.** `transfers` picks the best transfer set for a single target
  gameweek (weighing hits against that gameweek's points gain, with a
  small nudge for the following ~3 gameweeks' fixture difficulty — see
  "Fixture-run factor" above) — it does not plan several gameweeks ahead,
  simulate banking a free transfer for a future double gameweek, or model
  chips (wildcard, free hit, bench boost, triple captain). It also doesn't
  model FPL's actual sell-price rule (see [Team tracking &
  transfers](#team-tracking--transfers) above) or a player's transfer-out
  status if they're dropped from the API entirely (e.g. relegated/left the
  league) beyond simply excluding them from the pool. **Full multi-week
  chip-aware planning is out of scope for v1** but the underlying MILP
  (`optimize_transfers`) is the natural place to extend this.
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
