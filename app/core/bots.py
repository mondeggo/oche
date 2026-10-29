"""Simulated opponents for local X01 games."""

import math
import random

from app.core.events import Coords, Segment

LEVELS = range(1, 12)
# Standard board order, clockwise from the top.
ORDER = (20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5)
# Radii in millimetres from the board centre.
BULL, OUTER_BULL, TRIPLE, DOUBLE = 6.35, 15.9, (99.0, 107.0), (162.0, 170.0)
# Aim scatter per level, calibrated so level L averages about 10 * L + 15 when aiming at T20.
SPREAD = {1: 87.5, 2: 45.1, 3: 24.9, 4: 18.4, 5: 15.0, 6: 12.5, 7: 10.6, 8: 9.0, 9: 7.6, 10: 6.5, 11: 5.4}
# Automatic bots aim for the human players' three-dart average times this factor. A proportional gap
# feels the same for beginners and strong players, unlike a fixed number of points.
AUTO = {"easy": 0.85, "medium": 1.0, "hard": 1.15}
# Before the players have thrown, an automatic bot assumes this average, weighted as this many visits,
# so a single lucky or poor visit does not swing it.
PRIOR_AVERAGE, PRIOR_VISITS = 45.0, 3
# Seconds before each dart, and before the next player after a finished visit.
SPEEDS = {"slow": (2.0, 3.0), "normal": (1.2, 2.0), "fast": (0.5, 1.0)}

TRIPLES = [(f"T{number}", 3 * number) for number in range(20, 0, -1)]
SINGLES = [(str(number), number) for number in range(20, 0, -1)] + [("25", 25)]
# Common finishing doubles first, then the rest from high to low.
DOUBLES = [(f"D{n}", 2 * n) for n in (20, 16, 18, 12, 10, 8, 19, 17, 15, 14, 13, 11, 9, 7, 6, 5, 4, 3, 2, 1)]
DOUBLES.append(("Bull", 50))
# Setup darts prefer trebles, then singles, then doubles.
SETUP = TRIPLES + SINGLES + DOUBLES


def _setup(value: int, darts: int) -> list[str] | None:
    for name, points in SETUP:
        if darts == 1:
            if points == value:
                return [name]
        elif points < value and (rest := _setup(value - points, darts - 1)):
            return [name, *rest]
    return None


def _finish(remaining: int, darts: int, double_out: bool) -> list[str] | None:
    for name, points in DOUBLES if double_out else SETUP:
        if points == remaining and darts == 1:
            return [name]
        if darts > 1 and points < remaining and (route := _setup(remaining - points, darts - 1)):
            return [*route, name]
    return None


def checkout(remaining: int, darts: int = 3, double_out: bool = True) -> list[str] | None:
    """Shortest finish for an X01 score, matching the route shown on the game screen."""
    if not 1 <= remaining <= 180 or darts < 1:
        return None
    for count in range(1, min(darts, 3) + 1):
        if route := _finish(remaining, count, double_out):
            return route
    return None


def target(remaining: int, darts: int, double_out: bool) -> str:
    """Aim for a finish, else set one up for the next visit, else score on T20."""
    route = checkout(remaining, darts, double_out) or checkout(remaining, 3, double_out)
    return route[0] if route else "T20"


def _aim(name: str) -> tuple[float, float]:
    if name in {"Bull", "25"}:
        return 0.0, 0.0
    ring, number = (name[0], int(name[1:])) if name[0] in "TD" else ("S", int(name))
    radius = {"T": sum(TRIPLE) / 2, "D": sum(DOUBLE) / 2, "S": (TRIPLE[1] + DOUBLE[0]) / 2}[ring]
    angle = math.radians(ORDER.index(number) * 18)
    return radius * math.sin(angle), radius * math.cos(angle)


def segment_at(x: float, y: float) -> Segment:
    radius = math.hypot(x, y)
    if radius <= BULL:
        return Segment(number=25, multiplier=2, name="D25")
    if radius <= OUTER_BULL:
        return Segment(number=25, multiplier=1, name="S25")
    if radius > DOUBLE[1]:
        return Segment(number=0, multiplier=1, name="MISS")
    number = ORDER[int(((math.degrees(math.atan2(x, y)) + 9) % 360) // 18)]
    multiplier = 3 if TRIPLE[0] <= radius <= TRIPLE[1] else 2 if radius >= DOUBLE[0] else 1
    return Segment(number=number, multiplier=multiplier, name=f"{'SDT'[multiplier - 1]}{number}")


def spread(level: float) -> float:
    """Aim scatter for a level, interpolated geometrically between whole levels."""
    low = min(max(int(level), LEVELS.start), LEVELS.stop - 2)
    return SPREAD[low] * (SPREAD[low + 1] / SPREAD[low]) ** (level - low)


def auto_level(difficulty: str, visits: list[int]) -> float:
    """Level that matches the players' visits for an automatic bot of the given difficulty.

    The bot keeps aiming and scattering like any other level, so its visits vary naturally."""
    average = (PRIOR_AVERAGE * PRIOR_VISITS + sum(visits)) / (PRIOR_VISITS + len(visits))
    return min(max((average * AUTO[difficulty] - 15) / 10, LEVELS.start), LEVELS.stop - 1)


def throw(name: str, level: float, rng: random.Random) -> tuple[Segment, Coords]:
    """Where a bot's dart lands, as a segment and a position relative to the double ring."""
    x, y = _aim(name)
    scatter = spread(level)
    x, y = rng.gauss(x, scatter), rng.gauss(y, scatter)
    edge = DOUBLE[1]
    coords = Coords(x=max(-10, min(10, x / edge)), y=max(-10, min(10, y / edge)))
    return segment_at(x, y), coords
