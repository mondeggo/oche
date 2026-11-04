"""Saved local tournaments. Match slots form a bracket; Swiss rounds use standings."""

import json
import math
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ConnectionProblem
from app.core.local import LocalGames, LocalOptions
from app.core.storage import write_private_json

Format = Literal["single", "double", "swiss", "swiss_elimination"]


class TournamentOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="Local tournament", min_length=1, max_length=80)
    players: list[str] = Field(min_length=2, max_length=32)
    format: Format = "single"
    swiss_rounds: int = Field(default=3, ge=1, le=10, strict=True)
    finalists: int = Field(default=2, ge=2, le=32, strict=True)
    starting_score: Literal[301, 501] = 501
    double_out: bool = True
    input: Literal["autodarts", "manual"] = "manual"

    @field_validator("players")
    @classmethod
    def names(cls, values):
        names = [name.strip() for name in values]
        if any(not name or len(name) > 60 for name in names):
            raise ValueError("Enter a name of 1–60 characters for every player.")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Player names must be different.")
        return names

    @model_validator(mode="after")
    def format_settings(self):
        if self.format.startswith("swiss") and self.swiss_rounds >= len(self.players):
            raise ValueError("Choose fewer Swiss rounds than players.")
        if self.format == "swiss_elimination" and (
            self.finalists > len(self.players) or self.finalists & (self.finalists - 1)
        ):
            raise ValueError("The final bracket needs 2, 4, 8, 16 or 32 qualifying players.")
        return self


class Entrant(BaseModel):
    id: str
    name: str
    seed: int


class Slot(BaseModel):
    kind: Literal["player", "winner", "loser"] = "player"
    ref: str | None = None


class TournamentMatch(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    stage: Literal["swiss", "upper", "lower", "final", "reset"]
    round: int
    slots: list[Slot] = Field(min_length=2, max_length=2)
    completed: bool = False
    winner: str | None = None


class SavedTournament(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    options: TournamentOptions
    players: list[Entrant]
    matches: list[TournamentMatch] = Field(default_factory=list)
    state: Literal["active", "completed", "cancelled"] = "active"
    champion: str | None = None


def participants(
    match: TournamentMatch, matches: dict[str, TournamentMatch]
) -> tuple[bool, list[str | None]]:
    """Unresolved sources differ from resolved empty slots (byes)."""
    players = []
    for slot in match.slots:
        if slot.kind == "player":
            players.append(slot.ref)
            continue
        source = matches[slot.ref]
        if not source.completed:
            return False, []
        if slot.kind == "winner":
            players.append(source.winner)
        else:
            _, pair = participants(source, matches)
            players.append(next((player for player in pair if player and player != source.winner), None))
    return True, players


def standings(saved: SavedTournament) -> list[dict]:
    rows = {
        player.id: {
            **player.model_dump(),
            "points": 0,
            "played": 0,
            "wins": 0,
            "losses": 0,
            "byes": 0,
            "opponents": [],
        }
        for player in saved.players
    }
    matches = {match.id: match for match in saved.matches}
    for match in saved.matches:
        if match.stage != "swiss" or not match.completed:
            continue
        _, pair = participants(match, matches)
        for player in filter(None, pair):
            row = rows[player]
            opponent = next((other for other in pair if other and other != player), None)
            row["points"] += int(match.winner == player)
            if opponent:
                row["played"] += 1
                row["wins" if match.winner == player else "losses"] += 1
                row["opponents"].append(opponent)
            else:
                row["byes"] += 1
    for row in rows.values():
        row["buchholz"] = sum(rows[opponent]["points"] for opponent in row["opponents"])
    return sorted(rows.values(), key=lambda row: (-row["points"], -row["buchholz"], row["seed"]))


def swiss_pairs(rows: list[dict]) -> list[list[str | None]]:
    """Prefer close scores without repeat opponents. Bound search for larger fields."""
    rows = list(rows)
    pairs = []
    if len(rows) % 2:
        bye = min(reversed(rows), key=lambda row: row["byes"])
        rows.remove(bye)
        pairs.append([bye["id"], None])
    budget = 20000

    def search(pool):
        nonlocal budget
        if not pool:
            return []
        budget -= 1
        if budget < 0:
            return None
        first, *rest = pool
        candidates = sorted(rest, key=lambda row: abs(first["points"] - row["points"]))
        for other in candidates:
            if other["id"] in first["opponents"]:
                continue
            result = search([row for row in rest if row is not other])
            if result is not None:
                return [[first["id"], other["id"]], *result]
        return None

    result = search(rows)
    if result is None:
        # A repeat can be unavoidable; still prefer unplayed opponents with similar scores.
        result = []
        while rows:
            first = rows.pop(0)
            other = min(
                rows, key=lambda row: (row["id"] in first["opponents"], abs(first["points"] - row["points"]))
            )
            rows.remove(other)
            result.append([first["id"], other["id"]])
    return [*result, *pairs]


def add_swiss_round(saved: SavedTournament, number: int) -> None:
    for pair in swiss_pairs(standings(saved)):
        saved.matches.append(
            TournamentMatch(stage="swiss", round=number, slots=[Slot(ref=player) for player in pair])
        )


def add_bracket(saved: SavedTournament, seeds: list[str], *, double: bool = False) -> None:
    size = 1 << (len(seeds) - 1).bit_length()
    order = [1, 2]
    while len(order) < size:
        total = len(order) * 2 + 1
        order = [value for seed in order for value in (seed, total - seed)]
    slots = [Slot(ref=seeds[seed - 1] if seed <= len(seeds) else None) for seed in order]

    def round_matches(stage, number, entries):
        matches = [
            TournamentMatch(stage=stage, round=number, slots=entries[i : i + 2])
            for i in range(0, len(entries), 2)
        ]
        saved.matches.extend(matches)
        return matches

    def source(match, kind="winner"):
        return Slot(kind=kind, ref=match.id)

    upper = [round_matches("upper", 1, slots)]
    for number in range(2, int(math.log2(size)) + 1):
        upper.append(round_matches("upper", number, [source(match) for match in upper[-1]]))
    if not double:
        return
    if size == 2:
        lower_champion = source(upper[0][0], "loser")
    else:
        lower = round_matches("lower", 1, [source(match, "loser") for match in upper[0]])
        for number, upper_round in enumerate(upper[1:], start=2):
            drops = list(reversed(upper_round))
            lower = round_matches(
                "lower",
                2 * number - 2,
                [
                    slot
                    for match, drop in zip(lower, drops, strict=True)
                    for slot in (source(match), source(drop, "loser"))
                ],
            )
            if number < len(upper):
                lower = round_matches("lower", 2 * number - 1, [source(match) for match in lower])
        lower_champion = source(lower[0])
    round_matches("final", 1, [source(upper[-1][0]), lower_champion])


def advance(saved: SavedTournament) -> None:
    """Resolve byes and stage transitions; explicit completed flags make this idempotent."""
    if saved.state != "active":
        return
    while True:
        changed = False
        matches = {match.id: match for match in saved.matches}
        for match in saved.matches:
            ready, pair = participants(match, matches)
            if ready and not match.completed and None in pair:
                match.completed, match.winner = True, next(filter(None, pair), None)
                changed = True
        swiss = [match for match in saved.matches if match.stage == "swiss"]
        upper = [match for match in saved.matches if match.stage == "upper"]
        if swiss and all(match.completed for match in swiss) and not upper:
            number = max(match.round for match in swiss)
            if number < saved.options.swiss_rounds:
                add_swiss_round(saved, number + 1)
                changed = True
            elif saved.options.format == "swiss_elimination":
                add_bracket(saved, [row["id"] for row in standings(saved)[: saved.options.finalists]])
                changed = True
            else:
                saved.state, saved.champion = "completed", standings(saved)[0]["id"]
                return
        if upper:
            last = saved.matches[-1]
            if last.completed:
                if last.stage == "final" and last.winner == participants(last, matches)[1][1]:
                    # The upper winner's first loss requires a reset: everyone gets two losses.
                    saved.matches.append(
                        TournamentMatch(
                            stage="reset",
                            round=1,
                            slots=[Slot(ref=player) for player in participants(last, matches)[1]],
                        )
                    )
                    changed = True
                else:
                    saved.state, saved.champion = "completed", last.winner
                    return
        if not changed:
            return


class Tournaments:
    def __init__(self, directory: Path, local: LocalGames):
        self.path = directory / "tournament.json"
        self.local = local
        self.saved: SavedTournament | None = None
        self.history: list[SavedTournament] = []
        self.error: str | None = None
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                # Older installations saved a single tournament in this file.
                records = data["tournaments"] if "tournaments" in data else [data]
                self.history = [SavedTournament.model_validate(record) for record in records]
                if len({item.id for item in self.history}) != len(self.history):
                    raise ValueError("Duplicate tournament IDs")
                if any(item.state == "active" for item in self.history[:-1]):
                    raise ValueError("An older tournament is still active")
                for saved in self.history:
                    matches = {match.id: match for match in saved.matches}
                    for match in saved.matches:
                        participants(match, matches)
                self.saved = self.history[-1] if self.history else None
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            self.saved = None
            self.history = []
            self.error = "Cannot load the saved tournament. Restore tournament.json before continuing."

    def _save(self, saved: SavedTournament) -> None:
        history = [item for item in self.history if item.id != saved.id] + [saved]
        write_private_json(self.path, {"tournaments": [item.model_dump(mode="json") for item in history]})
        self.history = history
        self.saved = saved

    def create(self, options: TournamentOptions) -> None:
        self.sync()
        if self.error:
            raise ConnectionProblem(self.error)
        if self.saved and self.saved.state == "active":
            raise ConnectionProblem("Finish or end the current tournament before creating another.")
        if self.local.active:
            raise ConnectionProblem("End the current local game before starting another.")
        saved = SavedTournament(
            options=options,
            players=[
                Entrant(id=str(uuid4()), name=name, seed=index + 1)
                for index, name in enumerate(options.players)
            ],
        )
        if options.format.startswith("swiss"):
            add_swiss_round(saved, 1)
        else:
            add_bracket(saved, [player.id for player in saved.players], double=options.format == "double")
        advance(saved)
        self._save(saved)

    def sync(self) -> None:
        """Recover results directly from the durable game, including after a process restart."""
        if not self.saved or self.saved.state != "active":
            return
        game = self.local.saved
        if not game or game.tournament_id != self.saved.id or game.frame.winner < 0:
            return
        saved = self.saved.model_copy(deep=True)
        match = next((match for match in saved.matches if match.id == game.tournament_match_id), None)
        if not match or match.completed:
            return
        ready, pair = participants(match, {match.id: match for match in saved.matches})
        if not ready or None in pair or game.frame.winner >= len(pair):
            raise ConnectionProblem("The tournament match no longer matches the saved game.")
        match.completed, match.winner = True, pair[game.frame.winner]
        advance(saved)
        self._save(saved)

    def match_options(self, match_id: str) -> LocalOptions:
        self.sync()
        if not self.saved or self.saved.state != "active":
            raise ConnectionProblem("There is no active tournament.")
        match = next((match for match in self.saved.matches if match.id == match_id), None)
        if not match or match.completed:
            raise ConnectionProblem("This tournament match is not available.")
        ready, pair = participants(match, {match.id: match for match in self.saved.matches})
        if not ready or None in pair:
            raise ConnectionProblem("Finish the preceding matches first.")
        names = {player.id: player.name for player in self.saved.players}
        options = self.saved.options
        return LocalOptions(
            players=[names[player] for player in pair],
            starting_score=options.starting_score,
            double_out=options.double_out,
            input=options.input,
        )

    def cancel(self) -> None:
        self.sync()
        if not self.saved or self.saved.state != "active":
            raise ConnectionProblem("There is no active tournament.")
        saved = self.saved.model_copy(deep=True)
        saved.state = "cancelled"
        self._save(saved)

    def listing(self) -> dict:
        self.sync()
        return {
            "tournaments": [
                {
                    "id": saved.id,
                    "name": saved.options.name,
                    "format": saved.options.format,
                    "players": len(saved.players),
                    "state": saved.state,
                    "champion": next(
                        (player.name for player in saved.players if player.id == saved.champion), None
                    ),
                }
                for saved in reversed(self.history)
            ],
            "error": self.error,
        }

    def status(self, tournament_id: str | None = None) -> dict:
        self.sync()
        saved = (
            self.saved
            if tournament_id is None
            else next((item for item in self.history if item.id == tournament_id), None)
        )
        if not saved:
            return {"tournament": None, "error": self.error}
        matches = {match.id: match for match in saved.matches}
        game = self.local.saved
        result = saved.model_dump(mode="json")
        result["standings"] = standings(saved)
        for item, match in zip(result["matches"], saved.matches, strict=True):
            ready, pair = participants(match, matches)
            item["players"] = pair if ready else [None, None]
            item["ready"] = ready and None not in pair and not match.completed and saved.state == "active"
            item["running"] = bool(
                game
                and game.active
                and saved.state == "active"
                and game.tournament_id == saved.id
                and game.tournament_match_id == match.id
                and not match.completed
            )
        return {"tournament": result, "error": self.error}
