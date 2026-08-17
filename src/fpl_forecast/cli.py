"""CLI entrypoint: python -m fpl_forecast --gameweek 5"""

from __future__ import annotations

import argparse
import sys

import requests

from fpl_forecast.api_client import FPLClient
from fpl_forecast.constants import DEFAULT_BUDGET, MAX_PER_CLUB
from fpl_forecast.formatting import format_squad_result
from fpl_forecast.formatting_html import write_html
from fpl_forecast.optimizer import InfeasibleError, optimize_squad
from fpl_forecast.scoring import score_all_players


def resolve_default_gameweek(events: list[dict]) -> int:
    for e in events:
        if e.get("is_next"):
            return e["id"]
    for e in events:
        if e.get("is_current"):
            return e["id"]
    # Fallback: first event that hasn't finished.
    for e in events:
        if not e.get("finished"):
            return e["id"]
    return events[-1]["id"]


def build_forecast(
    gameweek: int | None,
    budget: float,
    max_per_club: int,
    cache_dir: str,
    force_refresh: bool = False,
):
    client = FPLClient(cache_dir=cache_dir)
    bootstrap = client.get_bootstrap_static(force_refresh=force_refresh)

    gw = gameweek or resolve_default_gameweek(bootstrap["events"])
    fixtures = client.get_fixtures(event=gw, force_refresh=force_refresh)

    try:
        set_piece_notes = client.get_set_piece_notes(force_refresh=force_refresh)
    except requests.RequestException:
        set_piece_notes = None

    players = [p for p in bootstrap["elements"] if p.get("status") != "u"]

    # Only fetch per-GW history for players with some minutes this season;
    # players with none have no useful history to fetch anyway.
    candidate_ids = [p["id"] for p in players if float(p.get("minutes") or 0) > 0]
    element_summaries = client.get_element_summaries_bulk(candidate_ids, force_refresh=force_refresh)

    scores = score_all_players(players, bootstrap["teams"], fixtures, element_summaries, set_piece_notes)
    result = optimize_squad(scores, budget=budget, max_per_club=max_per_club)
    return result, gw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fpl_forecast",
        description="Recommend an optimal FPL squad, starting XI, and captaincy for a gameweek.",
    )
    parser.add_argument("--gameweek", "-g", type=int, default=None, help="Target gameweek (default: next unplayed GW)")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET, help="Squad budget in £m (default: 100.0)")
    parser.add_argument("--max-per-club", type=int, default=MAX_PER_CLUB, help="Max players from one club (default: 3)")
    parser.add_argument("--cache-dir", type=str, default="data/cache", help="Directory for cached API responses")
    parser.add_argument("--refresh", action="store_true", help="Bypass cache and refetch all data from the FPL API")
    parser.add_argument(
        "--html",
        type=str,
        default=None,
        metavar="PATH",
        help="Also write a standalone HTML pitch-view report to this path",
    )
    args = parser.parse_args(argv)

    try:
        result, gw = build_forecast(
            gameweek=args.gameweek,
            budget=args.budget,
            max_per_club=args.max_per_club,
            cache_dir=args.cache_dir,
            force_refresh=args.refresh,
        )
    except InfeasibleError as exc:
        print(f"Could not build a squad: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"FPL API request failed: {exc}", file=sys.stderr)
        return 1

    print(format_squad_result(result, gw))

    if args.html:
        write_html(result, gw, args.html)
        print(f"\nHTML report written to {args.html}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
