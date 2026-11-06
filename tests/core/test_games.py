import pytest
from pydantic import ValidationError

from app.core import bots
from app.core.errors import ConnectionProblem
from app.core.events import EventBus
from app.core.local import LocalDart, LocalGames, LocalOptions, LocalPlayer


def start(tmp_path, mode, players=("Alice", "Bob"), **options):
    game = LocalGames(tmp_path, None, EventBus(capacity=2000))
    game.start(LocalOptions(mode=mode, players=list(players), input="manual", **options))
    return game


def visit(game, *darts):
    for number, multiplier in darts:
        game.throw(LocalDart(number=number, multiplier=multiplier).segment())
    if not game._over(game.saved):
        game.next_turn()


def events(game, name):
    return [event for event in game.bus.normalized_history if event.event == name]


def test_cricket_closes_numbers_scores_extra_marks_and_needs_the_best_score(tmp_path):
    game = start(tmp_path, "cricket")
    visit(game, (20, 3), (20, 2), (19, 1))  # Closes 20, then 40 points while Bob has it open.
    assert game.saved.frame.game_scores == [40, 0]
    assert game.saved.game.marks[0][5] == 3 and game.saved.game.marks[0][4] == 1
    assert game.saved.frame.turns[1].score == 40  # The caller reads the visit's points.
    visit(game, (20, 3), (19, 3), (19, 3))  # Alice closed 20, so only 19 scores for Bob: 57.
    assert game.saved.frame.game_scores == [40, 57]
    visit(game, (19, 3), (25, 2))  # Bob closed 19: Alice's extra mark scores nothing.
    assert game.saved.frame.game_scores == [40, 57]
    assert game.status()["targets"]["marks"][0][6] == 2


def test_cricket_win_on_closing_everything_with_the_best_score(tmp_path):
    game = start(tmp_path, "cricket", players=("Alice",))
    for number in (15, 16, 17, 18, 19, 20):
        visit(game, (number, 3))
    game.throw(LocalDart(number=25, multiplier=2).segment())
    assert not game._over(game.saved)
    game.throw(LocalDart(number=25, multiplier=1).segment())
    assert game.saved.frame.winner == 0 and game.status()["finished"]
    assert [event.event for event in game.bus.normalized_history][-2:] == ["leg_win", "match_win"]


def test_cutthroat_points_go_to_open_opponents_and_the_lowest_wins(tmp_path):
    game = start(tmp_path, "cricket", players=("Alice", "Bob", "Cara"), cricket_scoring="cutthroat")
    visit(game, (20, 3), (20, 3))
    assert game.saved.frame.game_scores == [0, 60, 60]


def test_around_the_clock_follows_the_ring_and_tells_the_caller_the_target(tmp_path):
    game = start(tmp_path, "atc", atc_ring="double", atc_bull=True)
    visit(game, (1, 1), (1, 2), (2, 2))
    assert game.saved.frame.game_scores == [2, 0]
    assert game.saved.frame.state["targets"][0] == {"number": 3, "bed": "double"}
    assert game.saved.frame.turns[1].points == 2
    game.saved.game.progress[1] = game.saved.game_visit.progress[1] = 20  # Bob only needs the bull.
    game.throw(LocalDart(number=25, multiplier=1).segment())
    assert game.saved.frame.winner == 1


def test_count_up_ends_after_eight_rounds_with_a_draw_or_a_winner(tmp_path):
    game = start(tmp_path, "countup")
    for _ in range(7):
        visit(game, (20, 1))
        visit(game, (20, 1))
    visit(game, (20, 1), (20, 1), (20, 1))
    assert not game._over(game.saved)
    game.throw(LocalDart(number=20, multiplier=1).segment())
    game.next_turn()  # A short final visit ends the game.
    assert game.status()["finished"] and game.saved.frame.winner == 0
    assert game.saved.frame.game_scores == [200, 160]
    with pytest.raises(ConnectionProblem):
        game.next_turn()


def test_bobs_27_penalty_elimination_and_undo(tmp_path):
    game = start(tmp_path, "bobs27")
    visit(game, (1, 2), (1, 2), (0, 1))  # +4
    for number, multiplier in ((0, 1), (0, 1), (0, 1)):
        game.throw(LocalDart(number=number, multiplier=multiplier).segment())
    assert game.saved.frame.game_scores == [31, 25]  # Missed D1: minus 2.
    game.undo()
    assert game.saved.frame.game_scores == [31, 27]  # Undo replays the visit without the penalty.
    game.throw(LocalDart(number=0, multiplier=1).segment())
    game.next_turn()
    game.saved.game.scores[1] = game.saved.game_visit.scores[1] = 3
    game.saved.frame.game_scores[1] = 3
    visit(game, (2, 2))  # Alice, round 2: +4.
    visit(game, (0, 1), (0, 1), (0, 1))  # Bob: 3 - 4 is below zero.
    assert game.saved.game.out == [False, True]
    assert game.saved.frame.player == 0 and game.saved.frame.round == 2
    visit(game, (3, 2))
    assert game.saved.frame.player == 0 and game.saved.frame.round == 3  # Bob is skipped.


def test_bobs_27_ends_without_a_winner_when_everyone_is_out(tmp_path):
    game = start(tmp_path, "bobs27", players=("Alice",))
    game.saved.game.scores[0] = game.saved.game_visit.scores[0] = 1
    game.saved.frame.game_scores[0] = 1
    visit(game, (0, 1), (0, 1), (0, 1))
    assert game.status()["finished"] and game.saved.frame.winner == -1


def test_shanghai_scores_only_the_round_number_and_wins_at_once(tmp_path):
    game = start(tmp_path, "shanghai")
    visit(game, (1, 3), (20, 3), (1, 1))
    assert game.saved.frame.game_scores == [4, 0]
    visit(game, (1, 1))
    game.throw(LocalDart(number=2, multiplier=1).segment())
    game.throw(LocalDart(number=2, multiplier=3).segment())
    assert not game._over(game.saved)
    game.throw(LocalDart(number=2, multiplier=2).segment())
    assert game.saved.frame.winner == 0 and game.status()["finished"]


def test_target_games_restore_from_disk(tmp_path):
    game = start(tmp_path, "cricket")
    visit(game, (20, 3))
    restored = LocalGames(tmp_path, None, EventBus())
    assert restored.active and restored.saved.game.marks[0][5] == 3
    assert restored.saved.frame.variant == "Cricket"


@pytest.mark.parametrize("mode", ["cricket", "atc", "countup", "bobs27", "shanghai", "football"])
def test_difficulty_bots_play_by_the_rules_to_the_end(tmp_path, mode):
    players = [{"name": "A", "difficulty": "hard"}, {"name": "B", "difficulty": "easy"}]
    game = LocalGames(tmp_path, None, EventBus(capacity=5000))
    game.random.seed(7)
    game.start(LocalOptions(mode=mode, players=players, input="manual", bot_speed="fast"))
    assert game.bot_level == bots.DIFFICULTY["hard"]
    now = 0.0
    for _ in range(20000):
        if game._over(game.saved):
            break
        game.bot_step(now)
        now += 5
    assert game.status()["finished"]


def test_one_kind_of_bot_per_player():
    with pytest.raises(ValidationError):
        LocalPlayer(name="Bot", bot=3, difficulty="easy")
    assert LocalPlayer(name="Bot", difficulty="easy").is_bot
