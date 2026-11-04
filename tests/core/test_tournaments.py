import random
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import ConnectionProblem
from app.core.events import EventBus
from app.core.local import LocalDart, LocalGames
from app.core.tournaments import TournamentOptions, Tournaments, advance, participants, standings
from app.main import create_app


def tournament(tmp_path, count=8, format="single", **options):
    local = LocalGames(tmp_path, None, EventBus())
    manager = Tournaments(tmp_path, local)
    manager.create(
        TournamentOptions(players=[f"Player {i + 1}" for i in range(count)], format=format, **options)
    )
    return manager


def ready(manager):
    return [match for match in manager.status()["tournament"]["matches"] if match["ready"]]


def finish(manager, match_id, winner=0):
    match = next(match for match in manager.saved.matches if match.id == match_id)
    _, pair = participants(match, {item.id: item for item in manager.saved.matches})
    match.completed, match.winner = True, pair[winner]
    advance(manager.saved)


@pytest.mark.parametrize("format", ["single", "double"])
@pytest.mark.parametrize("count", [2, 3, 4, 5, 7, 8, 9, 16, 31, 32])
@pytest.mark.parametrize("seed", [1, 9])
def test_elimination_finishes_with_correct_losses_and_byes(tmp_path, format, count, seed):
    manager = tournament(tmp_path, count, format)
    losses = Counter()
    rng = random.Random(seed)
    games = 0
    while manager.saved.state == "active":
        choices = ready(manager)
        assert choices, "A live bracket must have a playable match"
        match = rng.choice(choices)
        pair = match["players"]
        assert len(set(pair)) == 2
        winner = rng.randrange(2)
        losses[pair[1 - winner]] += 1
        finish(manager, match["id"], winner)
        games += 1
        assert games <= count * 2
    champion = manager.saved.champion
    assert losses[champion] < (2 if format == "double" else 1)
    assert all(
        losses[player.id] == (2 if format == "double" else 1)
        for player in manager.saved.players
        if player.id != champion
    )
    assert games == count - 1 if format == "single" else games in {2 * count - 2, 2 * count - 1}


def test_double_final_resets_only_after_upper_champions_first_loss(tmp_path):
    manager = tournament(tmp_path, 2, "double")
    finish(manager, ready(manager)[0]["id"], 0)
    final = ready(manager)[0]
    assert final["stage"] == "final"
    finish(manager, final["id"], 1)
    reset = ready(manager)[0]
    assert reset["stage"] == "reset"
    assert reset["players"] == final["players"]
    finish(manager, reset["id"], 0)
    assert manager.saved.state == "completed"


@pytest.mark.parametrize("count,rounds", [(2, 1), (3, 2), (5, 4), (8, 5), (16, 5), (32, 6)])
def test_swiss_rounds_pair_every_player_and_avoid_repeated_byes(tmp_path, count, rounds):
    manager = tournament(tmp_path, count, "swiss", swiss_rounds=rounds)
    for number in range(1, rounds + 1):
        matches = [match for match in manager.saved.matches if match.round == number]
        appeared = [slot.ref for match in matches for slot in match.slots if slot.ref]
        assert len(appeared) == count == len(set(appeared))
        for match in ready(manager):
            assert match["round"] == number
            finish(manager, match["id"], number % 2)
    assert manager.saved.state == "completed"
    rows = standings(manager.saved)
    assert all(row["played"] + row["byes"] == rounds for row in rows)
    assert max(row["byes"] for row in rows) <= 1
    assert manager.saved.champion == rows[0]["id"]
    if count % 2 == 0:
        assert all(len(row["opponents"]) == len(set(row["opponents"])) for row in rows)


def test_swiss_standings_seed_final_tree_and_keep_nonqualifiers_out(tmp_path):
    manager = tournament(tmp_path, 6, "swiss_elimination", swiss_rounds=3, finalists=4)
    while all(match.stage == "swiss" for match in manager.saved.matches):
        for match in ready(manager):
            finish(manager, match["id"])
    rows = standings(manager.saved)
    first = ready(manager)
    assert [match["players"] for match in first] == [
        [rows[0]["id"], rows[3]["id"]],
        [rows[1]["id"], rows[2]["id"]],
    ]
    while manager.saved.state == "active":
        for match in ready(manager):
            finish(manager, match["id"])
    assert manager.saved.champion == rows[0]["id"]


def test_validation_and_damaged_save_protection(tmp_path):
    for values in [
        dict(players=["A"]),
        dict(players=["A", " a "]),
        dict(players=["A", ""]),
        dict(players=["A", "B"], format="swiss", swiss_rounds=2),
        dict(players=["A", "B", "C"], format="swiss_elimination", swiss_rounds=1, finalists=3),
    ]:
        with pytest.raises(ValidationError):
            TournamentOptions(**values)
    (tmp_path / "tournament.json").write_text("broken")
    manager = Tournaments(tmp_path, LocalGames(tmp_path, None, EventBus()))
    assert manager.status()["error"]
    with pytest.raises(ConnectionProblem):
        manager.create(TournamentOptions(players=["A", "B"]))
    assert (tmp_path / "tournament.json").read_text() == "broken"


def checkout_first(local):
    # 301 straight out, keeping the other player in the game with a miss.
    for _ in range(3):
        local.throw(LocalDart(number=20, multiplier=3).segment())
    local.next_turn()
    local.throw(LocalDart(number=0, multiplier=1).segment())
    local.next_turn()
    for number, multiplier in [(20, 3), (20, 3), (1, 1)]:
        local.throw(LocalDart(number=number, multiplier=multiplier).segment())


def test_result_recovers_after_restart_and_is_recorded_only_once(tmp_path):
    manager = tournament(tmp_path, 4, starting_score=301, double_out=False)
    match = ready(manager)[0]
    manager.local.start(
        manager.match_options(match["id"]), tournament_id=manager.saved.id, tournament_match_id=match["id"]
    )
    checkout_first(manager.local)
    with pytest.raises(ConnectionProblem, match="completed tournament"):
        manager.local.undo()
    # A crash between game persistence and bracket persistence must not lose the winner.
    local = LocalGames(tmp_path, None, EventBus())
    recovered = Tournaments(tmp_path, local)
    data = recovered.status()
    result = next(item for item in data["tournament"]["matches"] if item["id"] == match["id"])
    assert result["completed"] and result["winner"] == match["players"][0]
    assert recovered.status() == data


def test_tournament_api_resume_guards_and_match_handover(settings):
    app = create_app(settings, transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with TestClient(app) as client:
        assert client.get("/local/tournament").status_code == 200
        assert client.get("/api/tournament").json()["tournament"] is None
        body = {"players": ["Alice", "Bob", "Carol", "Dan"], "starting_score": 301, "double_out": False}
        assert (
            client.post("/api/tournament", json=body, headers={"Origin": "https://evil.test"}).status_code
            == 403
        )
        assert client.post("/api/tournament", content="{}").status_code == 415
        response = client.post("/api/tournament", json=body)
        assert response.status_code == 200
        data = response.json()["tournament"]
        assert client.post("/api/tournament", json=body).status_code == 409
        matches = [match for match in data["matches"] if match["ready"]]
        first, second = matches
        assert (
            client.post(f"/api/tournament/matches/{data['matches'][-1]['id']}/start", json={}).status_code
            == 409
        )
        start = client.post(f"/api/tournament/matches/{first['id']}/start", json={}).json()
        resumed = client.post(f"/api/tournament/matches/{first['id']}/start", json={}).json()
        assert resumed["frame"]["id"] == start["frame"]["id"]
        assert start["tournament"]["id"] == data["id"]
        assert client.post(f"/api/tournament/matches/{second['id']}/start", json={}).status_code == 409
        # Exercise real API scoring, then hand over without manually ending the won game.
        for _ in range(3):
            assert client.post("/api/local/throw", json={"number": 20, "multiplier": 3}).status_code == 200
        client.post("/api/local/next", json={})
        client.post("/api/local/throw", json={"number": 0, "multiplier": 1})
        client.post("/api/local/next", json={})
        for number in (20, 20, 1):
            result = client.post(
                "/api/local/throw", json={"number": number, "multiplier": 3 if number == 20 else 1}
            )
            assert result.status_code == 200
        assert client.post("/api/local/undo", json={}).status_code == 409
        assert client.post(f"/api/tournament/matches/{second['id']}/start", json={}).status_code == 200
        assert client.post("/api/tournament/cancel", json={}).json()["tournament"]["state"] == "cancelled"
        assert not client.get("/api/local").json()["active"]


def test_detected_correction_cannot_reopen_completed_tournament_game(tmp_path):
    manager = tournament(tmp_path, 2, starting_score=301, double_out=False)
    match = ready(manager)[0]
    local = manager.local
    local.start(
        manager.match_options(match["id"]), tournament_id=manager.saved.id, tournament_match_id=match["id"]
    )
    checkout_first(local)
    local.detected = [dart.segment for dart in local.saved.frame.turns[0].throws]
    local.armed = True
    corrected = [segment.model_dump() for segment in local.detected]
    corrected[-1] = {"number": 2, "multiplier": 1, "name": "S2"}
    local.consume_board(
        {"status": "Throw", "numThrows": 3, "throws": [{"segment": segment} for segment in corrected]}
    )
    assert local.saved.frame.winner == 0
    assert local.saved.frame.game_scores[0] == 0
    assert manager.status()["tournament"]["state"] == "completed"


def test_failed_bracket_write_keeps_result_recoverable(tmp_path, monkeypatch):
    manager = tournament(tmp_path, 2, starting_score=301, double_out=False)
    match = ready(manager)[0]
    manager.local.start(
        manager.match_options(match["id"]), tournament_id=manager.saved.id, tournament_match_id=match["id"]
    )
    checkout_first(manager.local)
    from app.core import tournaments

    write = tournaments.write_private_json

    def failed_write(*args):
        raise ConnectionProblem("Disk full")

    monkeypatch.setattr(tournaments, "write_private_json", failed_write)
    with pytest.raises(ConnectionProblem, match="Disk full"):
        manager.sync()
    assert not manager.saved.matches[0].completed
    assert manager.local.saved.frame.winner == 0
    monkeypatch.setattr(tournaments, "write_private_json", write)
    manager.sync()
    assert manager.saved.state == "completed"


def test_in_progress_match_resumes_after_runtime_restart(settings):
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with TestClient(create_app(settings, transport=transport)) as client:
        data = client.post("/api/tournament", json={"players": ["A", "B"]}).json()["tournament"]
        match_id = data["matches"][0]["id"]
        started = client.post(f"/api/tournament/matches/{match_id}/start", json={}).json()
        client.post("/api/local/throw", json={"number": 20, "multiplier": 3})
    with TestClient(create_app(settings, transport=transport)) as client:
        resumed = client.post(f"/api/tournament/matches/{match_id}/start", json={}).json()
        assert resumed["frame"]["id"] == started["frame"]["id"]
        assert resumed["frame"]["gameScores"] == [441, 501]
        assert client.get("/api/tournament").json()["tournament"]["matches"][0]["running"]
        # Abandoning does not award a win and replay starts from the original score.
        client.post("/api/local/stop", json={})
        replay = client.post(f"/api/tournament/matches/{match_id}/start", json={}).json()
        assert replay["frame"]["id"] != started["frame"]["id"]
        assert replay["frame"]["gameScores"] == [501, 501]


def test_new_tournament_can_follow_completed_final_without_losing_winner(settings):
    app = create_app(settings, transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with TestClient(app) as client:
        options = {"players": ["A", "B"], "starting_score": 301, "double_out": False}
        data = client.post("/api/tournament", json=options).json()["tournament"]
        match_id = data["matches"][0]["id"]
        client.post(f"/api/tournament/matches/{match_id}/start", json={})
        checkout_first(app.state.runtime.local)
        completed = client.get("/api/tournament").json()["tournament"]
        assert completed["state"] == "completed"
        assert completed["champion"] == completed["players"][0]["id"]
        response = client.post("/api/tournament", json=options)
        assert response.status_code == 200
        assert response.json()["tournament"]["id"] != data["id"]
        assert not client.get("/api/local").json()["active"]
