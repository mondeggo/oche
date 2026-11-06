"""Target games on the X01 screen: Cricket, Around the Clock, Count Up, Bob's 27 and Shanghai.

As in football, a visit is replayed from the state at its start, so undo and detector
corrections are exact."""

from pydantic import BaseModel, Field

from app.core.events import Segment

MODES = ("cricket", "atc", "countup", "bobs27", "shanghai")
# Variant names as AutoDarts and the caller use them.
VARIANTS = {
    "cricket": "Cricket",
    "atc": "ATC",
    "countup": "Count Up",
    "bobs27": "Bob's 27",
    "shanghai": "Shanghai",
}
CRICKET = (15, 16, 17, 18, 19, 20, 25)
# Games with a fixed length; Cricket and Around the Clock run until someone finishes.
ROUNDS = {"countup": 8, "bobs27": 21, "shanghai": 7}
BEDS = {"any": "single", "double": "double", "triple": "triple"}


class TargetState(BaseModel):
    scores: list[int] = Field(min_length=1, max_length=8)
    # Cricket: marks on 15–20 and the bull, per player.
    marks: list[list[int]] = Field(default_factory=list)
    # Around the Clock: targets hit, per player.
    progress: list[int] = Field(default_factory=list)
    # Bob's 27: players whose score fell below zero.
    out: list[bool] = Field(default_factory=list)
    finished: bool = False
    winner: int = -1


def start(options, count: int) -> TargetState:
    return TargetState(
        scores=[27 if options.mode == "bobs27" else 0] * count,
        marks=[[0] * len(CRICKET) for _ in range(count)] if options.mode == "cricket" else [],
        progress=[0] * count if options.mode == "atc" else [],
        out=[False] * count,
    )


def sequence(options) -> list[int]:
    """Around the Clock targets in order, with the bull last when chosen."""
    return [*range(1, 21), *([25] if options.atc_bull else [])]


def target(state: TargetState, player: int, round: int, options) -> int | None:
    """The number this player aims for now, or None in Cricket, Count Up and after finishing."""
    if options.mode == "atc":
        order = sequence(options)
        return order[state.progress[player]] if state.progress[player] < len(order) else None
    if options.mode == "bobs27":
        return 25 if round >= 20 else round + 1
    if options.mode == "shanghai":
        return round + 1
    return None


def _clock_hit(segment: Segment, number: int, ring: str) -> bool:
    # A triple bull does not exist, so either bull counts for the bull target.
    if segment.number != number or not segment.multiplier:
        return False
    return number == 25 or ring == "any" or segment.multiplier == {"double": 2, "triple": 3}[ring]


def _cricket(state: TargetState, segment: Segment, player: int, options) -> None:
    if segment.number not in CRICKET:
        return
    index, marks = CRICKET.index(segment.number), state.marks
    open_for = [p for p in range(len(marks)) if p != player and marks[p][index] < 3]
    for _ in range(segment.multiplier):
        if marks[player][index] < 3:
            marks[player][index] += 1
        elif options.cricket_scoring == "cutthroat":
            for rival in open_for:
                state.scores[rival] += segment.number
        elif open_for:
            state.scores[player] += segment.number
    if all(mark >= 3 for mark in marks[player]):
        rivals = [score for p, score in enumerate(state.scores) if p != player]
        mine = state.scores[player]
        if all(
            mine <= score if options.cricket_scoring == "cutthroat" else mine >= score for score in rivals
        ):
            state.finished, state.winner = True, player


def following(state: TargetState, player: int, round: int, options) -> tuple[int, int] | None:
    """The next player still in the game and their round, or None when the game is over."""
    count = len(state.scores)
    for step in range(1, count + 1):
        candidate = (player + step) % count
        next_round = round + (candidate <= player)
        if state.out[candidate]:
            continue
        if options.mode in ROUNDS and next_round >= ROUNDS[options.mode]:
            return None
        return candidate, next_round
    return None


def _best(state: TargetState) -> int:
    """The single highest score among players still in, or -1 for a tie or when everyone is out."""
    scores = [score for score, out in zip(state.scores, state.out, strict=True) if not out]
    if not scores or scores.count(max(scores)) > 1:
        return -1
    return next(p for p, score in enumerate(state.scores) if score == max(scores) and not state.out[p])


def score(
    begin: TargetState, darts: list[Segment], player: int, round: int, options, final: bool
) -> tuple[TargetState, int]:
    """Replay a visit and return the new state and the visit's points.

    ``final`` closes a short visit; three darts always close it."""
    state, mode = begin.model_copy(deep=True), options.mode
    aim = target(state, player, round, options)
    hits = 0
    for segment in darts:
        value = segment.number * segment.multiplier
        if mode == "cricket":
            _cricket(state, segment, player, options)
        elif mode == "atc":
            if aim is not None and _clock_hit(segment, aim, options.atc_ring):
                state.progress[player] += 1
                state.scores[player] = state.progress[player]
                aim = target(state, player, round, options)
                if aim is None:
                    state.finished, state.winner = True, player
        elif mode == "countup":
            state.scores[player] += value
        elif mode == "bobs27":
            if segment.number == aim and segment.multiplier == 2:
                hits += 1
                state.scores[player] += value
        elif mode == "shanghai" and segment.number == aim:
            state.scores[player] += value
        if state.finished:
            break
    # A single, double and treble of the round's number in one visit wins at once.
    if mode == "shanghai" and {1, 2, 3} <= {dart.multiplier for dart in darts if dart.number == aim}:
        state.finished, state.winner = True, player
    if not state.finished and (final or len(darts) >= 3):
        if mode == "bobs27" and not hits:
            state.scores[player] -= 50 if aim == 25 else 2 * aim
            state.out[player] = state.scores[player] < 0
        if following(state, player, round, options) is None:
            state.finished, state.winner = True, _best(state)
    return state, state.scores[player] - begin.scores[player]


def public_state(state: TargetState, options) -> dict:
    """Frame state for integrations: the caller reads each player's next target."""
    data = state.model_dump()
    if options.mode == "atc":
        data["targets"] = [
            {"number": number, "bed": BEDS[options.atc_ring] if number != 25 else "single"}
            if (number := target(state, player, 0, options)) is not None
            else None
            for player in range(len(state.scores))
        ]
    return data


def bot_target(state: TargetState, player: int, round: int, darts: list[Segment], options) -> str:
    """Where a bot aims, following the game's rules, as a name that ``bots.throw`` understands."""
    mode = options.mode
    aim = target(state, player, round, options)
    if mode == "atc":
        if aim is None or aim == 25:
            return "Bull"
        return {"any": str(aim), "double": f"D{aim}", "triple": f"T{aim}"}[options.atc_ring]
    if mode == "bobs27":
        return "Bull" if aim == 25 else f"D{aim}"
    if mode == "shanghai":
        # Collect the treble, double and single of the number for a Shanghai.
        thrown = {dart.multiplier for dart in darts if dart.number == aim}
        return next(
            (name for multiplier, name in ((3, f"T{aim}"), (2, f"D{aim}")) if multiplier not in thrown),
            str(aim),
        )
    if mode == "cricket":
        return _cricket_aim(state, player, options)
    return "T20"


def _cricket_aim(state: TargetState, player: int, options) -> str:
    marks, scores = state.marks, state.scores
    rivals = [p for p in range(len(scores)) if p != player]
    cutthroat = options.cricket_scoring == "cutthroat"
    leading = all(scores[player] <= scores[p] if cutthroat else scores[player] > scores[p] for p in rivals)
    if not leading:
        # Score on a number this player has closed and a rival has not.
        for index in reversed(range(len(CRICKET))):
            if marks[player][index] >= 3 and any(marks[p][index] < 3 for p in rivals):
                return _name(CRICKET[index])
    for index in reversed(range(len(CRICKET))):
        if marks[player][index] < 3:
            return _name(CRICKET[index])
    return "T20"


def _name(number: int) -> str:
    return "Bull" if number == 25 else f"T{number}"
