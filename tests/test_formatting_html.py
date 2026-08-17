from __future__ import annotations

from fpl_forecast.formatting_html import render_html
from fpl_forecast.optimizer import optimize_squad
from tests.conftest import make_player


def test_render_html_contains_key_squad_info(sample_pool):
    result = optimize_squad(sample_pool, budget=100.0, max_per_club=3)
    html = render_html(result, gameweek=7)

    assert html.startswith("<!doctype html>")
    assert "Gameweek 7" in html
    assert result.formation in html
    assert result.captain.web_name in html
    assert result.vice_captain.web_name in html
    assert f"£{result.total_cost:.1f}m" in html

    # Every squad member should appear somewhere in the report.
    for p in result.squad:
        assert p.web_name in html

    # Captain/vice badges are present exactly once each.
    assert html.count('class="badge badge-c"') == 1
    assert html.count('class="badge badge-vc"') == 1


def test_render_html_escapes_special_characters():
    # A single-club pool where every player is forced into the squad, so the
    # player with the malicious/HTML-bearing name is guaranteed to render.
    # 5 clubs, exactly 3 players each, so the per-club cap of 3 is satisfied
    # by construction and doesn't make this composition-exact pool infeasible.
    pool = [
        make_player(1, "GK", 1, 4.5, 5.0, web_name="O'Br<i>en & Co"),
        make_player(2, "GK", 2, 4.0, 1.0),
        make_player(3, "DEF", 1, 4.0, 5.0),
        make_player(4, "DEF", 2, 4.0, 4.0),
        make_player(5, "DEF", 3, 4.0, 3.0),
        make_player(6, "DEF", 4, 4.0, 2.0),
        make_player(7, "DEF", 5, 4.0, 1.0),
        make_player(8, "MID", 3, 4.5, 5.0),
        make_player(9, "MID", 4, 4.5, 4.0),
        make_player(10, "MID", 5, 4.5, 3.0),
        make_player(11, "MID", 1, 4.5, 2.0),
        make_player(12, "MID", 2, 4.5, 1.0),
        make_player(13, "FWD", 3, 4.5, 5.0),
        make_player(14, "FWD", 4, 4.5, 4.0),
        make_player(15, "FWD", 5, 4.5, 3.0),
    ]
    result = optimize_squad(pool, budget=100.0, max_per_club=3)
    assert any(p.element_id == 1 for p in result.squad)

    html = render_html(result, gameweek=1)

    assert "<i>en" not in html
    assert "O&#x27;Br" in html
    assert "&lt;i&gt;" in html
    assert "&amp;" in html
