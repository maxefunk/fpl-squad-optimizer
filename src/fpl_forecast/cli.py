"""CLI entrypoint: python -m fpl_forecast <subcommand> ...

Subcommands:
  recommend    Recommend a squad for a gameweek from scratch (default; this
               is what earlier versions of this tool did with no subcommand)
  save-team    Save a freshly-recommended squad as your tracked team
  record       Record a completed gameweek's actual points against your team
  transfers    Suggest transfers for your tracked team ahead of a gameweek
  status       Show your tracked team's accumulated points and current squad
"""

from __future__ import annotations

import argparse
import sys

import requests

from fpl_forecast.api_client import FPLClient
from fpl_forecast.constants import (
    DEFAULT_BUDGET,
    DEFAULT_FREE_TRANSFERS,
    FIXTURE_TICKER_GWS,
    FREE_TRANSFER_CAP,
    MAX_PER_CLUB,
    TRANSFER_HIT_COST,
)
from fpl_forecast.formatting import format_squad_result
from fpl_forecast.formatting_html import write_html
from fpl_forecast.optimizer import InfeasibleError, optimize_squad, optimize_transfers
from fpl_forecast.scoring import build_fixture_ticker, score_all_players
from fpl_forecast.team_state import (
    current_squad_value,
    load_team,
    record_gameweek,
    save_team,
    team_state_from_squad_result,
)

DEFAULT_TEAM_FILE = "my_team.json"


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


def score_gameweek(gameweek: int | None, cache_dir: str, force_refresh: bool = False):
    """Fetch and score every eligible player for a gameweek.

    Shared by every subcommand that needs the scored player pool: recommend/
    save-team feed it into optimize_squad, transfers feeds it into
    optimize_transfers.
    """
    client = FPLClient(cache_dir=cache_dir)
    bootstrap = client.get_bootstrap_static(force_refresh=force_refresh)

    gw = gameweek or resolve_default_gameweek(bootstrap["events"])

    # Fetch the full-season fixture list once: it covers both the target
    # gameweek's fixtures (for scoring) and the upcoming-fixtures ticker
    # shown in the HTML report, with no extra API calls.
    all_fixtures = client.get_fixtures(force_refresh=force_refresh)
    gw_fixtures = [f for f in all_fixtures if f.get("event") == gw]

    try:
        set_piece_notes = client.get_set_piece_notes(force_refresh=force_refresh)
    except requests.RequestException:
        set_piece_notes = None

    players = [p for p in bootstrap["elements"] if p.get("status") != "u"]

    # Only fetch per-GW history for players with some minutes this season;
    # players with none have no useful history to fetch anyway.
    candidate_ids = [p["id"] for p in players if float(p.get("minutes") or 0) > 0]
    element_summaries = client.get_element_summaries_bulk(candidate_ids, force_refresh=force_refresh)

    ticker = build_fixture_ticker(bootstrap["teams"], all_fixtures, gw, num_gws=FIXTURE_TICKER_GWS)
    scores = score_all_players(
        players,
        bootstrap["teams"],
        gw_fixtures,
        element_summaries,
        set_piece_notes,
        gameweek=gw,
        fixture_ticker=ticker,
    )
    teams_lookup = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    return scores, gw, ticker, gw_fixtures, teams_lookup


def build_forecast(
    gameweek: int | None,
    budget: float,
    max_per_club: int,
    cache_dir: str,
    force_refresh: bool = False,
):
    scores, gw, ticker, gw_fixtures, teams_lookup = score_gameweek(gameweek, cache_dir, force_refresh)
    result = optimize_squad(scores, budget=budget, max_per_club=max_per_club)
    return result, gw, scores, ticker, gw_fixtures, teams_lookup


def _add_common_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cache-dir", type=str, default="data/cache", help="Directory for cached API responses")
    parser.add_argument("--refresh", action="store_true", help="Bypass cache and refetch all data from the FPL API")


def _add_team_file_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--team-file", type=str, default=DEFAULT_TEAM_FILE, help=f"Path to your tracked team (default: {DEFAULT_TEAM_FILE})"
    )


def cmd_recommend(args: argparse.Namespace) -> int:
    try:
        result, gw, scores, ticker, gw_fixtures, teams_lookup = build_forecast(
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
        write_html(
            result,
            gw,
            args.html,
            all_scores=scores,
            fixture_ticker=ticker,
            gw_fixtures=gw_fixtures,
            teams_lookup=teams_lookup,
        )
        print(f"\nHTML report written to {args.html}")

    return 0


def cmd_save_team(args: argparse.Namespace) -> int:
    try:
        result, gw, *_ = build_forecast(
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

    state = team_state_from_squad_result(result, gameweek=gw, free_transfers=args.free_transfers)
    save_team(args.team_file, state)

    print(format_squad_result(result, gw))
    print(f"\nSaved as your tracked team (gameweek {gw}) to {args.team_file}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    state = load_team(args.team_file)
    if state is None:
        print(f"No saved team found at {args.team_file}. Run `save-team` first.", file=sys.stderr)
        return 1

    client = FPLClient(cache_dir=args.cache_dir)
    try:
        live = client.get_live_event(args.gameweek, force_refresh=args.refresh)
    except requests.RequestException as exc:
        print(f"FPL API request failed: {exc}", file=sys.stderr)
        return 1

    actual_points = {e["id"]: e["stats"]["total_points"] for e in live["elements"]}
    actual_minutes = {e["id"]: e["stats"]["minutes"] for e in live["elements"]}

    try:
        record_gameweek(state, args.gameweek, actual_points, actual_minutes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    save_team(args.team_file, state)

    record = state.history[-1]
    captain_name = next((p.web_name for p in state.squad if p.element_id == record.captain_id), "?")
    print(f"Gameweek {args.gameweek}: {record.points} points (captain: {captain_name})")
    print(f"Total points so far: {state.total_points}")
    return 0


def cmd_transfers(args: argparse.Namespace) -> int:
    state = load_team(args.team_file)
    if state is None:
        print(f"No saved team found at {args.team_file}. Run `save-team` first.", file=sys.stderr)
        return 1

    try:
        scores, gw, ticker, gw_fixtures, teams_lookup = score_gameweek(args.gameweek, args.cache_dir, args.refresh)
    except requests.RequestException as exc:
        print(f"FPL API request failed: {exc}", file=sys.stderr)
        return 1

    live_prices = {p.element_id: p.now_cost for p in scores}
    squad_value = current_squad_value(state, live_prices)
    budget = round(squad_value + state.bank, 1)
    free_transfers = args.free_transfers if args.free_transfers is not None else state.free_transfers
    hit_cost = args.hit_cost if args.hit_cost is not None else TRANSFER_HIT_COST

    try:
        tr = optimize_transfers(
            scores,
            state.squad_ids,
            free_transfers=free_transfers,
            budget=budget,
            max_per_club=args.max_per_club,
            hit_cost=hit_cost,
            max_transfers=args.max_transfers,
        )
    except InfeasibleError as exc:
        print(f"Could not find a feasible transfer set: {exc}", file=sys.stderr)
        return 1

    print(f"\n=== Transfer suggestion — Gameweek {gw} ===\n")
    print(f"Squad value: £{squad_value:.1f}m  |  Bank: £{state.bank:.1f}m  |  Free transfers: {free_transfers}")

    if tr.transfers_made == 0:
        print("\nNo beneficial transfers found — your current squad is already the best use of this budget.")
    else:
        print(f"\n{tr.transfers_made} transfer(s) suggested ({tr.hits} hit{'s' if tr.hits != 1 else ''}, -{tr.hit_points:.0f} pts):")
        for out_p, in_p in zip(
            sorted(tr.transfers_out, key=lambda p: p.position),
            sorted(tr.transfers_in, key=lambda p: p.position),
        ):
            print(f"  OUT: {out_p.web_name:<18} {out_p.position:<3} £{out_p.now_cost:.1f}m  {out_p.xpts:.2f} pts")
            print(f"  IN:  {in_p.web_name:<18} {in_p.position:<3} £{in_p.now_cost:.1f}m  {in_p.xpts:.2f} pts")

    print(f"\nNew squad's projected starting-XI points: {tr.result.total_xi_xpts:.2f}")

    try:
        baseline = optimize_transfers(
            scores, state.squad_ids, free_transfers=free_transfers, budget=budget,
            max_per_club=args.max_per_club, hit_cost=hit_cost, max_transfers=0,
        )
        print(f"(No changes would project: {baseline.result.total_xi_xpts:.2f})")
    except InfeasibleError:
        pass  # current squad no longer affordable/valid at today's prices; skip the comparison

    print(f"\nCaptain:      {tr.result.captain.web_name} ({tr.result.captain.xpts:.2f} proj. pts)")
    print(f"Vice-Captain: {tr.result.vice_captain.web_name} ({tr.result.vice_captain.xpts:.2f} proj. pts)")

    if args.apply:
        new_free_transfers = min(FREE_TRANSFER_CAP, max(0, free_transfers - tr.transfers_made) + 1)
        new_state = team_state_from_squad_result(
            tr.result, gameweek=gw, free_transfers=new_free_transfers, bank=tr.bank_remaining
        )
        new_state.total_points = state.total_points
        new_state.history = state.history
        new_state.last_gameweek_recorded = state.last_gameweek_recorded
        save_team(args.team_file, new_state)
        print(f"\nApplied: {args.team_file} updated to this new squad for gameweek {gw}.")
    else:
        print("\n(Dry run -- pass --apply to update your tracked team to this squad.)")

    if args.html:
        write_html(
            tr.result,
            gw,
            args.html,
            all_scores=scores,
            fixture_ticker=ticker,
            gw_fixtures=gw_fixtures,
            teams_lookup=teams_lookup,
        )
        print(f"\nHTML report written to {args.html}")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = load_team(args.team_file)
    if state is None:
        print(f"No saved team found at {args.team_file}. Run `save-team` first.", file=sys.stderr)
        return 1

    print(f"Total points: {state.total_points}")
    print(f"Free transfers available: {state.free_transfers}")
    print(f"Bank: £{state.bank:.1f}m")
    print(f"Last recorded gameweek: {state.last_gameweek_recorded or 'none'}")

    if state.history:
        print("\nGameweek history:")
        for h in state.history:
            print(f"  GW{h.gameweek}: {h.points} pts (transfers: {h.transfers_made}, hits: {h.hits})")

    print("\nCurrent squad:")
    for p in sorted(state.squad, key=lambda x: (x.position, -x.now_cost)):
        tag = " (C)" if p.element_id == state.captain_id else (" (VC)" if p.element_id == state.vice_captain_id else "")
        bench_tag = "" if p.is_starting else " [bench]"
        print(f"  {p.web_name:<18} {p.position:<3} {p.team_short:<4} £{p.now_cost:>4.1f}m{tag}{bench_tag}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fpl_forecast",
        description="Recommend an optimal FPL squad, starting XI, and captaincy for a gameweek.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_recommend = subparsers.add_parser("recommend", help="Recommend a squad for a gameweek from scratch")
    p_recommend.add_argument("--gameweek", "-g", type=int, default=None, help="Target gameweek (default: next unplayed GW)")
    p_recommend.add_argument("--budget", type=float, default=DEFAULT_BUDGET, help="Squad budget in £m (default: 100.0)")
    p_recommend.add_argument("--max-per-club", type=int, default=MAX_PER_CLUB, help="Max players from one club (default: 3)")
    p_recommend.add_argument("--html", type=str, default=None, metavar="PATH", help="Also write a standalone HTML report to this path")
    _add_common_data_args(p_recommend)
    p_recommend.set_defaults(func=cmd_recommend)

    p_save = subparsers.add_parser("save-team", help="Recommend a squad and save it as your tracked team")
    p_save.add_argument("--gameweek", "-g", type=int, required=True, help="Gameweek this squad is for")
    p_save.add_argument("--budget", type=float, default=DEFAULT_BUDGET, help="Squad budget in £m (default: 100.0)")
    p_save.add_argument("--max-per-club", type=int, default=MAX_PER_CLUB, help="Max players from one club (default: 3)")
    p_save.add_argument("--free-transfers", type=int, default=DEFAULT_FREE_TRANSFERS, help="Free transfers to start with (default: 1)")
    _add_team_file_arg(p_save)
    _add_common_data_args(p_save)
    p_save.set_defaults(func=cmd_save_team)

    p_record = subparsers.add_parser("record", help="Record a completed gameweek's actual points")
    p_record.add_argument("--gameweek", "-g", type=int, required=True, help="Completed gameweek to record")
    _add_team_file_arg(p_record)
    _add_common_data_args(p_record)
    p_record.set_defaults(func=cmd_record)

    p_transfers = subparsers.add_parser("transfers", help="Suggest transfers for your tracked team")
    p_transfers.add_argument("--gameweek", "-g", type=int, default=None, help="Target gameweek (default: next unplayed GW)")
    p_transfers.add_argument("--free-transfers", type=int, default=None, help="Override free transfers available (default: from tracked team)")
    p_transfers.add_argument("--max-transfers", type=int, default=None, help="Cap the number of transfers considered")
    p_transfers.add_argument("--hit-cost", type=float, default=None, help=f"Points per transfer beyond free ones (default: {TRANSFER_HIT_COST})")
    p_transfers.add_argument("--max-per-club", type=int, default=MAX_PER_CLUB, help="Max players from one club (default: 3)")
    p_transfers.add_argument("--apply", action="store_true", help="Update your tracked team to the suggested squad (default: dry run)")
    p_transfers.add_argument("--html", type=str, default=None, metavar="PATH", help="Also write a standalone HTML report for the new squad")
    _add_team_file_arg(p_transfers)
    _add_common_data_args(p_transfers)
    p_transfers.set_defaults(func=cmd_transfers)

    p_status = subparsers.add_parser("status", help="Show your tracked team's points and current squad")
    _add_team_file_arg(p_status)
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
