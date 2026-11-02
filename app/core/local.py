"""Local X01 games, using the same match frames and normalizer as cloud play."""

import asyncio
import json
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from websockets.exceptions import ConnectionClosed

from app.core import bots
from app.core.board.detection import BOARD_URL
from app.core.board.local import LocalBoardConnection
from app.core.errors import ConnectionProblem
from app.core.events import Coords, Dart, EventBus, EventNormalizer, MatchFrame, Player, Segment, Turn
from app.core.storage import write_private_json

BOARD_ID = "oche-local"


class LocalPlayer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)
    bot: int | None = Field(default=None, ge=bots.LEVELS.start, le=bots.LEVELS.stop - 1, strict=True)
    # An automatic bot follows the human players' average instead of a fixed level.
    auto: Literal["easy", "medium", "hard"] | None = None

    @model_validator(mode="after")
    def one_bot_kind(self) -> "LocalPlayer":
        if self.bot is not None and self.auto is not None:
            raise ValueError("Choose a bot level or automatic difficulty, not both.")
        return self

    @property
    def is_bot(self) -> bool:
        return self.bot is not None or self.auto is not None


class LocalOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    players: list[LocalPlayer] = Field(
        default_factory=lambda: [LocalPlayer(name="Player 1")], min_length=1, max_length=8
    )
    starting_score: Literal[301, 501] = 501
    double_out: bool = True
    input: Literal["autodarts", "manual"] = "autodarts"
    bot_speed: Literal["slow", "normal", "fast"] = "normal"
    show_board: bool = True
    random_names: bool = True

    @field_validator("players", mode="before")
    @classmethod
    def player_names(cls, players: object) -> object:
        # Plain names are human players, as saved by earlier releases.
        if isinstance(players, list):
            return [{"name": item} if isinstance(item, str) else item for item in players]
        return players


class LocalDart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int = Field(ge=0, le=25, strict=True)
    multiplier: int = Field(ge=1, le=3, strict=True)

    def segment(self) -> Segment:
        if self.number not in {*range(21), 25} or (self.number == 25 and self.multiplier == 3):
            raise ConnectionProblem("Choose 1–20, bull (25), double bull (50), or miss (0).")
        prefix = {1: "S", 2: "D", 3: "T"}[self.multiplier]
        return Segment(
            number=self.number,
            multiplier=self.multiplier,
            name=f"{prefix}{self.number}" if self.number else "MISS",
        )


class SavedLocalGame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: LocalOptions
    frame: MatchFrame
    visit_start: int = Field(ge=1, le=501)
    active: bool = True

    @model_validator(mode="after")
    def valid_frame(self):
        frame = self.frame
        if (
            not frame.turns
            or len(frame.turns) > 100
            or len(frame.game_scores) != len(frame.players)
            or not 0 <= frame.player < len(frame.players)
            or max(frame.winner, frame.game_winner) >= len(frame.players)
            or frame.turns[0].player_id != frame.players[frame.player].id
        ):
            raise ValueError("Invalid saved local game frame")
        return self


class LocalGames:
    def __init__(self, directory: Path, http: httpx.AsyncClient, bus: EventBus, board_url: str = BOARD_URL):
        self.path = directory / "local-game.json"
        self.http, self.bus = http, bus
        self.connection = LocalBoardConnection(http, board_url)
        self.monitor_until = 0.0
        self.feed_error: str | None = None
        self.normalizer = EventNormalizer(BOARD_ID, bus, play_source="local")
        self.saved: SavedLocalGame | None = None
        self.error: str | None = None
        self.detector_state = "stopped"
        self.detector_error: str | None = None
        self.connected = False
        self.armed = False
        self.board: dict | None = None
        self.detected: list[Segment] = []
        self.taking_out = False
        self.bot_due: float | None = None
        self.random = random.Random()
        self.lock = asyncio.Lock()
        try:
            if self.path.exists():
                self.saved = SavedLocalGame.model_validate_json(self.path.read_text(encoding="utf-8"))
                self._frame(snapshot=True)
                if self.active and self.saved.options.input == "manual":
                    self._event("board.state", {"status": "Ready", "numThrows": 0}, snapshot=True)
        except (OSError, ValueError, ValidationError):
            self.saved = None
            self.error = "Cannot load the saved local game. Start a new game to replace it."

    @property
    def active(self) -> bool:
        return bool(self.saved and self.saved.active)

    @property
    def bot_level(self) -> float | None:
        """Level of the bot whose visit it is, or None for a human."""
        if not self.active:
            return None
        player = self.saved.options.players[self.saved.frame.player]
        if player.auto:
            return bots.auto_level(player.auto, self.human_visits())
        return player.bot

    def human_visits(self) -> list[int]:
        """Points of the finished human visits in this game; a bust scores nothing."""
        frame = self.saved.frame
        humans = {
            frame.players[index].id
            for index, player in enumerate(self.saved.options.players)
            if not player.is_bot
        }
        return [
            0 if turn.busted else turn.points or 0
            for turn in frame.turns
            if turn.player_id in humans and turn.finished_at
        ]

    def _human_turn(self) -> None:
        if self.bot_level is not None:
            raise ConnectionProblem("Wait for the bot to finish its visit.")

    def monitor(self) -> None:
        """Keep the read-only local feed alive while the Events screen polls."""
        self.monitor_until = time.monotonic() + 10

    def wants_feed(self) -> bool:
        return bool(
            (self.active and self.saved.options.input == "autodarts")
            or self.monitor_until > time.monotonic()
            or (self.bus.recorder and self.bus.recorder.enabled)
            or self.bus.raw_queues
        )

    def scoring_key(self) -> tuple | None:
        if self.active and self.saved.options.input == "autodarts" and self.bot_level is None:
            return self.saved.frame.id, self.saved.frame.turns[0].id
        return None

    def _event(self, event: str, data: dict, *, snapshot: bool = False) -> None:
        raw = self.bus.publish("autodarts-local", event, data, snapshot=snapshot)
        self.normalizer.consume(raw)

    def _frame(self, snapshot: bool = False) -> None:
        if self.saved:
            self.normalizer.select(self.saved.frame.id)
            self._event("match.state", self.saved.frame.model_dump(by_alias=True), snapshot=snapshot)

    def _save(self, saved: SavedLocalGame) -> None:
        # Commit to disk before publishing a change to integrations.
        write_private_json(self.path, saved.model_dump(mode="json", by_alias=True))
        self.saved, self.error = saved, None

    def game_state(self) -> dict:
        available = self.active and (
            self.saved.options.input == "manual"
            or self.bot_level is not None
            or (self.connected and self.armed)
        )
        state = self.normalizer.current_state(bool(available))
        # Keep saved scores in the local controls while integrations wait for detection.
        if self.active and not available:
            state = self.normalizer.current_state(True)
            state.update(
                available=False, phase="waiting", reason=self.detector_error or "Waiting for local detection"
            )
        return {**state, "source": "local"}

    def status(self) -> dict:
        return {
            "active": self.active,
            "error": self.error,
            "options": self.saved.options.model_dump() if self.saved else LocalOptions().model_dump(),
            "game": self.game_state() if self.saved else None,
            "frame": self.saved.frame.model_dump(by_alias=True) if self.saved else None,
            "feed": {**self.connection.status(), "monitoring": self.wants_feed(), "error": self.feed_error},
            "detector": {
                "state": self.detector_state,
                "error": self.detector_error,
                "connected": self.connected,
                "armed": self.armed,
                **self.connection.status(),
            },
        }

    def start(self, options: LocalOptions) -> None:
        if self.active:
            raise ConnectionProblem("End the current local game before starting another.")
        players = [Player(id=str(uuid4()), name=player.name, boardId=BOARD_ID) for player in options.players]
        frame = MatchFrame(
            id=str(uuid4()),
            variant="X01",
            players=players,
            turns=[
                Turn(id=str(uuid4()), playerId=players[0].id, round=0, points=0, score=options.starting_score)
            ],
            set=0,
            leg=0,
            round=0,
            player=0,
            gameScores=[options.starting_score] * len(players),
            settings={
                "baseScore": options.starting_score,
                "outMode": "Double" if options.double_out else "Straight",
            },
        )
        self._save(SavedLocalGame(options=options, frame=frame, visit_start=options.starting_score))
        self.bot_due = None
        self.reset_detector()
        self._event("board.matches", {"event": "start", "id": frame.id})
        self._frame(snapshot=True)
        if options.input == "manual":
            self._event("board.state", {"status": "Ready", "numThrows": 0})

    def stop(self) -> None:
        if not self.active:
            return
        saved = self.saved.model_copy(deep=True)
        saved.active = False
        self._save(saved)
        self._event("board.matches", {"event": "end", "id": saved.frame.id})
        self.reset_detector()

    def reset_detector(self) -> None:
        self.connected = self.armed = self.taking_out = False
        self.detected = []
        self.board = None
        self.detector_state, self.detector_error = "waiting", None

    def _require_game(self) -> SavedLocalGame:
        if not self.active:
            raise ConnectionProblem("Start a local game first.")
        return self.saved.model_copy(deep=True)

    @staticmethod
    def _score(saved: SavedLocalGame) -> None:
        frame, remaining = saved.frame, saved.visit_start
        turn = frame.turns[0]
        turn.busted, turn.finished_at = False, None
        frame.winner = frame.game_winner = -1
        for dart in turn.throws:
            remaining -= dart.segment.number * dart.segment.multiplier
            if remaining < 0 or (
                saved.options.double_out
                and (remaining == 1 or (remaining == 0 and dart.segment.multiplier != 2))
            ):
                turn.busted = True
                remaining = saved.visit_start
                break
            if remaining == 0:
                frame.winner = frame.game_winner = frame.player
                break
        turn.points = saved.visit_start - remaining
        turn.score = remaining
        frame.game_scores[frame.player] = remaining
        if turn.busted or frame.winner >= 0 or len(turn.throws) == 3:
            turn.finished_at = datetime.now(UTC).isoformat()

    def throw(
        self, segment: Segment, *, detected: bool = False, bot: bool = False, coords: Coords | None = None
    ) -> None:
        saved = self._require_game()
        if not bot:
            self._human_turn()
        if not (detected or bot) and saved.options.input != "manual":
            raise ConnectionProblem("Manual darts are only available in manual input mode.")
        turn = saved.frame.turns[0]
        if saved.frame.winner >= 0 or turn.finished_at:
            raise ConnectionProblem("This turn is finished. Remove darts and continue.")
        turn.throws.append(Dart(id=str(uuid4()), segment=segment, coords=coords))
        self._score(saved)
        self._save(saved)
        self._event(
            "board.state", {"status": "Ready", "event": "Throw detected", "numThrows": len(turn.throws)}
        )
        self._frame()

    def undo(self) -> None:
        saved = self._require_game()
        self._human_turn()
        if saved.options.input != "manual":
            raise ConnectionProblem("Correct detected darts on the board; manual undo is disabled.")
        turn = saved.frame.turns[0]
        if not turn.throws:
            raise ConnectionProblem("There is no dart to undo in this turn.")
        turn.throws.pop()
        self._score(saved)
        self._save(saved)
        self._frame()
        self._event("board.state", {"status": "Ready", "numThrows": len(turn.throws)})

    def next_turn(self, *, detected: bool = False, bot: bool = False) -> None:
        saved = self._require_game()
        if not bot:
            self._human_turn()
        if (
            not (detected or bot)
            and saved.options.input == "autodarts"
            and not (
                self.connected
                and self.board
                and self.board.get("numThrows") == 0
                and self.board.get("running") is not False
                and self.board["status"].lower() in {"ready", "throw", "ready for throw"}
            )
        ):
            raise ConnectionProblem("Clear the board and reconnect detection before continuing.")
        frame, turn = saved.frame, saved.frame.turns[0]
        if frame.winner >= 0:
            raise ConnectionProblem("The game is finished. End it to start a new game.")
        if not turn.throws:
            raise ConnectionProblem("Throw at least one dart before continuing.")
        turn.finished_at = turn.finished_at or datetime.now(UTC).isoformat()
        frame.player = (frame.player + 1) % len(frame.players)
        if frame.player == 0:
            frame.round += 1
        saved.visit_start = frame.game_scores[frame.player]
        frame.turns.insert(
            0,
            Turn(
                id=str(uuid4()),
                playerId=frame.players[frame.player].id,
                round=frame.round,
                points=0,
                score=saved.visit_start,
            ),
        )
        frame.turns = frame.turns[:100]
        self._save(saved)
        self._event("board.events", {"event": "Takeout started"})
        self._event("board.events", {"event": "Takeout finished"})
        self._frame()
        self._event("board.state", {"status": "Ready", "numThrows": 0})
        self.detected = []
        self.taking_out = False
        if self.connected:
            self.armed = True

    @staticmethod
    def parse_board(data: object) -> tuple[dict, list[Segment], list[Coords | None]]:
        if not isinstance(data, dict) or not isinstance(data.get("status"), str):
            raise ConnectionProblem("The local detector returned an invalid board state.")
        count = data.get("numThrows")
        # The observed v2.0.2 board omits throws on an empty board.
        throws = data.get("throws", [] if type(count) is int and count == 0 else None)
        if not isinstance(throws, list):
            raise ConnectionProblem(
                "This Autodarts local API does not expose scored throws. "
                "Scored dart segments are required for automatic local games."
            )
        if type(count) is not int or not 0 <= count <= 3 or len(throws) != count:
            raise ConnectionProblem("The local detector returned an inconsistent throw count.")
        segments, positions = [], []
        for item in throws:
            if not isinstance(item, dict) or not isinstance(item.get("segment"), dict):
                raise ConnectionProblem("The local detector returned an invalid dart.")
            raw = item["segment"]
            # Detection can represent a miss using multiplier zero.
            if (
                type(raw.get("number")) is int
                and type(raw.get("multiplier")) is int
                and raw["number"] == 0
                and raw["multiplier"] == 0
            ):
                segment = Segment(number=0, multiplier=0, name="MISS")
            else:
                segment = LocalDart(number=raw.get("number"), multiplier=raw.get("multiplier")).segment()
            segments.append(segment)
            positions.append(Dart(id="detected", segment=segment, coords=item.get("coords")).coords)
        return data, segments, positions

    def consume_board(self, data: object) -> None:
        board, segments, positions = self.parse_board(data)
        previous_action = str((self.board or {}).get("event", "")).lower()
        self.board, self.connected = board, True
        self.detector_error = None
        status = board["status"].lower()
        action = str(board.get("event", "")).lower()
        if board.get("running") is False or status in {"stopped", "stopping"}:
            self.armed = False
            self.detector_state = "stopped"
            self.detector_error = "Local Autodarts detection is stopped."
            self._event("board.state", board)
            return
        if action != previous_action and action in {
            "manual reset",
            "calibration started",
            "calibration finished",
        }:
            self._event("board.events", {"event": board["event"]})
        ready = status in {"ready", "throw", "ready for throw"}
        if not self.armed:
            self.detector_state = "waiting_for_clear"
            if not segments and ready and not self.saved.frame.turns[0].throws:
                self.armed, self.detector_state = True, "connected"
                self.detected = []
                self._event("board.state", {**board, "event": ""}, snapshot=True)
            else:
                self.detector_error = "Clear the board. If a visit was interrupted, select Next turn."
            return
        self.detector_state = "connected"
        if status in {"takeout", "takeout in progress", "removing darts"} or action == "takeout started":
            self.taking_out = True
            # A poll may first see the last dart in the full takeout snapshot.
            # Append only a strict extension of known darts; removal snapshots
            # and changing segments during takeout must never undo a visit.
            if len(segments) > len(self.detected) and segments[: len(self.detected)] == self.detected:
                for index in range(len(self.saved.frame.turns[0].throws), len(segments)):
                    if not self.saved.frame.turns[0].finished_at and self.saved.frame.winner < 0:
                        self.throw(segments[index], detected=True, coords=positions[index])
                self.detected = segments
            self._event("board.state", board)
            return
        if not segments and self.detected:
            if ready and (self.taking_out or action == "takeout finished"):
                if self.saved.frame.winner < 0:
                    self.next_turn(detected=True)
                else:
                    self._event("board.events", {"event": "Takeout finished"})
                    self.detected = []
            else:
                self.armed = False
                self.detector_error = "Detection reset unexpectedly. Clear the board and select Next turn."
            return
        if not ready:
            self._event("board.state", board)
            return
        # Repeated snapshots are inert. Corrections retain dart IDs and are normalized as edits.
        saved = self._require_game()
        old = self.detected
        if len(segments) < len(old):
            self.armed = False
            self.detector_error = "Detection lost a dart. Clear the board and select Next turn."
            return
        if old != segments[: len(old)]:
            for index, segment in enumerate(segments[: len(saved.frame.turns[0].throws)]):
                saved.frame.turns[0].throws[index].segment = segment
                saved.frame.turns[0].throws[index].coords = positions[index]
            self._score(saved)
            self._save(saved)
            self._frame()
        for index in range(len(self.saved.frame.turns[0].throws), len(segments)):
            if not self.saved.frame.turns[0].finished_at and self.saved.frame.winner < 0:
                self.throw(segments[index], detected=True, coords=positions[index])
        self.detected = segments
        self._event("board.state", board)

    def bot_step(self, now: float) -> None:
        """Throw the next bot dart, or hand over after a finished bot visit, once it is due."""
        level = self.bot_level
        if level is None or self.saved.frame.winner >= 0:
            self.bot_due = None
            return
        turn = self.saved.frame.turns[0]
        if self.bot_due is None:
            dart, handover = bots.SPEEDS[self.saved.options.bot_speed]
            self.bot_due = now + (handover if turn.finished_at else dart)
            return
        if now < self.bot_due:
            return
        self.bot_due = None
        if turn.finished_at:
            self.next_turn(bot=True)
            return
        aim = bots.target(turn.score, 3 - len(turn.throws), self.saved.options.double_out)
        segment, coords = bots.throw(aim, level, self.random)
        self.throw(segment, bot=True, coords=coords)

    async def run_bots(self) -> None:
        while True:
            async with self.lock:
                try:
                    self.bot_step(time.monotonic())
                except (OSError, ValueError, ConnectionProblem) as exc:
                    self.bot_due = None
                    self.error = (
                        str(exc) if isinstance(exc, ConnectionProblem) else "Cannot save the bot's dart."
                    )
            await asyncio.sleep(0.1)

    async def run(self) -> None:
        while True:
            if self.wants_feed():
                try:
                    async with self.connection.session() as states:
                        while self.wants_feed():
                            key = self.scoring_key()
                            data = await anext(states)
                            async with self.lock:
                                self.feed_error = None
                                self.bus.record_raw(
                                    json.dumps(
                                        {
                                            "source": "autodarts-local",
                                            "channel": "local",
                                            "topic": "board.state",
                                            "type": "state",
                                            "data": data,
                                        }
                                    )
                                )
                                if key is not None and self.scoring_key() == key:
                                    self.consume_board(data)
                except (httpx.HTTPError, OSError, ValueError, ConnectionClosed, ConnectionProblem) as exc:
                    self.feed_error = (
                        str(exc)
                        if isinstance(exc, ConnectionProblem)
                        else ("Cannot read local detection. Check the vision engine's page in Integrations.")
                    )
                    if self.scoring_key() is not None:
                        self.connected = self.armed = False
                        self.detector_state = "unavailable"
                        self.detector_error = self.feed_error
            await asyncio.sleep(0.2 if self.active else 1)
