from __future__ import annotations

from fpl_forecast.formatting_html import render_html
from fpl_forecast.optimizer import optimize_squad
from fpl_forecast.scoring import build_fixture_ticker
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


def test_render_html_includes_glossary():
    html = render_html(_make_minimal_result(), gameweek=1)
    assert "What do these numbers mean?" in html
    assert "Proj. Pts (Projected Points)" in html
    assert "FDR (Fixture Difficulty Rating)" in html


def test_render_html_includes_fixture_ticker_when_provided(sample_pool):
    result = optimize_squad(sample_pool, budget=100.0, max_per_club=3)
    teams = [{"id": i, "short_name": f"T{i}"} for i in range(1, 11)]
    # Only team 1 gets a fixture in the window; every other squad team blanks.
    all_fixtures = [
        {"event": 5, "team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 4},
    ]
    ticker = build_fixture_ticker(teams, all_fixtures, start_gw=5, num_gws=5)

    html = render_html(result, gameweek=5, fixture_ticker=ticker)

    assert "Upcoming fixtures" in html
    squad_team_ids = {p.team_id for p in result.squad}
    if 1 in squad_team_ids:
        assert "fdr-box" in html
    assert "BLANK" in html  # at least one squad club has no fixture in this contrived window


def test_render_html_includes_player_pool_when_provided(sample_pool):
    result = optimize_squad(sample_pool, budget=100.0, max_per_club=3)
    html = render_html(result, gameweek=1, all_scores=sample_pool)

    assert "Who else was in the mix" in html
    assert 'class="picked"' in html  # at least one squad player appears highlighted in the pool table
    assert "picked-mark" in html


def test_render_html_includes_gameweek_fixtures_list_when_provided():
    result = _make_minimal_result()
    teams_lookup = {i: f"T{i}" for i in range(1, 11)}
    gw_fixtures = [
        {"team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 4},
        {"team_h": 3, "team_a": 4, "team_h_difficulty": 3, "team_a_difficulty": 3},
    ]

    html = render_html(result, gameweek=1, gw_fixtures=gw_fixtures, teams_lookup=teams_lookup)

    assert "Gameweek 1 fixtures" in html
    assert "T1" in html and "T2" in html and "T3" in html and "T4" in html
    assert html.count("fixture-row") >= 2


def test_render_html_omits_gameweek_fixtures_when_not_provided():
    html = render_html(_make_minimal_result(), gameweek=1)
    assert "Gameweek 1 fixtures" not in html


def test_render_html_includes_full_league_ticker_when_provided():
    result = _make_minimal_result()
    teams = [{"id": i, "short_name": f"T{i}"} for i in range(1, 11)]
    teams_lookup = {t["id"]: t["short_name"] for t in teams}
    all_fixtures = [{"event": 2, "team_h": 9, "team_a": 10, "team_h_difficulty": 2, "team_a_difficulty": 4}]
    ticker = build_fixture_ticker(teams, all_fixtures, start_gw=1, num_gws=5)

    html = render_html(result, gameweek=1, fixture_ticker=ticker, teams_lookup=teams_lookup)

    assert "Full-league fixture difficulty" in html
    # Team 9/10 aren't necessarily in the squad, but must still appear since
    # this ticker covers every club, not just the squad's.
    non_squad_ids = {9, 10} - {p.team_id for p in result.squad}
    if non_squad_ids:
        assert any(f"T{tid}" in html for tid in non_squad_ids)


def test_render_html_includes_score_breakdown_chart():
    html = render_html(_make_minimal_result(), gameweek=1)
    assert "Score breakdown" in html
    assert "chart-bar-row" in html


def test_render_html_score_breakdown_chart_handles_none_form_component():
    # form_component is None for a player with no real current-season
    # recency signal (see scoring.score_player) -- the chart must render
    # an "n/a" row for Form instead of crashing on `f"{None:.2f}"`.
    result = _make_minimal_result()
    for p in result.starting_xi:
        p.form_component = None

    html = render_html(result, gameweek=1)

    assert "n/a" in html


def test_render_html_includes_most_selected_section_when_ownership_present(sample_pool):
    result = optimize_squad(sample_pool, budget=100.0, max_per_club=3)
    for i, p in enumerate(sample_pool):
        p.selected_by_percent = float(i % 40)  # give everyone some nonzero ownership

    html = render_html(result, gameweek=1, all_scores=sample_pool)

    assert "Most selected players" in html
    assert "Our Proj. Pts" in html  # header unique to the most-selected table


def test_render_html_omits_most_selected_section_without_ownership_data(sample_pool):
    result = optimize_squad(sample_pool, budget=100.0, max_per_club=3)
    # sample_pool players all default to selected_by_percent=0.0 (no data).
    html = render_html(result, gameweek=1, all_scores=sample_pool)

    assert "Most selected players" not in html


def _make_minimal_result():
    pool = [
        make_player(1, "GK", 1, 4.5, 5.0),
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
    return optimize_squad(pool, budget=100.0, max_per_club=3)
