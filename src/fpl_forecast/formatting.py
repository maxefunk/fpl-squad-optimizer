"""Human-readable CLI output for a SquadResult."""

from __future__ import annotations

from fpl_forecast.constants import POSITION_ORDER
from fpl_forecast.models import PlayerScore, SquadResult


def _player_line(p: PlayerScore, tag: str = "") -> str:
    suffix = f" {tag}" if tag else ""
    return f"  {p.web_name:<18} {p.position:<3} {p.team_short:<4} £{p.now_cost:>4.1f}m  xPts {p.xpts:>5.2f}{suffix}"


def format_squad_result(result: SquadResult, gameweek: int) -> str:
    lines = []
    lines.append(f"\n=== FPL Squad Recommendation — Gameweek {gameweek} ===\n")

    lines.append(f"Formation: {result.formation}  |  Budget used: £{result.total_cost:.1f}m / £{result.budget:.1f}m")
    lines.append(f"Projected starting-XI xPts: {result.total_xi_xpts:.2f}\n")

    lines.append(f"Captain:      {result.captain.web_name} ({result.captain.xpts:.2f} xPts)")
    lines.append(f"Vice-Captain: {result.vice_captain.web_name} ({result.vice_captain.xpts:.2f} xPts)\n")

    lines.append("Starting XI:")
    for pos in POSITION_ORDER:
        for p in result.starting_xi:
            if p.position != pos:
                continue
            tag = ""
            if p.element_id == result.captain.element_id:
                tag = "(C)"
            elif p.element_id == result.vice_captain.element_id:
                tag = "(VC)"
            lines.append(_player_line(p, tag))

    lines.append("\nBench (sub order):")
    for i, p in enumerate(result.bench, start=1):
        lines.append(_player_line(p, f"[{i}]"))

    lines.append("\nFull squad cost check: {} players, £{:.1f}m".format(len(result.squad), result.total_cost))

    lines.append("\nTop picks — reasoning:")
    top_picks = sorted(result.starting_xi, key=lambda p: p.xpts, reverse=True)[:5]
    for p in top_picks:
        lines.append(f"\n  {p.web_name} ({p.position}, {p.team_short}) — {p.xpts:.2f} xPts")
        for reason in p.reasons:
            lines.append(f"    - {reason}")

    return "\n".join(lines)
