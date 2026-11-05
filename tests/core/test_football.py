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
    defaults = {
        "football_format": "goals",
        "football_kickoff": "outer",
        "football_beginner": False,
        "football_own_goals": False,
    }
    defaults.update(options)
    local.start(LocalOptions(
        mode="football",
        players=["Alice", "Bob"],
        input="manual",
        **defaults,
    ))
    bullup(local)
    return local


def bullup(local):
    for distance in (4, 10):
        local.throw(
            LocalDart(number=25, multiplier=1).segment(),
            coords=football.Coords(x=0, y=distance / 170),
            detected=local.saved.options.input == "autodarts",
        )
        local.next_turn(bot=True)


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


def test_football_defaults():
    options = LocalOptions(mode="football", players=["Alice", "Bob"])
    assert options.football_format == "rounds"
    assert options.football_rounds == 10
    assert options.football_kickoff == "inner"
    assert options.football_beginner is True
    assert options.football_own_goals is True


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
        assert local.saved.football.ball == "O6"
        hit(local, "O10")
        assert local.saved.football.ball == "O10"


def test_only_correct_goal_scores_and_a_full_game_finishes(tmp_path):
    local = game(tmp_path, goals_to_win=3)
    for goal in range(3):
        direction = "6" if local.saved.football.half == 1 else "11"
        if goal == 2:
            visit(local, "MISS", "MISS", "MISS")  # Complete the round before half-time.
            direction = "11"
        if goal:
            visit(local, "OB", "I" + direction, "T" + direction)
            hit(local, "O" + direction)
        else:
            at_right_goal(local)
        hit(local, "D" + direction)
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
    bullup(local)

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
        options = response.json()["options"]
        assert options["football_format"] == "rounds"
        assert options["football_kickoff"] == "inner"
        assert options["football_beginner"] is True
        assert options["football_own_goals"] is True
        assert response.json()["football"]["ball"] == "B"
        for distance in (4, 10):
            response = client.post(
                "/api/local/throw", json={"number": 25, "multiplier": 1, "distance_mm": distance}
            )
            assert response.status_code == 200, response.text
            response = client.post("/api/local/next", json={})
            assert response.status_code == 200, response.text
        client.post("/api/local/throw", json={"number": 25, "multiplier": 1})
        assert client.post("/api/local/throw", json={"number": 6, "multiplier": 1}).status_code == 409
        response = client.post("/api/local/throw", json={"number": 6, "multiplier": 1, "bed": "inner"})
        assert response.status_code == 200, response.text
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
    local.saved.football.goals = [2, 0]
    local.saved.football_visit.goals = [2, 0]
    local.saved.frame.game_scores = [2, 0]
    visit(local, "OB", "I6", "T6")
    hit(local, "O6")
    hit(local, "D6")
    assert local.saved.frame.winner == 0
    assert local.saved.frame.player == 1
    local.undo()
    assert local.saved.frame.winner == -1
    assert local.saved.football.goals == [2, 0]
    assert local.saved.football.ball == "O6"


def test_bullup_closest_ties_undo_and_persistence(tmp_path):
    local = LocalGames(tmp_path, None, EventBus())
    local.start(LocalOptions(mode="football", players=["A", "B"], input="manual"))
    assert local.saved.football.phase == "bullup"
    for distance in (8, 8):
        dart = LocalDart(number=25, multiplier=1, distance_mm=distance)
        local.throw(dart.segment(), coords=dart.coordinates())
        local.next_turn()
    assert local.saved.football.event == "bullup_tie"
    dart = LocalDart(number=25, multiplier=1, distance_mm=12)
    local.throw(dart.segment(), coords=dart.coordinates())
    local.undo()
    assert local.saved.football.bullup == [None, None]
    local.throw(dart.segment(), coords=dart.coordinates())
    local.next_turn()
    dart = LocalDart(number=25, multiplier=1, distance_mm=2)
    local.throw(dart.segment(), coords=dart.coordinates())
    reloaded = LocalGames(tmp_path, None, EventBus())
    reloaded.next_turn()
    assert reloaded.saved.frame.player == reloaded.saved.football.starter == 1
    assert reloaded.saved.football.phase == "play"


def empty_round(local):
    for _ in range(2):
        visit(local, "MISS", "MISS", "MISS")


@pytest.mark.parametrize("tiebreak", ["draw", "golden", "penalties"])
def test_rounds_halftime_and_tiebreaks(tmp_path, tiebreak):
    local = game(tmp_path, football_format="rounds", football_rounds=2, football_tiebreak=tiebreak)
    empty_round(local)
    state = local.saved.football
    assert state.half == 2 and state.event == "halftime"
    assert state.ball == "B" and local.saved.frame.player == 1
    assert football.goal_for(state, 0) == "D11"
    empty_round(local)
    assert local.saved.football.phase == ("finished" if tiebreak == "draw" else tiebreak)
    if tiebreak == "draw":
        with pytest.raises(ConnectionProblem, match="finished"):
            hit(local, "OB")
    elif tiebreak == "penalties":
        hit(local, "D11")
        assert local.saved.frame.winner == -1
        local.next_turn()
        hit(local, "MISS")
        assert local.saved.frame.winner == 0
        local.undo()
        assert local.saved.frame.winner == -1
        hit(local, "D6")
        local.next_turn()
        assert local.saved.football.penalties == [None, None]
        hit(local, "MISS")
        local.next_turn()
        hit(local, "D6")
        assert local.saved.frame.winner == 1
    else:
        visit(local, "OB", "I11", "T11")
        visit(local, "O11", "MISS", "MISS")
        hit(local, "D11")
        assert local.saved.frame.winner == 0


def test_clock_stoppage_and_odd_rounds(tmp_path):
    local = game(tmp_path, football_format="rounds", football_rounds=5, football_tiebreak="draw")
    local.saved.football.clock_seconds = []  # Legacy saves retain their evenly spaced clock.
    local.saved.football_visit.clock_seconds = []
    # Persist a deterministic allowance for both halves.
    local.saved.football.added = [1, 2]
    local.saved.football_visit.added = [1, 2]
    assert football.clock(local.saved.football, local.saved.options) == "22′"
    empty_round(local)
    assert football.clock(local.saved.football, local.saved.options) == "45′"
    empty_round(local)
    assert football.clock(local.saved.football, local.saved.options) == "45+1′"
    empty_round(local)
    assert local.saved.football.half == 2
    for _ in range(4):
        empty_round(local)
    assert football.clock(local.saved.football, local.saved.options) == "90+2′"
    empty_round(local)
    assert local.saved.football.phase == "finished"
    assert local.saved.football.rounds == 8


def test_goals_halftime_waits_for_round_end(tmp_path):
    local = game(tmp_path, goals_to_win=3)
    local.saved.football.goals = [1, 0]
    local.saved.football_visit.goals = [1, 0]
    local.saved.frame.game_scores = [1, 0]
    at_right_goal(local)
    hit(local, "D6")
    assert local.saved.football.halftime_due
    local.next_turn()
    assert local.saved.football.half == 1
    visit(local, "MISS", "MISS", "MISS")
    assert local.saved.football.half == 2
    assert local.saved.frame.player == 1
    assert local.saved.football.ball == "B"


def test_wire_detection_confirmation_and_repeat_snapshots(tmp_path):
    local = game(tmp_path)
    local.saved.options.input = "autodarts"
    local.armed = True
    raw = {
        "status": "Throw",
        "numThrows": 1,
        "throws": [
            {"segment": {"number": 25, "multiplier": 1, "name": "S25"}, "coords": {"x": 0, "y": 15.9 / 170}}
        ],
    }
    local.consume_board(raw)
    assert local.saved.football_pending is not None
    assert not local.saved.frame.turns[0].throws
    local.confirm_football(LocalDart(number=25, multiplier=1))
    assert local.saved.football.ball == "OB"
    assert local.saved.football_pending is None
    local.consume_board(raw)
    assert len(local.saved.frame.turns[0].throws) == 1
    assert local.saved.football_pending is None
    assert football.near_wire(football.Coords(x=0, y=99 / 170))
    assert not football.near_wire(football.Coords(x=0, y=65 / 170))


def test_wraparound_and_own_goal_blocked():
    assert "I5" in football.neighbours("I20")
    state = football.FootballState(ball="O11", kickoff=False)
    football.move(state, "D11", 0, False, "outer", False)
    assert state.ball == "O11" and state.goals == [0, 0]


def test_wire_confirmation_survives_restart_and_board_clear(tmp_path):
    local = game(tmp_path)
    local.saved.options.input = "autodarts"
    local.armed = True
    raw = {
        "status": "Throw",
        "numThrows": 1,
        "throws": [
            {"segment": {"number": 25, "multiplier": 1, "name": "S25"}, "coords": {"x": 0, "y": 15.9 / 170}}
        ],
    }
    local.consume_board(raw)
    reloaded = LocalGames(tmp_path, None, EventBus())
    reloaded.armed = True
    reloaded.consume_board({"status": "Ready", "numThrows": 0})
    assert reloaded.saved.football_pending
    reloaded.confirm_football(LocalDart(number=25, multiplier=1))
    assert reloaded.saved.football.ball == "OB"
    assert len(reloaded.saved.frame.turns[0].throws) == 1


def test_rounds_score_winner_and_extra_own_goal_visit(tmp_path):
    local = game(tmp_path, football_format="rounds", football_rounds=2, football_own_goals=True)
    # Alice scores an own goal in her first visit: she gets another visit in this round.
    local.saved.football.ball = local.saved.football_visit.ball = "O11"
    local.saved.football.kickoff = local.saved.football_visit.kickoff = False
    hit(local, "D11")
    local.next_turn()
    assert local.saved.frame.player == 0 and local.saved.football.rounds == 0
    visit(local, "MISS", "MISS", "MISS")
    assert local.saved.football.rounds == 0
    visit(local, "MISS", "MISS", "MISS")
    assert local.saved.football.half == 2 and local.saved.football.rounds == 1
    empty_round(local)
    assert local.saved.football.phase == "finished" and local.saved.frame.winner == 1


def test_vision_coordinates_define_the_cell(tmp_path):
    local = game(tmp_path)
    local.saved.options.input = "autodarts"
    local.armed = True
    # Segment metadata disagrees, but the coordinates are in the outer bull.
    local.consume_board(
        {
            "status": "Throw",
            "numThrows": 1,
            "throws": [
                {"segment": {"number": 6, "multiplier": 2, "name": "D6"}, "coords": {"x": 0, "y": 11 / 170}}
            ],
        }
    )
    assert local.saved.football.ball == "OB"


def test_forfeited_dart_near_wire_does_not_block_goal(tmp_path):
    local = game(tmp_path)
    local.saved.options.input = "autodarts"
    local.armed = True
    local.saved.football.ball = local.saved.football_visit.ball = "O6"
    local.saved.football.kickoff = local.saved.football_visit.kickoff = False
    local.consume_board(
        {
            "status": "Throw",
            "numThrows": 2,
            "throws": [
                {"segment": {"number": 6, "multiplier": 2, "name": "D6"}, "coords": {"x": 166 / 170, "y": 0}},
                {
                    "segment": {"number": 25, "multiplier": 1, "name": "S25"},
                    "coords": {"x": 0, "y": 15.9 / 170},
                },
            ],
        }
    )
    assert local.saved.football.goals == [1, 0]
    assert local.saved.football_pending is None
    assert len(local.saved.frame.turns[0].throws) == 1


@pytest.mark.parametrize("beginner", [False, True])
def test_pitch_inspection_uses_the_backend_neighbour_graph(tmp_path, beginner):
    local = game(tmp_path, football_beginner=beginner)
    graph = local.status()["football"]["neighbours"]
    assert set(graph) == football.REGIONS
    assert graph["I17"] == football.neighbours("I17", beginner)
    assert ("T3" in graph["I17"]) == beginner
    assert "I5" in graph["I20"]


def test_varied_match_clock_is_monotonic_bounded_and_saved(tmp_path):
    for rounds in (2, 5, 10, 16, 200):
        schedule = football.clock_schedule(rounds, random.Random(12))
        assert [len(half) for half in schedule] == [rounds // 2, rounds - rounds // 2]
        for half in schedule:
            assert half[-1] == 2700
            assert all(a < b for a, b in zip([0, *half[:-1]], half, strict=True))
    ticks = football.clock_schedule(10, random.Random(12))[0]
    assert len({b - a for a, b in zip([0, *ticks[:-1]], ticks, strict=True)}) > 1
    local = game(tmp_path, football_format="rounds", football_rounds=10)
    first = local.status()["football"]["clock"]
    assert ":" in first
    reloaded = LocalGames(tmp_path, None, EventBus())
    assert reloaded.status()["football"]["clock"] == first
    assert reloaded.saved.football.clock_seconds == local.saved.football.clock_seconds
    empty_round(local)
    assert local.status()["football"]["clock"] > first
