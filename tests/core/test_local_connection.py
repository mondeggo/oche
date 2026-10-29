import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from websockets.asyncio.server import serve

from app.core.autodarts.detection import LocalDetection
from app.core.autodarts.errors import ConnectionProblem
from app.core.autodarts.local import LocalBoardConnection
from app.core.config import Settings
from app.core.events import EventBus
from app.core.local import LocalGames, LocalOptions


def state(*darts, status="Throw", event="Throw detected"):
    data = {"running": True, "connected": True, "status": status, "event": event, "numThrows": len(darts)}
    if darts:
        data["throws"] = [
            {"segment": {"number": number, "multiplier": multiplier}} for number, multiplier in darts
        ]
    return data


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110 - Observe state without production-only test hooks.
            await asyncio.sleep(0.01)


def test_captured_v2_throws_normalize_and_reconcile_the_final_takeout_snapshot(tmp_path):
    packets = json.loads((Path(__file__).parent / "fixtures/local-v2-state.json").read_text(encoding="utf-8"))
    feed = LocalBoardConnection(None)
    game = LocalGames(tmp_path, None, EventBus())
    game.start(LocalOptions(players=["Alice", "Bob"]))
    for packet in packets:
        game.consume_board(feed.parse_message(json.dumps(packet)))
    assert game.saved.frame.game_scores == [422, 501]
    darts = [event for event in game.bus.normalized_history if event.event == "throw"]
    assert [event.data["dart"]["segment"]["name"] for event in darts] == ["T20", "S19"]
    assert all(event.data["play_source"] == "local" for event in darts)
    game.consume_board(packets[-1]["data"])
    # Partial physical removal preserves the reconciled visit.
    game.consume_board(state((19, 1), status="Takeout in progress", event="Takeout started"))
    assert game.saved.frame.game_scores == [422, 501]
    # A clear-board control snapshot completes the captured short visit.
    game.consume_board(state(event="Takeout finished"))
    assert game.saved.frame.player == 1
    visits = [event for event in game.bus.normalized_history if event.event == "turn_end"]
    assert len(visits) == 1 and visits[0].data["score"] == 79


async def test_local_websocket_feeds_normalized_throws_corrections_and_takeout(tmp_path):
    clients = []

    async def connected(socket):
        assert socket.request.path == "/api/events?type=state"
        clients.append(socket)
        await socket.wait_closed()

    async with serve(connected, "127.0.0.1", 0) as server:
        url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"

        def read(request):
            assert str(request.url) == f"{url}/api/state"
            return httpx.Response(200, json=state(event="Started"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(read)) as http:
            game = LocalGames(tmp_path, http, EventBus(), url)
            game.start(LocalOptions(players=["Alice", "Bob"]))
            task = asyncio.create_task(game.run())
            try:
                await until(lambda: game.armed)
                assert game.status()["detector"]["transport"] == "websocket"
                assert not game.bus.normalized_history or not any(
                    event.event == "throw" for event in game.bus.normalized_history
                )
                for data in [state((20, 3)), state((20, 3)), state((5, 1))]:
                    await clients[0].send(json.dumps({"type": "state", "data": data}))
                await until(lambda: game.saved.frame.game_scores == [496, 501])
                names = [event.event for event in game.bus.normalized_history]
                assert names.count("throw") == 1
                assert names.count("throw_corrected") == 1
                dart = next(event for event in game.bus.normalized_history if event.event == "throw")
                assert dart.data["play_source"] == "local"
                await clients[0].send(
                    json.dumps(
                        {
                            "type": "state",
                            "data": state(status="Takeout in progress", event="Takeout started"),
                        }
                    )
                )
                await clients[0].send(json.dumps({"type": "state", "data": state(event="Takeout finished")}))
                await until(lambda: game.saved.frame.player == 1)
                assert game.saved.frame.game_scores == [496, 501]
                assert len(game.saved.frame.turns) == 2
                assert sum(event.event == "takeout_finished" for event in game.bus.normalized_history) == 1
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert game.connection.transport is None
            await clients[0].wait_closed()


async def test_http_fallback_and_connection_cleanup(monkeypatch):
    monkeypatch.setattr("app.core.autodarts.local.connect", AsyncMock(side_effect=OSError))
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, json=state() if len(requests) == 1 else state((20, 3)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        connection = LocalBoardConnection(http, "http://192.0.2.1:3180")
        async with connection.session() as states:
            assert connection.transport == "polling"
            assert (await anext(states))["numThrows"] == 0
            assert (await anext(states))["numThrows"] == 1
        assert connection.transport is None
        assert connection.received == 2
        assert requests == ["http://192.0.2.1:3180/api/state"] * 2


async def test_socket_reconnect_preserves_a_visit_and_waits_for_clear(tmp_path):
    clients = []
    baseline = state()

    async def connected(socket):
        clients.append(socket)
        await socket.wait_closed()

    async with serve(connected, "127.0.0.1", 0) as server:
        url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=baseline))
        ) as http:
            game = LocalGames(tmp_path, http, EventBus(), url)
            game.start(LocalOptions(players=["Alice", "Bob"]))
            task = asyncio.create_task(game.run())
            try:
                await until(lambda: game.armed)
                baseline = state((20, 3))
                await clients[0].send(json.dumps({"type": "state", "data": baseline}))
                await until(lambda: game.saved.frame.game_scores == [441, 501])
                await clients[0].close()
                await until(lambda: len(clients) == 2 and game.detector_state == "waiting_for_clear")
                assert not game.armed
                assert game.saved.frame.game_scores == [441, 501]
                assert sum(event.event == "throw" for event in game.bus.normalized_history) == 1
                baseline = state(event="Takeout finished")
                await clients[1].send(json.dumps({"type": "state", "data": baseline}))
                await until(lambda: game.board["numThrows"] == 0)
                assert game.saved.frame.player == 0
                game.next_turn()
                assert game.saved.frame.player == 1
                assert game.armed
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_initial_websocket_backlog_is_a_silent_baseline(tmp_path, monkeypatch):
    pending = asyncio.Queue()
    pending.put_nowait(json.dumps({"type": "state", "data": state((20, 3))}))
    socket = AsyncMock()
    socket.recv.side_effect = pending.get
    monkeypatch.setattr("app.core.autodarts.local.connect", AsyncMock(return_value=socket))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=state()))
    ) as http:
        connection = LocalBoardConnection(http)
        game = LocalGames(tmp_path, http, EventBus())
        game.start(LocalOptions())
        async with connection.session() as states:
            game.consume_board(await anext(states))
            assert not game.armed
            assert game.saved.frame.game_scores == [501]
            assert not any(event.event == "throw" for event in game.bus.normalized_history)
        socket.close.assert_awaited_once()


@pytest.mark.parametrize(
    "payload", ["invalid json", '{"type":"state","data":null}', '{"type":"state","data":{}}']
)
def test_invalid_stream_payload_does_not_become_a_board_state(payload):
    connection = LocalBoardConnection(None)
    with pytest.raises(ConnectionProblem):
        connection.parse_message(payload)
    assert connection.invalid_messages == 1
    assert connection.received == 0
    assert connection.parse_message('{"type":"stats","data":{}}') is None


async def test_remote_detection_does_not_depend_on_a_local_binary():
    def respond(request):
        assert str(request.url) == "http://192.0.2.1:3180/api/state"
        return httpx.Response(200, json=state())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        detection = LocalDetection(http, "http://192.0.2.1:3180")
        await detection.check({"status": "nofile"})
        assert detection.status()["detecting"]


@pytest.mark.parametrize(
    "url",
    [
        "ws://localhost:3180",
        "http://user:secret@localhost",
        "http://localhost/path",
        "http://localhost?token=secret",
        "http://localhost:invalid",
    ],
)
def test_detector_origin_cannot_include_credentials_paths_or_invalid_ports(url, tmp_path):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, data_dir=tmp_path, local_autodarts_url=url)


def test_remote_detector_setting_uses_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("OCHE_CORE_LOCAL_AUTODARTS_URL", "http://192.0.2.1:3180/")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.local_autodarts_url == "http://192.0.2.1:3180"


async def test_events_monitor_reads_local_frames_without_starting_or_normalizing_a_game(tmp_path):
    clients = []

    async def connected(socket):
        clients.append(socket)
        await socket.wait_closed()

    async with serve(connected, "127.0.0.1", 0) as server:
        url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=state()))
        ) as http:
            game = LocalGames(tmp_path, http, EventBus(), url)
            game.monitor()
            task = asyncio.create_task(game.run())
            try:
                await until(lambda: bool(game.bus.raw_history))
                await clients[0].send(json.dumps({"type": "state", "data": state((20, 3))}))
                await until(lambda: game.bus.raw_history[-1]["data"]["numThrows"] == 1)
                raw = game.bus.raw_history[-1]
                assert raw["source"] == "autodarts-local" and raw["topic"] == "board.state"
                assert not game.active and game.saved is None
                assert not game.bus.normalized_history
                game.monitor_until = 0
                await clients[0].send(json.dumps({"type": "state", "data": state()}))
                await until(lambda: game.connection.transport is None)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
