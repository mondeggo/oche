"""Football movement on the dartboard: shared edges, optional diagonals, and goal resets."""

import math
from collections import deque
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core import bots
from app.core.errors import ConnectionProblem
from app.core.events import Coords, Dart

REGIONS = {"B", "OB"} | {f"{ring}{number}" for ring in "ITOD" for number in range(1, 21)}
GOALS = ("D6", "D11")  # Player 1 attacks right; Player 2 attacks left.


class FootballState(BaseModel):
    ball: str = "B"
    kickoff: bool = True
    goals: list[int] = Field(default_factory=lambda: [0, 0], min_length=2, max_length=2)
    goal_by: Literal[0, 1] | None = None
    event: Literal["kickoff", "moved", "invalid", "goal", "own_goal", "turn"] = "kickoff"

    @field_validator("ball")
    @classmethod
    def valid_region(cls, value: str) -> str:
        if value not in REGIONS:
            raise ValueError("Invalid football region")
        return value


def neighbours(region: str, beginner: bool = False) -> list[str]:
    if region == "B":
        return ["OB"]
    if region == "OB":
        return ["B", *[f"I{number}" for number in bots.ORDER]]
    ring, number = region[0], int(region[1:])
    index, depth = bots.ORDER.index(number), "ITOD".index(ring)
    sides = [bots.ORDER[(index - 1) % 20], bots.ORDER[(index + 1) % 20]]
    result = [f"{ring}{side}" for side in sides]
    if ring == "I":
        result.append("OB")
    for radial in (depth - 1, depth + 1):
        if 0 <= radial < 4:
            result.append(f"{'ITOD'[radial]}{number}")
            if beginner:
                result.extend(f"{'ITOD'[radial]}{side}" for side in sides)
    return result


def targets(state: FootballState, beginner: bool, kickoff: str) -> list[str]:
    if not state.kickoff:
        return neighbours(state.ball, beginner)
    return (
        ["OB"]
        + (["B"] if kickoff != "outer" else [])
        + ([f"I{number}" for number in bots.ORDER] if kickoff == "inner" else [])
    )


def region(dart: Dart) -> str | None:
    segment = dart.segment
    if segment.number == 0:
        return None
    if segment.number == 25:
        return "B" if segment.multiplier == 2 else "OB"
    if segment.multiplier == 1:
        if dart.coords is None:
            raise ConnectionProblem(
                "Football needs hit coordinates to distinguish inner and outer singles. "
                "Use manual input if your detector does not provide them."
            )
        radius = math.hypot(dart.coords.x, dart.coords.y) * bots.DOUBLE[1]
        return f"{'I' if radius < bots.TRIPLE[0] else 'O'}{segment.number}"
    return f"{'T' if segment.multiplier == 3 else 'D'}{segment.number}"


def move(
    state: FootballState, hit: str | None, player: int, beginner: bool, kickoff: str, own_goals: bool
) -> None:
    state.goal_by = None
    if hit not in targets(state, beginner, kickoff):
        state.event = "invalid"
        return
    state.ball, state.kickoff, state.event = hit, False, "moved"
    if hit == GOALS[player] or (own_goals and hit == GOALS[1 - player]):
        scorer = player if hit == GOALS[player] else 1 - player
        state.goals[scorer] += 1
        state.goal_by = scorer
        state.ball, state.kickoff = "B", True
        state.event = "goal" if scorer == player else "own_goal"


def position(region: str) -> tuple[float, float]:
    if region in {"B", "OB"}:
        return (0, 0) if region == "B" else (0, 11 / 170)
    ring, number = region[0], int(region[1:])
    radius = {"I": 65, "T": 103, "O": 135, "D": 166}[ring] / 170
    angle = math.radians(bots.ORDER.index(number) * 18)
    return radius * math.sin(angle), radius * math.cos(angle)


def bot_target(state: FootballState, player: int, beginner: bool, kickoff: str, own_goals: bool) -> str:
    """Choose the first step of a shortest legal route, avoiding optional own goals."""
    queue = deque((hit, hit) for hit in targets(state, beginner, kickoff))
    seen = set()
    while queue:
        hit, first = queue.popleft()
        if hit == GOALS[player]:
            return first
        if hit in seen or (own_goals and hit == GOALS[1 - player]):
            continue
        seen.add(hit)
        queue.extend((neighbour, first) for neighbour in neighbours(hit, beginner))
    return "OB"


def throw_at(target: str, level: float, rng) -> tuple:
    x, y = position(target)
    spread = bots.spread(level)
    x, y = rng.gauss(x * 170, spread), rng.gauss(y * 170, spread)
    return bots.segment_at(x, y), Coords(x=max(-10, min(10, x / 170)), y=max(-10, min(10, y / 170)))
