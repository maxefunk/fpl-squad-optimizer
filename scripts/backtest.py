"""Backtest the model against a completed gameweek.

Reconstructs each player's inputs using only data available *before* the
target gameweek (per-round history from element-summary, filtered to
round < gameweek), scores and optimizes a squad as if predicting that GW
in advance, then compares the recommended starting XI's actual points
(from event/{gw}/live/) against the actual dream-team benchmark
(dream-team/{gw}/).

Usage:
    python scripts/backtest.py --gameweek 4

See the README "Backtesting" section for the caveats this simplified
point-in-time reconstruction carries (team strength ratings and player
prices are current-day, not historical; no autosub simulation beyond the
captain->vice-captain blank fallback).
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fpl_forecast.api_client import FPLClient  # noqa: E402
from fpl_forecast.constants import DEFAULT_BUDGET, MAX_PER_CLUB  # noqa: E402
from fpl_forecast.optimizer import InfeasibleError, optimize_squad  # noqa: E402
from fpl_forecast.scoring import score_all_players  # noqa: E402


def _to_float(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_point_in_time_dataset(client: FPLClient, gameweek: int, players_meta: list[dict]):
    """Reconstruct player dicts + history using only pre-gameweek data.

    Returns (players, element_summaries) suitable for score_all_players,
    restricted to players with enough prior data to score meaningfully.
    """
    candidate_ids = [p["id"] for p in players_meta if _to_float(p.get("minutes")) > 0]
    summaries = client.get_element_summaries_bulk(candidate_ids)

    players_pit = []
    summaries_pit = {}

    for player in players_meta:
        summary = summaries.get(player["id"])
        pre_gw_history = []
        used_past_season = False

        if summary:
            pre_gw_history = [h for h in summary.get("history", []) if h["round"] < gameweek]

        if not pre_gw_history:
            # No in-season data yet (e.g. gameweek 1, or a summary fetch
            # miss) -- fall back to the most recent past season's totals,
            # if any, as a weak prior. Otherwise skip: there's no signal.
            past = (summary or {}).get("history_past", [])
            if not past:
                continue
            used_past_season = True
            last_season = past[-1]
            total_minutes = _to_float(last_season.get("minutes"))
            total_goals = _to_float(last_season.get("goals_scored"))
            total_assists = _to_float(last_season.get("assists"))
            total_saves = _to_float(last_season.get("saves"))
            total_points = _to_float(last_season.get("total_points"))
            games = max(1, round(total_minutes / 90)) if total_minutes else 1
            points_per_game = total_points / games
            price = player["now_cost"]
        else:
            total_minutes = sum(_to_float(h.get("minutes")) for h in pre_gw_history)
            total_goals = sum(_to_float(h.get("goals_scored")) for h in pre_gw_history)
            total_assists = sum(_to_float(h.get("assists")) for h in pre_gw_history)
            total_saves = sum(_to_float(h.get("saves")) for h in pre_gw_history)
            points_per_game = statistics.mean(h["total_points"] for h in pre_gw_history)
            last_row = max(pre_gw_history, key=lambda h: h["round"])
            price = last_row.get("value", player["now_cost"])

        total_xg = sum(_to_float(h.get("expected_goals")) for h in pre_gw_history) if pre_gw_history else 0.0
        total_xa = sum(_to_float(h.get("expected_assists")) for h in pre_gw_history) if pre_gw_history else 0.0
        xg90 = (total_xg / total_minutes * 90.0) if total_minutes > 0 else 0.0
        xa90 = (total_xa / total_minutes * 90.0) if total_minutes > 0 else 0.0

        pit_player = dict(player)
        pit_player.update(
            {
                "minutes": total_minutes,
                "goals_scored": total_goals,
                "assists": total_assists,
                "saves": total_saves,
                "expected_goals_per_90": xg90,
                "expected_assists_per_90": xa90,
                "points_per_game": points_per_game,
                "now_cost": price,
                "chance_of_playing_next_round": None,
                "status": "a",
            }
        )
        players_pit.append(pit_player)
        summaries_pit[player["id"]] = {"history": [] if used_past_season else pre_gw_history}

    return players_pit, summaries_pit


def run_backtest(gameweek: int, budget: float, max_per_club: int, cache_dir: str) -> None:
    client = FPLClient(cache_dir=cache_dir)
    bootstrap = client.get_bootstrap_static()

    try:
        event_status = client.get_event_status()
        finalized = any(
            s.get("event") == gameweek and s.get("bonus_added") for s in event_status.get("status", [])
        )
        if not finalized:
            print(
                f"WARNING: event-status does not show gameweek {gameweek} as finalized "
                "(bonus points may not be settled). Results below could still change.\n"
            )
    except Exception:
        pass  # event-status is a best-effort sanity check, not required

    players_meta = [p for p in bootstrap["elements"] if p.get("status") != "u"]
    fixtures = client.get_fixtures(event=gameweek)
    try:
        set_piece_notes = client.get_set_piece_notes()
    except Exception:
        set_piece_notes = None

    players_pit, summaries_pit = build_point_in_time_dataset(client, gameweek, players_meta)
    if not players_pit:
        print(f"No players had usable pre-gameweek-{gameweek} history; cannot backtest.", file=sys.stderr)
        sys.exit(1)

    scores = score_all_players(players_pit, bootstrap["teams"], fixtures, summaries_pit, set_piece_notes)

    try:
        # force_include_most_owned=False: backtesting is meant to sanity-check
        # and tune the model's own scoring weights against real outcomes --
        # forcing in the crowd's #1 pick regardless of the model's confidence
        # would evaluate a hybrid of "model + crowd override" instead of the
        # model's own judgment, muddying that signal.
        result = optimize_squad(scores, budget=budget, max_per_club=max_per_club, force_include_most_owned=False)
    except InfeasibleError as exc:
        print(f"Could not build a backtest squad: {exc}", file=sys.stderr)
        sys.exit(1)

    live = client.get_live_event(gameweek)
    actual_points = {e["id"]: e["stats"]["total_points"] for e in live["elements"]}
    actual_minutes = {e["id"]: e["stats"]["minutes"] for e in live["elements"]}

    dream_team = client.get_dream_team(gameweek)
    benchmark_total = sum(entry["points"] for entry in dream_team["team"])

    raw_xi_total = sum(actual_points.get(p.element_id, 0) for p in result.starting_xi)

    captain_minutes = actual_minutes.get(result.captain.element_id, 0)
    captain_for_double = result.captain if captain_minutes > 0 else result.vice_captain
    captain_bonus = actual_points.get(captain_for_double.element_id, 0)
    captained_total = raw_xi_total + captain_bonus

    print(f"\n=== Backtest: Gameweek {gameweek} ===\n")
    print("Recommended starting XI (projected points -> actual points):")
    for p in result.starting_xi:
        actual = actual_points.get(p.element_id, "N/A")
        tag = " (C)" if p.element_id == result.captain.element_id else (
            " (VC)" if p.element_id == result.vice_captain.element_id else ""
        )
        print(f"  {p.web_name:<18} {p.position:<3} predicted {p.xpts:>5.2f}  actual {actual}{tag}")

    print(f"\nRecommended XI actual points (no captain bonus): {raw_xi_total}")
    print(f"Recommended XI actual points (with captain x2):  {captained_total}")
    print(f"Dream Team (actual best XI) benchmark total:      {benchmark_total}")
    print(f"Gap vs. benchmark (captained): {captained_total - benchmark_total:+d}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the FPL forecasting model against a completed gameweek.")
    parser.add_argument("--gameweek", "-g", type=int, required=True, help="A completed gameweek to backtest against")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET)
    parser.add_argument("--max-per-club", type=int, default=MAX_PER_CLUB)
    parser.add_argument("--cache-dir", type=str, default="data/cache")
    args = parser.parse_args(argv)

    run_backtest(args.gameweek, args.budget, args.max_per_club, args.cache_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
