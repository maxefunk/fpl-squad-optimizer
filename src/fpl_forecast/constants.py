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
