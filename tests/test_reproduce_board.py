"""scripts/reproduce_board.py rebuilds the leaderboard from the public `preferences.jsonl` alone,
with the same Bradley-Terry code the site runs. The point is that a reader with only the
Hugging Face table can check the published board; so the script must need nothing but that file."""

from __future__ import annotations

import json

from scripts.reproduce_board import load_preferences, rebuild


def _row(a, b, winner, criterion="overall", voter="v1"):
    return {
        "output_a_id": 1,
        "output_b_id": 2,
        "winner": winner,
        "criterion": criterion,
        "task_title": "t",
        "a_generator_slug": a,
        "b_generator_slug": b,
        "a_license": "",
        "b_license": "",
        "a_mesh_available": True,
        "b_mesh_available": True,
        "voter": voter,
        "cohort": None,
        "created": "2026-01-01T00:00:00",
    }


def test_rebuild_orders_generators_by_wins(tmp_path):
    rows = [_row("A", "B", "a")] * 6 + [_row("B", "C", "a")] * 6 + [_row("A", "C", "a")] * 6
    p = tmp_path / "preferences.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    prefs = load_preferences(p)
    board = rebuild(prefs, criterion="overall", bootstrap=0)
    slugs = [r["slug"] for r in board]
    assert slugs == ["A", "B", "C"]
    assert board[0]["n_games"] == 12 and board[2]["n_games"] == 12
    assert board[0]["bt_score"] > board[1]["bt_score"] > board[2]["bt_score"]


def test_ties_split_and_bad_votes_are_dropped(tmp_path):
    rows = [_row("A", "B", "tie")] * 4 + [_row("A", "B", "bad")] * 3
    p = tmp_path / "p.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    board = rebuild(load_preferences(p), criterion="overall", bootstrap=0)
    by = {r["slug"]: r for r in board}
    # A tie is credited as one win EACH WAY and app/ranking.py counts every credited match, so
    # 4 ties are 8 games per side — the same figure the site's board shows. 'bad' adds nothing.
    assert by["A"]["n_games"] == 8 and by["B"]["n_games"] == 8
    assert abs(by["A"]["bt_score"] - by["B"]["bt_score"]) < 1e-6


def test_other_criteria_do_not_leak_into_a_board(tmp_path):
    rows = [_row("A", "B", "a")] * 5 + [_row("B", "A", "a", criterion="visual_quality")] * 50
    p = tmp_path / "p.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    board = rebuild(load_preferences(p), criterion="overall", bootstrap=0)
    assert [r["slug"] for r in board] == ["A", "B"]
