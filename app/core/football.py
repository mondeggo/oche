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
    event: str = "kickoff"
    phase: Literal["bullup", "play", "golden", "penalties", "finished"] = "play"
    starter: int = 0
    half: int = 1
    rounds: int = 0
    visits: list[int] = Field(default_factory=lambda: [0, 0])
    bullup: list[float | None] = Field(default_factory=lambda: [None, None])
    penalties: list[bool | None] = Field(default_factory=lambda: [None, None])
    penalty_score: list[int] = Field(default_factory=lambda: [0, 0])
    clock_seconds: list[list[int]] = Field(default_factory=list)
    added: list[int] = Field(default_factory=lambda: [0, 0])
    halftime_due: bool = False
    winner: int = -1

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
    if state.phase == "bullup":
        return ["B", "OB"]
    if state.phase == "penalties":
        return list(GOALS)
    if state.phase == "finished":
        return []
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
    attack = goal_for(state, player)
    if not own_goals and hit == goal_for(state, 1 - player):
        state.event = "invalid"
        return
    state.ball, state.kickoff, state.event = hit, False, "moved"
    if hit == attack or (own_goals and hit == goal_for(state, 1 - player)):
        scorer = player if hit == attack else 1 - player
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
    if state.phase == "bullup":
        return "B"
    if state.phase == "penalties":
        return goal_for(state, player)
    attack = goal_for(state, player)
    queue = deque((hit, hit) for hit in targets(state, beginner, kickoff))
    seen = set()
    while queue:
        hit, first = queue.popleft()
        if hit == attack:
            return first
        if hit in seen or hit == goal_for(state, 1 - player):
            continue
        seen.add(hit)
        queue.extend((neighbour, first) for neighbour in neighbours(hit, beginner))
    return "OB"


def throw_at(target: str, level: float, rng) -> tuple:
    x, y = position(target)
    spread = bots.spread(level)
    x, y = rng.gauss(x * 170, spread), rng.gauss(y * 170, spread)
    return bots.segment_at(x, y), Coords(x=max(-10, min(10, x / 170)), y=max(-10, min(10, y / 170)))


def goal_for(state: FootballState, player: int) -> str:
    return GOALS[player if state.half == 1 else 1 - player]


def near_wire(coords: Coords | None, tolerance: float = 1.5) -> bool:
    """Ask for confirmation within 1.5 mm of a circular or radial wire."""
    if coords is None:
        return False
    radius = math.hypot(coords.x, coords.y) * 170
    if min(abs(radius - wire) for wire in (6.35, 15.9, 99, 107, 162, 170)) <= tolerance:
        return True
    angle = math.degrees(math.atan2(coords.x, coords.y))
    delta = abs((angle % 18) - 9)
    return 15.9 < radius < 170 and radius * math.sin(math.radians(delta)) <= tolerance


def strike(state: FootballState, dart: Dart, player: int, options) -> None:
    if state.phase == "bullup":
        if dart.coords is None:
            raise ConnectionProblem("Enter the bull-up distance from the centre in millimetres.")
        state.bullup[player] = math.hypot(dart.coords.x, dart.coords.y) * 170
        return
    hit = region(dart)
    if state.phase == "penalties":
        state.penalties[player] = hit == goal_for(state, player)
        state.penalty_score[player] += int(state.penalties[player])
        if all(result is not None for result in state.penalties) and state.penalties[0] != state.penalties[1]:
            state.winner = 0 if state.penalties[0] else 1
            state.phase, state.event = "finished", "fulltime"
        return
    move(state, hit, player, options.football_beginner, options.football_kickoff, options.football_own_goals)
    if state.goal_by is not None:
        if state.phase == "golden" or (
            options.football_format == "goals" and max(state.goals) >= options.goals_to_win
        ):
            state.winner = state.goal_by
            state.phase, state.event = "finished", "fulltime"
        elif (
            options.football_format == "goals"
            and state.half == 1
            and max(state.goals) >= math.ceil(options.goals_to_win / 2)
        ):
            state.halftime_due = True


def advance(state: FootballState, player: int, options) -> int:
    """Close a visit; transitions happen after darts are removed, never mid-visit."""
    if state.phase == "bullup":
        if any(distance is None for distance in state.bullup):
            return 1 - player
        if math.isclose(*state.bullup, abs_tol=0.01):
            state.bullup = [None, None]
            state.event = "bullup_tie"
            return 0
        state.starter = 0 if state.bullup[0] < state.bullup[1] else 1
        state.phase, state.event = "play", "kickoff"
        return state.starter
    if state.phase == "penalties":
        if all(result is not None for result in state.penalties):
            state.penalties = [None, None]
        return 1 - player
    next_player = 1 - state.goal_by if state.goal_by is not None else 1 - player
    state.visits[player] += 1
    state.goal_by = None
    state.event = "kickoff" if state.kickoff else "turn"
    if all(state.visits):
        state.visits = [0, 0]
        state.rounds += 1
        half_rounds = options.football_rounds // 2
        first_end = half_rounds + state.added[0]
        final_end = options.football_rounds + sum(state.added)
        halftime = state.half == 1 and (
            state.halftime_due if options.football_format == "goals" else state.rounds >= first_end
        )
        if halftime:
            state.half = 2
            state.ball, state.kickoff, state.event = "B", True, "halftime"
            return 1 - state.starter
        if options.football_format == "rounds" and state.phase == "play" and state.rounds >= final_end:
            if state.goals[0] != state.goals[1] or options.football_tiebreak == "draw":
                state.phase, state.event = "finished", "fulltime"
                state.winner = (
                    -1 if state.goals[0] == state.goals[1] else int(state.goals[1] > state.goals[0])
                )
            else:
                state.phase = options.football_tiebreak
                state.event = "fulltime"
                state.ball, state.kickoff = "B", True
                return state.starter
    return next_player


def clock_schedule(rounds: int, rng) -> list[list[int]]:
    """Save varied round endpoints, with each half ending at exactly 45 minutes."""
    result = []
    for count in (rounds // 2, rounds - rounds // 2):
        weights = [rng.uniform(0.8, 1.2) for _ in range(count)]
        elapsed = 0.0
        ticks = []
        for weight in weights:
            elapsed += weight
            ticks.append(round(2700 * elapsed / sum(weights)))
        result.append(ticks)
    return result


def clock(state: FootballState, options) -> str:
    if state.phase == "bullup":
        return "0′"
    if options.football_format != "rounds":
        return str(state.rounds + 1)
    if state.event == "halftime":
        return "45′"
    first = options.football_rounds // 2
    second = options.football_rounds - first
    completed = state.rounds if state.half == 1 else state.rounds - first - state.added[0]
    base, length = (0, first) if state.half == 1 else (45, second)
    if state.phase in {"finished", "golden", "penalties"}:
        return "90′"
    if completed >= length:
        return f"{base + 45}+{completed - length + 1}′"
    if state.clock_seconds:
        seconds = base * 60 + state.clock_seconds[state.half - 1][completed]
        return f"{seconds // 60:02d}:{seconds % 60:02d}"
    return f"{base + round((completed + 1) * 45 / length)}′"


def playable_targets(state: FootballState, player: int, options) -> list[str]:
    if state.phase == "penalties":
        return [goal_for(state, player)]
    return [
        hit
        for hit in targets(state, options.football_beginner, options.football_kickoff)
        if options.football_own_goals or hit != goal_for(state, 1 - player)
    ]
