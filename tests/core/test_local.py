import asyncio
import json
import random
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import bots
from app.core.autodarts.errors import ConnectionProblem
from app.core.events import EventBus
from app.core.integrations.caller.service import CallerConfig, call_parts
from app.core.local import LocalDart, LocalGames, LocalOptions
from app.main import create_app


@pytest.fixture
def local(tmp_path):
    game = LocalGames(tmp_path, None, EventBus(capacity=500))
    game.start(LocalOptions(players=["Alice", "Bob"], input="manual", starting_score=301))
    return game


def hit(game, number=20, multiplier=3):
    game.throw(LocalDart(number=number, multiplier=multiplier).segment())


def events(game, name):
    return [event for event in game.bus.normalized_history if event.event == name]


def board(*darts, status="Throw", event="Throw detected"):
    return {
        "status": status,
        "event": event,
        "numThrows": len(darts),
        "throws": [{"segment": {"number": n, "multiplier": m}} for n, m in darts],
    }


def test_scoring_normalized_visit_and_caller_contract(local):
    for _ in range(3):
        hit(local)
    assert local.game_state()["remaining"] == 121
    assert local.game_state()["phase"] == "takeout"
    throws = events(local, "throw")
    assert len(throws) == 3
    assert [e.data["dart"]["position"] for e in throws] == [1, 2, 3]
    assert all(e.data["player"]["is_local"] for e in throws)
    end = events(local, "turn_end")[-1]
    assert end.data["score"] == 180
    assert call_parts(end, CallerConfig()) == [["180"]]
    local.next_turn()
    assert local.game_state()["player"]["name"] == "Bob"
    assert local.game_state()["remaining"] == 301
    assert local.game_state()["phase"] == "ready"
    assert len(events(local, "takeout_started")) == len(events(local, "takeout_finished")) == 1
    assert events(local, "turn_started")[-1].data["player"]["name"] == "Bob"


def test_bust_restores_whole_visit_and_undo_reopens_it(local):
    # 301 -> 121 -> 1 is a bust in double out.
    for _ in range(3):
        hit(local)
    local.next_turn()
    hit(local, 0, 1)
    local.next_turn()
    hit(local)
    hit(local)
    assert local.game_state()["remaining"] == 121
    assert events(local, "bust")[-1].data["score"] == 0
    with pytest.raises(ConnectionProblem):
        hit(local)
    local.undo()
    assert local.game_state()["remaining"] == 61
    assert not local.saved.frame.turns[0].busted
    assert events(local, "throw_removed")


@pytest.mark.parametrize(
    "double_out,finish,winner", [(True, (1, 1), -1), (False, (1, 1), 0), (True, (25, 2), 0)]
)
def test_checkout_rules_and_win_events(tmp_path, double_out, finish, winner):
    game = LocalGames(tmp_path, None, EventBus())
    game.start(LocalOptions(input="manual", starting_score=301, double_out=double_out))
    if finish == (25, 2):
        visits = [[(20, 3)] * 3, [(17, 3), (20, 1)]]  # 301 -> 121 -> 50
    else:
        visits = [[(20, 3)] * 3, [(20, 3), (20, 3)]]  # Straight out allows 1.
        if double_out:
            visits[-1] = [(20, 3), (19, 3), (1, 1)]  # Leave 3, then invalid single 3 below.
            finish = (3, 1)
    for visit in visits:
        for number, multiplier in visit:
            hit(game, number, multiplier)
        game.next_turn()
    hit(game, *finish)
    assert game.saved.frame.winner == winner
    if winner >= 0:
        assert [e.event for e in game.bus.normalized_history][-3:] == ["checkout", "leg_win", "match_win"]
        assert game.game_state()["remaining"] == 0
        game.undo()
        assert game.saved.frame.winner == -1
    else:
        assert events(game, "bust")


def test_saved_game_restores_silently_and_completed_game_does_not_resume(local, tmp_path):
    hit(local)
    restored = LocalGames(tmp_path, None, EventBus())
    assert restored.active
    assert restored.game_state()["remaining"] == 241
    assert restored.game_state()["phase"] == "ready"
    assert not restored.bus.normalized_history
    restored.stop()
    assert not LocalGames(tmp_path, None, EventBus()).active


def test_failed_save_does_not_change_scores_or_emit(local, monkeypatch):
    before = local.status()
    count = len(local.bus.normalized_history)

    def fail(*args):
        raise ConnectionProblem("Disk full")

    monkeypatch.setattr("app.core.local.write_private_json", fail)
    with pytest.raises(ConnectionProblem):
        hit(local)
    assert local.status() == before
    assert len(local.bus.normalized_history) == count


def test_corrupt_saved_frame_is_reported_and_replaceable(local, tmp_path):
    data = local.saved.model_dump(mode="json", by_alias=True)
    data["frame"]["turns"] = []
    local.path.write_text(json.dumps(data), encoding="utf-8")
    game = LocalGames(tmp_path, None, EventBus())
    assert not game.active
    assert game.error
    game.start(LocalOptions(input="manual"))
    assert game.active and not game.error


def test_round_wrap_and_short_visit(local):
    for _ in range(2):
        hit(local, 0, 1)
        local.next_turn()
    assert local.saved.frame.round == 1
    assert local.saved.frame.player == 0
    assert len(events(local, "turn_end")) == 2


@pytest.mark.parametrize("number,multiplier", [(21, 1), (24, 2), (25, 3)])
def test_invalid_segments(local, number, multiplier):
    with pytest.raises(ConnectionProblem):
        hit(local, number, multiplier)


@pytest.mark.parametrize(
    "options",
    [
        {"players": [""]},
        {"players": []},
        {"players": ["x"] * 9},
        {"players": ["x" * 61]},
        {"starting_score": 999},
    ],
)
def test_invalid_options(options):
    with pytest.raises(ValidationError):
        LocalOptions(**options)


@pytest.fixture
def detected(tmp_path):
    game = LocalGames(tmp_path, None, EventBus(capacity=500))
    game.start(LocalOptions(starting_score=301))
    return game


def test_detector_requires_empty_baseline_and_deduplicates(detected):
    detected.consume_board(board((20, 3)))
    assert not events(detected, "throw")
    detected.consume_board(board(event="Takeout finished"))
    assert detected.armed
    for count in range(1, 4):
        detected.consume_board(board(*([(20, 3)] * count)))
        detected.consume_board(board(*([(20, 3)] * count)))
    assert len(events(detected, "throw")) == 3
    detected.consume_board(board(*([(20, 3)] * 3), status="Takeout", event="Takeout started"))
    # Removal snapshots must not undo the already scored visit.
    detected.consume_board(board((20, 3), status="Takeout", event="Takeout started"))
    detected.consume_board(board(event="Takeout finished"))
    detected.consume_board(board(event="Takeout finished"))
    assert detected.saved.frame.game_scores == [121]
    assert len(detected.saved.frame.turns) == 2
    assert len(events(detected, "turn_end")) == 1
    assert len(events(detected, "takeout_finished")) == 1


def test_detector_correction_is_an_edit_not_an_extra_throw(detected):
    detected.consume_board(board())
    detected.consume_board(board((20, 3)))
    detected.consume_board(board((5, 1)))
    assert detected.saved.frame.game_scores == [296]
    assert len(events(detected, "throw")) == 1
    assert len(events(detected, "throw_corrected")) == 1


def test_detector_counts_miss_and_rejects_manual_mutations(detected):
    detected.consume_board(board())
    detected.consume_board(board((0, 0), (20, 3)))
    assert len(events(detected, "throw")) == 2
    assert detected.saved.frame.game_scores == [241]
    with pytest.raises(ConnectionProblem):
        hit(detected)
    with pytest.raises(ConnectionProblem):
        detected.undo()
    with pytest.raises(ConnectionProblem):
        detected.next_turn()


def test_unexpected_reset_and_reconnect_do_not_replay_darts(detected):
    detected.consume_board(board())
    detected.consume_board(board((20, 3)))
    detected.consume_board(board(event="Started"))
    assert not detected.armed
    detected.consume_board(board((20, 3), (20, 3)))
    assert len(events(detected, "throw")) == 1
    detected.consume_board(board())
    detected.next_turn()
    detected.consume_board(board((20, 3)))
    assert len(events(detected, "throw")) == 2
    assert detected.saved.frame.game_scores == [181]


def test_manual_detector_reset_preserves_visit_and_waits_for_confirmation(detected):
    detected.consume_board(board())
    detected.consume_board(board((20, 3)))
    detected.consume_board(board(event="Manual reset"))
    assert not detected.armed
    assert detected.saved.frame.game_scores == [241]
    assert len(detected.saved.frame.turns) == 1
    assert len(events(detected, "manual_reset")) == 1


def test_stopped_detector_preserves_visit_and_requires_running_detection(detected):
    detected.consume_board(board())
    detected.consume_board(board((20, 3)))
    detected.consume_board({"status": "Stopped", "event": "Stopped", "running": False, "numThrows": 0})
    assert detected.detector_state == "stopped"
    assert detected.detector_error == "Local Autodarts detection is stopped."
    assert not detected.armed
    assert not detected.game_state()["available"]
    assert detected.saved.frame.game_scores == [241]
    assert len(events(detected, "throw")) == 1
    with pytest.raises(ConnectionProblem):
        detected.next_turn()
    # Starting detection restores transport readiness, while the interrupted
    # visit still needs explicit acceptance before the next player's throws.
    detected.consume_board({"status": "Throw", "event": "Started", "running": True, "numThrows": 0})
    assert not detected.armed
    detected.next_turn()
    assert detected.armed
    assert detected.saved.frame.game_scores == [241]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"status": "Throw", "numThrows": 1},
        {"status": "Throw", "numThrows": 2, "throws": []},
        board((25, 3)),
        board((21, 1)),
    ],
)
def test_invalid_or_unsupported_detector_never_scores(detected, payload):
    with pytest.raises((ConnectionProblem, ValidationError)):
        detected.consume_board(payload)
    assert detected.saved.frame.game_scores == [301]
    assert not events(detected, "throw")


async def test_polling_only_calls_loopback_and_stops_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.autodarts.local.connect", AsyncMock(side_effect=OSError))
    requests = []
    received = asyncio.Event()

    def respond(request):
        requests.append(str(request.url))
        received.set()
        return httpx.Response(200, json=board())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        game = LocalGames(tmp_path, http, EventBus())
        game.start(LocalOptions())
        task = asyncio.create_task(game.run())
        async with asyncio.timeout(2):
            await received.wait()
        assert game.armed
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert requests == ["http://127.0.0.1:3180/api/state"]


async def test_inflight_detector_response_cannot_score_the_next_player(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.autodarts.local.connect", AsyncMock(side_effect=OSError))
    received, release, processed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def respond(request):
        received.set()
        await release.wait()
        return httpx.Response(200, json=board((20, 3)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        game = LocalGames(tmp_path, http, EventBus())
        game.start(LocalOptions(players=["Alice", "Bob"]))
        game.consume_board(board())
        game.consume_board(board((20, 3)))
        game.consume_board(board(event="Started"))
        assert not game.armed
        task = asyncio.create_task(game.run())
        try:
            await asyncio.wait_for(received.wait(), 2)
            # User clears the board and advances while an older request is in flight.
            game.next_turn()
            game.consume_board = lambda data: processed.set()
            release.set()
            # A later poll uses the new visit; only that poll may consume a response.
            await asyncio.wait_for(processed.wait(), 2)
            assert game.saved.frame.game_scores == [441, 501]
            assert len(events(game, "throw")) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_local_api_offline_source_switch_and_persistence(settings):
    def unexpected(request):
        pytest.fail(f"Offline game contacted {request.url}")

    app = create_app(settings, transport=httpx.MockTransport(unexpected))
    with TestClient(app) as client:
        assert client.get("/local").status_code == 200
        assert client.post("/api/local/throw", json={"number": 20, "multiplier": 3}).status_code == 409
        result = client.post("/api/local/start", json={"input": "manual", "players": ["Alice", "Bob"]})
        assert result.status_code == 200
        assert client.get("/api/status").json()["cloud"]["state"] == "paused_for_local_game"
        assert client.post("/api/local/start", json={"input": "manual"}).status_code == 409
        for _ in range(3):
            assert client.post("/api/local/throw", json={"number": 20, "multiplier": 3}).status_code == 200
        assert client.get("/api/game").json()["remaining"] == 321
        assert client.get("/api/game").json()["source"] == "local"
        assert client.get("/readyz").status_code == 200
        assert client.post("/api/local/next", json={}).status_code == 200
        assert client.get("/api/game").json()["player"]["name"] == "Bob"
    with TestClient(create_app(settings, transport=httpx.MockTransport(unexpected))) as client:
        assert client.get("/api/local").json()["active"]
        assert not client.get("/api/events").json()
        assert client.post("/api/local/stop", json={}).status_code == 200
        assert client.get("/api/game").json().get("source") != "local"


def test_local_api_validation_and_origin_guards(settings):
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/local/start", json={"input": "manual", "players": []}).status_code == 422
        assert (
            client.post("/api/local/start", json={}, headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
        assert client.post("/api/local/start", content="{}").status_code == 415
        assert client.post("/api/local/start", json={"input": "manual"}).status_code == 200
        assert client.post("/api/local/throw", json={"number": 25, "multiplier": 3}).status_code == 409
        assert client.post("/api/local/throw", json={"number": "20", "multiplier": 3}).status_code == 422


def test_detector_restart_silently_restores_scores_and_requires_clear(detected, tmp_path):
    detected.consume_board(board())
    detected.consume_board(board((20, 3)))
    game = LocalGames(tmp_path, None, EventBus())
    game.consume_board(board((20, 3)))
    assert not game.armed
    assert game.saved.frame.game_scores == [241]
    assert not game.bus.normalized_history


def test_bust_detector_extra_physical_darts_cannot_score_or_crash(detected):
    detected.consume_board(board())
    detected.consume_board(board(*([(20, 3)] * 3)))
    detected.consume_board(board(event="Takeout finished"))
    detected.consume_board(board((20, 3), (20, 3), (20, 3)))
    assert detected.saved.frame.turns[0].busted
    detected.consume_board(board((20, 3), (20, 3), (19, 3)))
    assert detected.saved.frame.game_scores == [121]
    assert len(detected.saved.frame.turns[0].throws) == 2


def test_bot_board_geometry_routes_and_levels():
    assert bots.segment_at(0, 103).name == "T20"
    assert bots.segment_at(0, 0).name == "D25"
    assert bots.segment_at(0, 12).name == "S25"
    assert bots.segment_at(166, 0).name == "D6"
    assert bots.segment_at(0, 200).name == "MISS"
    assert bots.target(501, 3, True) == "T20"
    assert bots.target(40, 1, True) == "D20"
    assert bots.target(170, 3, True) == "T20"
    assert bots.target(3, 1, True) == "1"  # No one-dart finish: set up a double.
    assert bots.target(3, 1, False) == "T1"
    rng = random.Random(7)

    def average(level):
        darts = [bots.throw("T20", level, rng)[0] for _ in range(4000)]
        return 3 * sum(dart.number * dart.multiplier for dart in darts) / 4000

    weak, strong = average(1), average(11)
    assert 15 < weak < 35
    assert 110 < strong < 140


def test_bot_visits_play_themselves_and_block_manual_input(tmp_path):
    game = LocalGames(tmp_path, None, EventBus(capacity=500))
    game.random = random.Random(3)
    options = LocalOptions(players=["Alice", {"name": "Robo", "bot": 11}], input="manual", bot_speed="fast")
    assert options.players[0].bot is None
    game.start(options)
    hit(game)
    game.next_turn()
    assert game.bot_level == 11
    with pytest.raises(ConnectionProblem):
        hit(game)
    with pytest.raises(ConnectionProblem):
        game.next_turn()
    now = 0.0
    while game.bot_level is not None:
        now += 0.1
        game.bot_step(now)
    assert now >= 3 * bots.SPEEDS["fast"][0] + bots.SPEEDS["fast"][1]
    bot_turn = game.saved.frame.turns[1]
    assert len(bot_turn.throws) == 3 or bot_turn.busted
    assert game.saved.frame.player == 0
    assert game.status()["options"]["players"][1] == {"name": "Robo", "bot": 11, "auto": None}


def test_bot_turn_ignores_detection_and_counts_as_available(tmp_path):
    game = LocalGames(tmp_path, None, EventBus(capacity=500))
    game.start(LocalOptions(players=[{"name": "Robo", "bot": 3}]))
    assert game.scoring_key() is None
    assert game.game_state()["available"]


@pytest.mark.parametrize("bot", [0, 12, "5"])
def test_invalid_bot_levels(bot):
    with pytest.raises(ValidationError):
        LocalOptions(players=[{"name": "Robo", "bot": bot}])


@pytest.mark.parametrize("player", [{"auto": "insane"}, {"auto": "easy", "bot": 3}])
def test_invalid_auto_bots(player):
    with pytest.raises(ValidationError):
        LocalOptions(players=[{"name": "Robo", **player}])


def test_auto_bot_level_follows_the_players_average():
    # Before anyone throws, an auto bot plays near the starting average.
    assert bots.auto_level("medium", []) == pytest.approx(3.0)
    visits = [60] * 30
    easy, medium, hard = (bots.auto_level(mode, visits) for mode in ("easy", "medium", "hard"))
    average = (bots.PRIOR_AVERAGE * bots.PRIOR_VISITS + sum(visits)) / (bots.PRIOR_VISITS + len(visits))
    assert medium == pytest.approx((average - 15) / 10)
    assert easy < medium < hard
    assert bots.auto_level("easy", [0] * 50) == 1
    assert bots.auto_level("hard", [180] * 50) == 11
    assert bots.spread(4) == bots.SPREAD[4] and bots.spread(11) == pytest.approx(bots.SPREAD[11])
    assert bots.SPREAD[5] < bots.spread(4.5) < bots.SPREAD[4]
    rng = random.Random(11)
    darts = [bots.throw("T20", 4.5, rng)[0] for _ in range(6000)]
    # Between whole levels the bot still scores about 10 * level + 15 per visit.
    assert 54 < 3 * sum(dart.number * dart.multiplier for dart in darts) / 6000 < 66


def test_auto_bot_matches_human_visits_only(tmp_path):
    game = LocalGames(tmp_path, None, EventBus(capacity=500))
    game.random = random.Random(5)
    players = ["Alice", {"name": "Robo", "auto": "hard"}, {"name": "Max", "bot": 11}]
    game.start(LocalOptions(players=players, input="manual", bot_speed="fast"))
    assert game.bot_level is None
    for _ in range(3):
        hit(game)
    game.next_turn()
    assert game.human_visits() == [180]
    assert game.bot_level == pytest.approx(bots.auto_level("hard", [180]))
    now = 0.0
    while game.saved.frame.player != 0:
        now += 0.1
        game.bot_step(now)
    # The other bots' visits do not move the auto bot.
    assert game.human_visits() == [180]
    assert game.status()["options"]["players"][1] == {"name": "Robo", "bot": None, "auto": "hard"}


def test_dart_positions_from_detector_and_bots_are_kept_for_the_board(detected, tmp_path):
    detected.consume_board(board())
    state = board((20, 3), (19, 1))
    state["throws"][0]["coords"] = {"x": -0.01, "y": 0.6}
    state["throws"][1]["coords"] = {"x": "far", "y": None}
    detected.consume_board(state)
    first, second = detected.saved.frame.turns[0].throws
    assert first.coords.model_dump() == {"x": -0.01, "y": 0.6}
    assert second.coords is None
    assert detected.status()["options"]["show_board"] is True
    assert detected.status()["options"]["random_names"] is True

    segment, coords = bots.throw("T20", 11, random.Random(1))
    assert bots.segment_at(coords.x * bots.DOUBLE[1], coords.y * bots.DOUBLE[1]) == segment
