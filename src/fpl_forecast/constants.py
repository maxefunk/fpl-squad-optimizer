"""Shared constants: API endpoints, squad rules, and scoring-model tunables."""

BASE_URL = "https://fantasy.premierleague.com/api"

# element_type -> position code
POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
POSITION_ORDER = ["GK", "DEF", "MID", "FWD"]

# 15-man squad composition (FPL rules)
SQUAD_COMPOSITION = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}

# Valid starting-XI ranges (GK is always exactly 1)
XI_MIN = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}
XI_SIZE = 11

DEFAULT_BUDGET = 100.0
MAX_PER_CLUB = 3

# Secondary objective weight rewarding outfield bench quality (GK excluded --
# a backup keeper almost never plays, so cheapest-possible is correct real
# strategy there). Without this, the optimizer has zero preference among
# bench slots since they don't count toward the objective at all, and tends
# to fill them with the cheapest fodder regardless of playing chance. Kept
# small so it only breaks ties/nudges among otherwise-equal choices -- it
# must never be large enough to trade away starting-XI quality for a
# better bench (see the regression test asserting XI xpts is unaffected).
BENCH_QUALITY_WEIGHT = 0.05

# FPL points values
GOAL_POINTS = {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4}
ASSIST_POINTS = 3
CLEAN_SHEET_POINTS = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}
APPEARANCE_POINTS_FULL = 2  # points for playing 60+ minutes

# Simplified Poisson clean-sheet / goal model calibration.
# Rough Premier League long-run average goals scored per team per match.
LEAGUE_AVG_GOALS_PER_TEAM = 1.35

# xPts blend weights: how much weight each component gets in the final score.
# Must sum to 1.0.
WEIGHT_MODEL_COMPONENT = 0.45   # fixture-adjusted statistical model (this GW)
WEIGHT_FORM_COMPONENT = 0.35    # recency-weighted actual points, last N GWs
WEIGHT_SEASON_COMPONENT = 0.20  # season-long points-per-game prior

FORM_LOOKBACK_GWS = 6
# Most-recent-first recency weights for the form component (must have
# FORM_LOOKBACK_GWS entries; extra weight is renormalised if fewer GWs exist).
FORM_RECENCY_WEIGHTS = [0.32, 0.24, 0.18, 0.13, 0.08, 0.05]

# Set-piece bonus nudges (see scoring.py) applied to MID/FWD only.
PENALTY_TAKER_BONUS = 0.35
SET_PIECE_TAKER_BONUS = 0.15

# A player with < this many minutes played this season is treated as having
# no reliable season/per-90 signal (bench fodder / fringe player).
MIN_SEASON_MINUTES_FOR_SIGNAL = 90

# Minutes threshold in a single match to count as a "start" for the
# rotation-risk / nailed-on heuristic.
START_MINUTES_THRESHOLD = 60

# -- Small-sample confidence shrinkage --------------------------------------
# A player with only a couple of great cameos shouldn't get the same trust
# in their points-per-game as one with a near-full season behind it. Below
# MIN_MINUTES_FOR_FULL_CONFIDENCE minutes, season/no-history-form components
# are blended toward NEUTRAL_PPG_PRIOR in proportion to how little data
# backs them (confidence = minutes / MIN_MINUTES_FOR_FULL_CONFIDENCE).
MIN_MINUTES_FOR_FULL_CONFIDENCE = 900  # ~10 full matches
NEUTRAL_PPG_PRIOR = 2.0  # a roughly average points-per-game across all outfield players

# When current-season minutes are 0 (typically gameweek 1), confidence in
# season/form/attacking-rate numbers instead comes from last season's
# minutes (history_past), discounted further since a full season has
# passed -- transfers, injuries, and aging can all have changed things.
PAST_SEASON_CONFIDENCE_DISCOUNT = 0.85

# -- Past-season availability fallback --------------------------------------
# When a player has no current-season per-GW history yet (most notably
# gameweek 1 of a new season), fall back to how much they played in the
# most recent completed season (element-summary "history_past") as a proxy
# for "are they actually in the matchday XI most weeks". Thresholds are in
# minutes-equivalent full matches (minutes / 90).
PAST_SEASON_GAMES_NAILED = 25  # played the bulk of a season -> likely a starter
PAST_SEASON_GAMES_ROTATION = 12  # regular squad player, some rotation
PAST_SEASON_GAMES_FRINGE = 3  # occasional cameos only

AVAILABILITY_PAST_SEASON_NAILED = 0.80
AVAILABILITY_PAST_SEASON_ROTATION = 0.55
AVAILABILITY_PAST_SEASON_FRINGE = 0.30
AVAILABILITY_NO_DATA = 0.15  # no current- or past-season signal at all (new to the PL, academy graduate, etc.)

# -- Ownership as a credibility cap on availability --------------------------
# For goalkeepers especially, clean-sheet probability (the biggest driver of
# their score) is team-level and identical whether the starter or backup
# plays -- our minutes-based squad-role signal is the *only* thing telling
# them apart, and it can be stale or simply wrong (e.g. history_past minutes
# from a season where the "backup" covered an injury). Thousands of FPL
# managers price in "will this player actually start" in near-real-time via
# selected_by_percent, which is a faster and often more reliable check than
# our own minutes heuristics. Below OWNERSHIP_CAP_THRESHOLD% ownership, the
# squad-role factor is capped (never boosted -- this can only pull an
# estimate down) on a ramp down to OWNERSHIP_CAP_FLOOR at 0% owned. A player
# missing ownership data entirely is not capped (see scoring.py) -- no
# signal shouldn't override a real one.
OWNERSHIP_CAP_THRESHOLD = 2.0  # % owned; at or above this, no cap is applied
OWNERSHIP_CAP_FLOOR = 0.15

# Fixture-ticker window shown in the HTML report.
FIXTURE_TICKER_GWS = 5

# -- Near-term fixture-run factor --------------------------------------------
# A player who looks great for the target gameweek but faces a brutal run
# immediately afterward is a worse pick than the raw single-GW xPts implies,
# since swapping them out again costs a transfer (or a hit). This nudges the
# model component up/down based on the average FDR of the
# FIXTURE_RUN_LOOKAHEAD_GWS gameweeks *after* the target one (the target GW
# itself is already scored via the normal fixture-adjusted model).
FIXTURE_RUN_LOOKAHEAD_GWS = 3
FIXTURE_RUN_WEIGHT = 0.03  # multiplier change per FDR point away from neutral (3)
FIXTURE_RUN_MULTIPLIER_MIN = 0.85
FIXTURE_RUN_MULTIPLIER_MAX = 1.15

# -- Transfers (week 2+) -----------------------------------------------------
DEFAULT_FREE_TRANSFERS = 1
TRANSFER_HIT_COST = 4.0  # points deducted per transfer beyond the free ones
# How many free transfers can bank up if unused (classic FPL rule). Some
# recent seasons allow banking more (up to 5) -- kept simple/configurable
# here rather than tracking season-specific rule changes.
FREE_TRANSFER_CAP = 2
