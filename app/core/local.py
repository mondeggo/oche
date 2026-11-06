"""Local darts games, using the same match frames and normalizer as cloud play."""

import asyncio
import json
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from websockets.exceptions import ConnectionClosed

from app.core import bots, games
from app.core import football as football_rules
from app.core.board import board_words
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
    # Games other than X01 have no scoring average, so their bots play at a fixed difficulty.
    difficulty: Literal["easy", "medium", "hard"] | None = None

    @model_validator(mode="after")
    def one_bot_kind(self) -> "LocalPlayer":
        if sum(kind is not None for kind in (self.bot, self.auto, self.difficulty)) > 1:
            raise ValueError("Choose a bot level or automatic difficulty, not both.")
        return self

    @property
    def is_bot(self) -> bool:
        return self.bot is not None or self.auto is not None or self.difficulty is not None


class LocalOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    players: list[LocalPlayer] = Field(
        default_factory=lambda: [LocalPlayer(name="Player 1")], min_length=1, max_length=8
    )
    mode: Literal["x01", "football", "cricket", "atc", "countup", "bobs27", "shanghai"] = "x01"
    goals_to_win: Literal[3, 5] = 5
    football_format: Literal["goals", "rounds"] = "rounds"
    football_rounds: int = Field(default=10, ge=2, le=200)
    football_tiebreak: Literal["penalties", "golden", "draw"] = "penalties"
    football_stoppage: bool = False
    football_beginner: bool = True
    football_kickoff: Literal["outer", "bull", "inner"] = "inner"
    football_own_goals: bool = True
    cricket_scoring: Literal["standard", "cutthroat"] = "standard"
    atc_ring: Literal["any", "double", "triple"] = "any"
    atc_bull: bool = False
    starting_score: Literal[301, 501] = 501
    double_out: bool = True
    input: Literal["autodarts", "manual"] = "autodarts"
    bot_speed: Literal["slow", "normal", "fast"] = "normal"
    show_board: bool = True
    random_names: bool = False

    @model_validator(mode="after")
    def football_players(self) -> "LocalOptions":
        if self.mode == "football" and len(self.players) != 2:
            raise ValueError("Football requires exactly two players.")
        return self

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

    bed: Literal["inner", "outer"] | None = None
    distance_mm: float | None = Field(default=None, ge=0, le=1700, allow_inf_nan=False)
    # Where a manual dart was placed on the drawn board, in millimetres with y up, as for a bull-up.
    x_mm: float | None = Field(default=None, ge=-300, le=300, allow_inf_nan=False)
    y_mm: float | None = Field(default=None, ge=-300, le=300, allow_inf_nan=False)

    @model_validator(mode="after")
    def whole_position(self) -> "LocalDart":
        if (self.x_mm is None) != (self.y_mm is None):
            raise ValueError("Give both x_mm and y_mm, or neither.")
        return self

    def coordinates(self) -> Coords | None:
        if self.x_mm is not None:
            return Coords(x=self.x_mm / 170, y=self.y_mm / 170)
        if self.distance_mm is not None:
            return Coords(x=0, y=self.distance_mm / 170)
        if self.bed and 1 <= self.number <= 20 and self.multiplier == 1:
            x, y = football_rules.position(f"{'I' if self.bed == 'inner' else 'O'}{self.number}")
            return Coords(x=x, y=y)
        return None

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
    visit_start: int = Field(ge=-10000, le=10000)
    football: football_rules.FootballState | None = None
    football_visit: football_rules.FootballState | None = None
    football_pending: dict | None = None
    football_confirmed: dict[int, dict] = Field(default_factory=dict)
    # Cricket, Around the Clock, Count Up, Bob's 27 and Shanghai, now and at the start of the visit.
    game: games.TargetState | None = None
    game_visit: games.TargetState | None = None
    active: bool = True
    tournament_id: str | None = None
    tournament_match_id: str | None = None

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
        if self.options.mode == "football" and (
            not self.football
            or not self.football_visit
            or len(frame.players) != 2
            or frame.variant != "Football"
            or frame.game_scores != self.football.goals
        ):
            raise ValueError("Invalid saved football state")
        if self.options.mode in games.MODES and (
            not self.game
            or not self.game_visit
            or frame.variant != games.VARIANTS[self.options.mode]
            or frame.game_scores != self.game.scores
        ):
            raise ValueError("Invalid saved game state")
        return self


class LocalGames:
    def __init__(
        self,
        directory: Path,
        http: httpx.AsyncClient,
        bus: EventBus,
        board_url: str | Callable[[], str] = BOARD_URL,
    ):
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
        if player.difficulty:
            return bots.DIFFICULTY[player.difficulty]
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
            (
                sum(dart.segment.number * dart.segment.multiplier for dart in turn.throws)
                if self.saved.options.mode != "x01"
                else 0
                if turn.busted
                else turn.points or 0
            )
            for turn in frame.turns
            if turn.player_id in humans and turn.finished_at
        ]

    @staticmethod
    def _over(saved: SavedLocalGame) -> bool:
        """A win, or a finished game without one, such as a draw."""
        return (
            saved.frame.winner >= 0
            or bool(saved.football and saved.football.phase == "finished")
            or bool(saved.game and saved.game.finished)
        )

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
            "finished": bool(self.saved and self._over(self.saved)),
            "targets": (
                {
                    **self.saved.game.model_dump(),
                    "rounds": games.ROUNDS.get(self.saved.options.mode),
                    "sequence": games.sequence(self.saved.options),
                    "target": games.target(
                        self.saved.game, self.saved.frame.player, self.saved.frame.round, self.saved.options
                    ),
                }
                if self.saved and self.saved.game
                else None
            ),
            "tournament": (
                {"id": self.saved.tournament_id, "match_id": self.saved.tournament_match_id}
                if self.saved and self.saved.tournament_id
                else None
            ),
            "football": (
                {
                    **self.saved.football.model_dump(),
                    "neighbours": {
                        region: football_rules.neighbours(region, self.saved.options.football_beginner)
                        for region in football_rules.REGIONS
                    },
                    "clock": football_rules.clock(self.saved.football, self.saved.options),
                    "attacks": [football_rules.goal_for(self.saved.football, p) for p in (0, 1)],
                    "pending": self.saved.football_pending,
                    "targets": football_rules.playable_targets(
                        self.saved.football, self.saved.frame.player, self.saved.options
                    ),
                }
                if self.saved and self.saved.football
                else None
            ),
            "feed": {**self.connection.status(), "monitoring": self.wants_feed(), "error": self.feed_error},
            "detector": {
                "state": self.detector_state,
                "error": self.detector_error,
                "connected": self.connected,
                "armed": self.armed,
                **self.connection.status(),
            },
        }

    def start(
        self,
        options: LocalOptions,
        *,
        tournament_id: str | None = None,
        tournament_match_id: str | None = None,
    ) -> None:
        if self.active:
            raise ConnectionProblem("End the current local game before starting another.")
        players = [Player(id=str(uuid4()), name=player.name, boardId=BOARD_ID) for player in options.players]
        game = games.start(options, len(players)) if options.mode in games.MODES else None
        initial_score = (
            game.scores[0] if game else 0 if options.mode == "football" else options.starting_score
        )
        football = (
            football_rules.FootballState(
                phase="bullup",
                event="bullup",
                clock_seconds=football_rules.clock_schedule(options.football_rounds, self.random)
                if options.football_format == "rounds"
                else [],
                added=[self.random.randint(0, 2) for _ in range(2)]
                if options.football_stoppage and options.football_format == "rounds"
                else [0, 0],
            )
            if options.mode == "football"
            else None
        )
        frame = MatchFrame(
            id=str(uuid4()),
            variant="Football" if options.mode == "football" else games.VARIANTS.get(options.mode, "X01"),
            players=players,
            turns=[Turn(id=str(uuid4()), playerId=players[0].id, round=0, points=0, score=initial_score)],
            set=0,
            leg=0,
            round=0,
            player=0,
            gameScores=list(game.scores) if game else [initial_score] * len(players),
            state=football.model_dump() if football else games.public_state(game, options) if game else {},
            settings={"goalsToWin": options.goals_to_win}
            if options.mode == "football"
            else {}
            if game
            else {
                "baseScore": options.starting_score,
                "outMode": "Double" if options.double_out else "Straight",
            },
        )
        self._save(
            SavedLocalGame(
                options=options,
                frame=frame,
                visit_start=initial_score,
                football=football,
                football_visit=football.model_copy(deep=True) if football else None,
                game=game,
                game_visit=game.model_copy(deep=True) if game else None,
                tournament_id=tournament_id,
                tournament_match_id=tournament_match_id,
            )
        )
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
    def _score(saved: SavedLocalGame, final: bool = False) -> None:
        if saved.options.mode == "football":
            LocalGames._score_football(saved)
            return
        if saved.game:
            LocalGames._score_game(saved, final)
            return
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

    @staticmethod
    def _score_football(saved: SavedLocalGame) -> None:
        """Replay this visit from its snapshot so undo and detector corrections are exact."""
        frame, options = saved.frame, saved.options
        turn = frame.turns[0]
        current = saved.football_visit.model_copy(deep=True)
        current.goal_by = None
        turn.busted, turn.finished_at = False, None
        frame.winner = frame.game_winner = -1
        for dart in turn.throws:
            football_rules.strike(current, dart, frame.player, options)
            if current.goal_by is not None or current.phase == "finished":
                break
        saved.football = current
        frame.state = current.model_dump()
        frame.game_scores = list(current.goals)
        turn.score = current.goals[frame.player]
        turn.points = turn.score - saved.football_visit.goals[frame.player]
        frame.winner = frame.game_winner = current.winner
        limit = 1 if saved.football_visit.phase in {"bullup", "penalties"} else 3
        if current.goal_by is not None or current.phase == "finished" or len(turn.throws) >= limit:
            turn.finished_at = datetime.now(UTC).isoformat()

    @staticmethod
    def _score_game(saved: SavedLocalGame, final: bool) -> None:
        """Replay this visit from its snapshot; ``final`` closes a visit of fewer than three darts."""
        frame, turn = saved.frame, saved.frame.turns[0]
        current, points = games.score(
            saved.game_visit,
            [dart.segment for dart in turn.throws],
            frame.player,
            frame.round,
            saved.options,
            final,
        )
        saved.game = current
        frame.state = games.public_state(current, saved.options)
        frame.game_scores = list(current.scores)
        frame.winner = frame.game_winner = current.winner
        turn.busted = False
        turn.points = points
        # The caller announces a Cricket visit's points from the turn total.
        turn.score = points if saved.options.mode == "cricket" else current.scores[frame.player]
        done = current.finished or final or len(turn.throws) >= 3
        turn.finished_at = (turn.finished_at or datetime.now(UTC).isoformat()) if done else None

    def throw(
        self, segment: Segment, *, detected: bool = False, bot: bool = False, coords: Coords | None = None
    ) -> None:
        saved = self._require_game()
        if not bot:
            self._human_turn()
        if not (detected or bot) and saved.options.input != "manual":
            raise ConnectionProblem("Manual darts are only available in manual input mode.")
        turn = saved.frame.turns[0]
        if self._over(saved) or turn.finished_at:
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
        if saved.tournament_id and saved.frame.winner >= 0:
            raise ConnectionProblem("A completed tournament result cannot be changed.")
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
        if self._over(saved):
            raise ConnectionProblem("The game is finished. End it to start a new game.")
        if not turn.throws:
            raise ConnectionProblem("Throw at least one dart before continuing.")
        turn.finished_at = turn.finished_at or datetime.now(UTC).isoformat()
        if saved.football_pending:
            raise ConnectionProblem("Confirm the hit near the wire before continuing.")
        if saved.game:
            # A short visit is scored as finished, which may end the game.
            self._score(saved, final=True)
            following = (
                None
                if saved.game.finished
                else games.following(saved.game, frame.player, frame.round, saved.options)
            )
            if following is None:
                self._save(saved)
                self._frame()
                self.detected = []
                self.taking_out = False
                return
            frame.player, frame.round = following
            saved.game_visit = saved.game.model_copy(deep=True)
            frame.state = games.public_state(saved.game, saved.options)
        elif saved.football:
            frame.player = football_rules.advance(saved.football, frame.player, saved.options)
            frame.round = saved.football.rounds
            frame.winner = frame.game_winner = saved.football.winner
            saved.football_visit = saved.football.model_copy(deep=True)
            frame.state = saved.football.model_dump()
            saved.football_confirmed = {}
        else:
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
        """Read a board state, returned with word names and its dart count.

        Autodarts 2.1.1 and OcheCore list flat throws in camelCase states without
        ``numThrows``; other detectors nest ``segment`` and ``coords`` and count them.
        """
        if not isinstance(data, dict) or not isinstance(data.get("status"), str):
            raise ConnectionProblem("The local detector returned an invalid board state.")
        count = data.get("numThrows")
        # An earlier v2.0.2 board omitted throws on an empty board.
        throws = data.get("throws", [] if type(count) is int and count == 0 else None)
        if not isinstance(throws, list):
            raise ConnectionProblem(
                "This Autodarts local API does not expose scored throws. "
                "Scored dart segments are required for automatic local games."
            )
        if "numThrows" not in data:
            count = len(throws)
        if type(count) is not int or not 0 <= count <= 3 or len(throws) != count:
            raise ConnectionProblem("The local detector returned an inconsistent throw count.")
        board = {**data, "status": board_words(data["status"]), "numThrows": count}
        if isinstance(data.get("event"), str):
            board["event"] = board_words(data["event"])
            # Autodarts lists a visit's first dart under the reset before it and renames
            # the event a moment later. A reset clears the board, so this is a throw.
            if board["event"] == "Manual reset" and throws:
                board["event"] = "Throw detected"
        segments, positions = [], []
        for item in throws:
            if not isinstance(item, dict):
                raise ConnectionProblem("The local detector returned an invalid dart.")
            if isinstance(item.get("segment"), dict):
                raw = item["segment"]
                number, multiplier, coords = raw.get("number"), raw.get("multiplier"), item.get("coords")
                missed = number == 0 and multiplier == 0
            elif "segmentNumber" in item:
                # Positions are fractions of 170 mm, +x toward 6 and +y toward 20, like Coords.
                number, multiplier = item.get("segmentNumber"), item.get("multiplier")
                x, y = item.get("coordsX"), item.get("coordsY")
                coords = {"x": x, "y": y} if x is not None and y is not None else None
                # A miss is bed Outside with multiplier zero, numbered after the segment beside it.
                missed = multiplier == 0 or item.get("bed") == "Outside"
            else:
                raise ConnectionProblem("The local detector returned an invalid dart.")
            # Detection can represent a miss using multiplier zero.
            if missed and type(number) is int and type(multiplier) is int and 0 <= number <= 25:
                segment = Segment(number=0, multiplier=0, name="MISS")
            else:
                segment = LocalDart(number=number, multiplier=multiplier).segment()
            segments.append(segment)
            positions.append(Dart(id="detected", segment=segment, coords=coords).coords)
        return board, segments, positions

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
            self.detector_error = "Local detection is stopped."
            self._event("board.state", board)
            return
        # Autodarts also resets an empty board about 2 s after each takeout; that is not news.
        if action != previous_action and (
            action in {"calibration started", "calibration finished"}
            or (action == "manual reset" and self.detected)
        ):
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
        if self.saved.football_pending and not segments and action != "manual reset":
            self.detector_error = "Confirm the hit near the wire before continuing."
            return
        if self.saved.football and segments and self.saved.football.phase != "bullup":
            preview = self.saved.football_visit.model_copy(deep=True)
            for index, (segment, coords) in enumerate(zip(segments, positions, strict=True)):
                if coords is not None:
                    segment = segments[index] = bots.segment_at(coords.x * 170, coords.y * 170)
                raw = {"segment": segment.model_dump(), "coords": coords.model_dump() if coords else None}
                confirmed = self.saved.football_confirmed.get(index)
                if confirmed and confirmed["raw"] == raw:
                    segments[index] = Segment.model_validate(confirmed["segment"])
                    positions[index] = (
                        Coords.model_validate(confirmed["coords"]) if confirmed["coords"] else None
                    )
                elif football_rules.near_wire(coords):
                    saved = self._require_game()
                    saved.football_pending = {"index": index, "board": board, **raw}
                    self._save(saved)
                    self.detector_error = "Confirm the hit near the wire before continuing."
                    return
                football_rules.strike(
                    preview,
                    Dart(id="preview", segment=segments[index], coords=positions[index]),
                    self.saved.frame.player,
                    self.saved.options,
                )
                if (
                    preview.goal_by is not None
                    or preview.phase == "finished"
                    or self.saved.football_visit.phase == "penalties"
                ):
                    break
            if self.saved.football_pending:
                saved = self._require_game()
                saved.football_pending = None
                self._save(saved)
        if status in {"takeout", "takeout in progress", "removing darts"} or action == "takeout started":
            self.taking_out = True
            # A poll may first see the last dart in the full takeout snapshot.
            # Append only a strict extension of known darts; removal snapshots
            # and changing segments during takeout must never undo a visit.
            if len(segments) > len(self.detected) and segments[: len(self.detected)] == self.detected:
                for index in range(len(self.saved.frame.turns[0].throws), len(segments)):
                    if not self.saved.frame.turns[0].finished_at and not self._over(self.saved):
                        self.throw(segments[index], detected=True, coords=positions[index])
                self.detected = segments
            self._event("board.state", board)
            return
        if not segments and self.detected:
            if ready and (self.taking_out or action == "takeout finished"):
                if not self._over(self.saved):
                    self.next_turn(detected=True)
                else:
                    self._event("board.events", {"event": "Takeout finished"})
                    self.detected = []
            elif ready and action == "manual reset":
                self._wipe_visit(board)
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
        position_changed = saved.options.mode == "football" and any(
            dart.coords != position
            for dart, position in zip(saved.frame.turns[0].throws, positions, strict=False)
        )
        if (old != segments[: len(old)] or position_changed) and not (
            saved.tournament_id and saved.frame.winner >= 0
        ):
            for index, segment in enumerate(segments[: len(saved.frame.turns[0].throws)]):
                saved.frame.turns[0].throws[index].segment = segment
                saved.frame.turns[0].throws[index].coords = positions[index]
            self._score(saved)
            self._save(saved)
            self._frame()
        for index in range(len(self.saved.frame.turns[0].throws), len(segments)):
            if not self.saved.frame.turns[0].finished_at and not self._over(self.saved):
                self.throw(segments[index], detected=True, coords=positions[index])
        self.detected = segments
        self._event("board.state", board)

    def _wipe_visit(self, board: dict) -> None:
        """The detector cleared its darts, for example when a hand came into view, or
        when Autodarts resets the board 2 s after a takeout. Oche follows its count:
        the visit loses those darts and goes on with the darts detected next."""
        saved = self._require_game()
        if not (saved.tournament_id and saved.frame.winner >= 0):
            saved.frame.turns[0].throws.clear()
            saved.football_pending, saved.football_confirmed = None, {}
            self._score(saved)
            self._save(saved)
            self._frame()
        self.detected = []
        self._event("board.state", board)

    def confirm_football(self, dart: LocalDart) -> None:
        saved = self._require_game()
        pending = saved.football_pending
        if not pending or not saved.football:
            raise ConnectionProblem("There is no hit waiting for confirmation.")
        segment, coords = dart.segment(), dart.coordinates()
        if coords is None and segment.number:
            target = (
                "B"
                if segment.number == 25 and segment.multiplier == 2
                else "OB"
                if segment.number == 25
                else f"{'T' if segment.multiplier == 3 else 'D'}{segment.number}"
            )
            if segment.multiplier == 1 and segment.number != 25:
                raise ConnectionProblem("Choose the inner or outer single region.")
            x, y = football_rules.position(target)
            coords = Coords(x=x, y=y)
        saved.football_confirmed[pending["index"]] = {
            "raw": {key: pending[key] for key in ("segment", "coords")},
            "segment": segment.model_dump(),
            "coords": coords.model_dump() if coords else None,
        }
        saved.football_pending = None
        self._save(saved)
        self.armed = True  # This snapshot was captured while armed, before confirmation/restart.
        self.consume_board(pending["board"])

    def bot_step(self, now: float) -> None:
        """Throw the next bot dart, or hand over after a finished bot visit, once it is due."""
        level = self.bot_level
        if level is None or self._over(self.saved):
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
        if self.saved.options.mode == "football":
            options = self.saved.options
            aim = football_rules.bot_target(
                self.saved.football,
                self.saved.frame.player,
                options.football_beginner,
                options.football_kickoff,
                options.football_own_goals,
            )
            segment, coords = football_rules.throw_at(aim, level, self.random)
        elif self.saved.game:
            aim = games.bot_target(
                self.saved.game,
                self.saved.frame.player,
                self.saved.frame.round,
                [dart.segment for dart in turn.throws],
                self.saved.options,
            )
            segment, coords = bots.throw(aim, level, self.random)
        else:
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
