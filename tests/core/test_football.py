import random

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import football
from app.core.errors import ConnectionProblem
from app.core.events import EventBus
from app.core.local import LocalDart, LocalGames, LocalOptions
from app.main import create_app


def game(tmp_path, **options):
    local = LocalGames(tmp_path, None, EventBus())
    local.start(LocalOptions(mode="football", players=["Alice", "Bob"], input="manual", **options))
    return local


def hit(local, target):
    if target in {"B", "OB"}:
        dart = LocalDart(number=25, multiplier=2 if target == "B" else 1)
    elif target == "MISS":
        dart = LocalDart(number=0, multiplier=1)
    else:
        dart = LocalDart(
            number=int(target[1:]),
            multiplier={"I": 1, "O": 1, "T": 3, "D": 2}[target[0]],
            bed="inner" if target[0] == "I" else "outer" if target[0] == "O" else None,
        )
    local.throw(dart.segment(), coords=dart.coordinates())


def visit(local, *targets):
    for target in targets:
        hit(local, target)
    local.next_turn()


def at_right_goal(local):
    visit(local, "OB", "I6", "T6")
    visit(local, "O6", "MISS", "MISS")
    assert local.saved.football.ball == "O6"
    assert local.saved.frame.player == 0


def test_edges_and_beginner_diagonals_are_symmetric():
    assert set(football.neighbours("I17")) == {"I3", "I2", "T17", "OB"}
    assert set(football.neighbours("I17", True)) == {"I3", "I2", "T17", "OB", "T3", "T2"}
    for beginner in [False, True]:
        for region in football.REGIONS:
            adjacent = football.neighbours(region, beginner)
            assert region not in adjacent
            assert set(adjacent) <= football.REGIONS
            for neighbour in adjacent:
                assert region in football.neighbours(neighbour, beginner)
    assert "D17" not in football.neighbours("I17", True)
    assert "D6" not in football.neighbours("T6")
    assert "D6" in football.neighbours("O6")


@pytest.mark.parametrize(
    "kickoff,accepted",
    [("outer", {"OB"}), ("bull", {"OB", "B"}), ("inner", {"OB", "B"} | {f"I{n}" for n in range(1, 21)})],
)
def test_kickoff_choices(kickoff, accepted):
    assert set(football.targets(football.FootballState(), False, kickoff)) == accepted
    for target in football.REGIONS | {None}:
        state = football.FootballState()
        football.move(state, target, 0, False, kickoff, False)
        assert state.kickoff == (target not in accepted)
        assert state.ball == (target if target in accepted else "B")


def test_invalid_hits_use_darts_and_do_not_move_ball(tmp_path):
    local = game(tmp_path)
    hit(local, "D6")
    assert local.saved.football.ball == "B"
    assert local.saved.football.goals == [0, 0]
    hit(local, "OB")
    hit(local, "O6")
    assert local.saved.football.ball == "OB"
    assert local.saved.frame.turns[0].finished_at
    with pytest.raises(ConnectionProblem, match="finished"):
        hit(local, "I6")
    local.next_turn()
    hit(local, "I17")
    hit(local, "T3")
    assert local.saved.football.ball == "I17"
    local.undo()
    hit(local, "I3")
    assert local.saved.football.ball == "I3"


def test_goal_undo_restart_and_conceding_player_kickoff(tmp_path):
    local = game(tmp_path)
    at_right_goal(local)
    hit(local, "D6")
    assert local.saved.football.goals == [1, 0]
    assert local.saved.football.ball == "B" and local.saved.football.kickoff
    assert local.saved.frame.turns[0].finished_at
    local.undo()
    assert local.saved.football.goals == [0, 0]
    assert local.saved.football.ball == "O6"
    hit(local, "D6")
    reloaded = LocalGames(tmp_path, None, EventBus())
    assert reloaded.status()["football"] == local.status()["football"]
    reloaded.next_turn()
    assert reloaded.saved.frame.player == 1
    assert reloaded.saved.football.kickoff
    assert reloaded.saved.frame.turns[0].throws == []
    hit(reloaded, "OB")
    assert not reloaded.saved.football.kickoff


@pytest.mark.parametrize("own_goals", [False, True])
def test_own_goal_option_and_same_player_gets_new_turn(tmp_path, own_goals):
    local = game(tmp_path, football_own_goals=own_goals)
    visit(local, "OB", "I6", "T6")
    hit(local, "O6")
    hit(local, "D6")  # Bob is attacking left, so D6 is his own goal.
    assert local.saved.football.goals == ([1, 0] if own_goals else [0, 0])
    if own_goals:
        assert local.saved.football.event == "own_goal"
        local.undo()
        assert local.saved.football.goals == [0, 0]
        assert local.saved.football.ball == "O6"
        hit(local, "D6")
        local.next_turn()
        assert local.saved.frame.player == 1
        assert local.saved.football.kickoff
    else:
        assert local.saved.football.ball == "D6"
        hit(local, "D10")  # Sideways along the boundary remains legal.
        assert local.saved.football.ball == "D10"


def test_only_correct_goal_scores_and_a_full_game_finishes(tmp_path):
    local = game(tmp_path, goals_to_win=3)
    for goal in range(3):
        if goal:
            visit(local, "OB", "I6", "T6")  # Conceding player moves toward their own goal.
            hit(local, "O6")
        else:
            at_right_goal(local)
        hit(local, "D6")
        assert local.saved.football.goals == [goal + 1, 0]
        if goal < 2:
            local.next_turn()
    assert local.saved.frame.winner == 0
    assert local.game_state()["remaining"] is None
    with pytest.raises(ConnectionProblem, match="finished"):
        local.next_turn()
    local.undo()
    assert local.saved.frame.winner == -1
    assert local.saved.football.goals == [2, 0]


def test_detected_coordinates_corrections_and_takeout(tmp_path):
    local = game(tmp_path)
    local.stop()
    local.start(LocalOptions(mode="football", players=["A", "B"], input="autodarts"))

    def board(*darts, status="Throw", event="Throw detected"):
        return {"status": status, "event": event, "numThrows": len(darts), "throws": list(darts)}

    def dart(number, multiplier=1, bed=None):
        value = LocalDart(number=number, multiplier=multiplier, bed=bed)
        return {
            "segment": value.segment().model_dump(),
            "coords": value.coordinates().model_dump() if bed else None,
        }

    local.consume_board(board(status="Ready", event=""))
    outer_bull = dart(25)
    local.consume_board(board(outer_bull, dart(17, bed="inner")))
    assert local.saved.football.ball == "I17"
    # Same scored segment, different bed: the correction must replay movement.
    local.consume_board(board(outer_bull, dart(17, bed="outer")))
    assert local.saved.football.ball == "OB"
    local.consume_board(board(outer_bull, dart(17, bed="inner")))
    assert local.saved.football.ball == "I17"
    local.consume_board(board(outer_bull, dart(17, bed="inner"), status="Takeout", event="Takeout started"))
    local.consume_board(board(status="Ready", event="Takeout finished"))
    assert local.saved.frame.player == 1
    assert local.saved.football.ball == "I17"


def test_missing_coordinates_do_not_guess_a_single_region(tmp_path):
    local = game(tmp_path)
    hit(local, "OB")
    with pytest.raises(ConnectionProblem, match="coordinates"):
        local.throw(LocalDart(number=6, multiplier=1).segment())
    assert len(local.saved.frame.turns[0].throws) == 1
    assert local.saved.football.ball == "OB"


def test_player_validation_and_manual_api(settings):
    for players in [["A"], ["A", "B", "C"]]:
        with pytest.raises(ValidationError):
            LocalOptions(mode="football", players=players)
    app = create_app(settings, transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with TestClient(app) as client:
        response = client.post(
            "/api/local/start", json={"mode": "football", "players": ["A", "B"], "input": "manual"}
        )
        assert response.status_code == 200
        assert response.json()["football"]["ball"] == "B"
        client.post("/api/local/throw", json={"number": 25, "multiplier": 1})
        assert client.post("/api/local/throw", json={"number": 6, "multiplier": 1}).status_code == 409
        response = client.post("/api/local/throw", json={"number": 6, "multiplier": 1, "bed": "inner"})
        assert response.json()["football"]["ball"] == "I6"


def test_bots_choose_legal_forward_targets():
    for player in (0, 1):
        state = football.FootballState()
        for _ in range(12):
            target = football.bot_target(state, player, False, "outer", True)
            assert target in football.targets(state, False, "outer")
            football.move(state, target, player, False, "outer", True)
            if state.goal_by is not None:
                break
        assert state.goals[player] == 1
        assert state.event == "goal"
    segment, coords = football.throw_at("I17", 11, random.Random(1))
    assert coords is not None and segment.number in range(26)


def test_own_goal_can_win_for_opponent_and_undo_restores_both_scores(tmp_path):
    local = game(tmp_path, goals_to_win=3, football_own_goals=True)
    for count in range(3):
        visit(local, "OB", "I6", "T6")
        hit(local, "O6")
        hit(local, "D6")
        # After the own goal Bob takes another turn; send Alice back to her attacking side.
        assert local.saved.football.goal_by == 0
        if count < 2:
            local.next_turn()
            assert local.saved.frame.player == 1
            visit(local, "MISS", "MISS", "MISS")
    assert local.saved.frame.winner == 0
    assert local.saved.frame.player == 1
    local.undo()
    assert local.saved.frame.winner == -1
    assert local.saved.football.goals == [2, 0]
    assert local.saved.football.ball == "O6"
