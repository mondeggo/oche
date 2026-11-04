# Local games

Open **Play on Oche** in the sidebar (`/local`) and choose a game mode. The first
mode is one-leg X01: 301 or 501, single in, straight or double out, and one to
eight players sharing the board. Bull is 25; double bull is 50 and counts as a
double for checkout. A bust restores the score from the beginning of the visit.
The **Arcade** category includes playable Football. Other modes, including
Pac-Man and Mario 64, are marked **Coming soon** and cannot be started yet.

The setup screen is one centered column, up to 680 px wide, with one panel: the
players and, below them, labelled settings (starting score, finish, player names, dartboard and input) with
full-width choices. A new X01 setup starts with one player, and Football with two; **Rematch** keeps the previous players. **Player names** chooses
how new rows are named: **Random** gives a playful name such as
"Lucky Otter", with a `[Bot]` prefix for bots; **Numbered** (default) follows the list as
"Player 1", "Bot 2" and so on, renumbering when rows are removed. Switching it
renames generated names but keeps typed ones; a cleared name becomes a numbered
player. **Start game**
stays at the bottom of the window in a slim card-coloured footer that spans the
content area; the note about pausing cloud game events sits under **Input**. The game screen starts with an **End game** exit button
on the left, above rounded details: the game
(X01), the input (or, with the board hidden, the detection status), the starting
score, the finish and the round. It shows each player's remaining score (centered and sized to fill the card),
three-dart average and last visit. Who is throwing sits in the header row,
level with **End game** and centered over the board (below the badges on narrow
screens). The current visit is a plain line of three fixed-size dart tiles (the
newest one underlined), the visit total, **Undo dart** and **Next turn**. With Local Autodarts input, the
hint about advancing detected visits is the tooltip of **Next turn**.
Averages use the up to 100 recent visits the service keeps.

**Add bot** opens **Bot settings**: choose a play speed (Slow, Normal or Fast,
shared by every bot in the game), then **Auto** or a level from 1 to 11. A level's
label is its approximate three-dart scoring average, from 20+ to 120+. **Auto**
opens **Auto difficulty** under the play speed: **Easy**, **Medium** or **Hard**.
An auto bot aims for about 85%, 100% or 115% of the human players' three-dart
average in the game (busts count as zero). A proportional gap feels alike for
beginners and strong players, unlike a fixed number of points. Before anyone has
thrown it assumes 45, weighted as three visits, so one visit does not swing it;
the result maps to an accuracy between the fixed levels, limited to levels 1 to 11
(about 25 to 125). The bot's visits vary naturally because it keeps aiming and
scattering like the fixed levels, without extra randomness. Select a bot's level
in the player list to change it. Bots throw simulated darts in the service: each
dart is aimed at T20, at the first dart of a checkout route, or at a dart that
sets one up, then scattered on a standard board geometry by an amount calibrated
per level. A bot visit advances on its own after a short pause. During a bot's
visit, manual darts, **Undo dart**, **Next turn** and detected darts are ignored;
with Local Autodarts input, keep the board clear while a bot throws.

**Dartboard** (Show by default) puts the board in the middle of the game screen,
sized so the whole game screen fits the window without scrolling: after each
layout change the page measures any overflow and shrinks the board by that much.
Manual entry opens from the highlighted dashed outline of the next dart slot.
Phones, and windows too short for a 300 px board or eight players, still scroll. Players are seated clockwise around it: player 1 at the
top left, then down the right side and back up the left (with three players, 1 on
the left and 2 and 3 on the right; with four, 1 and 2 on top and 4 and 3 below),
with the current visit in a line below the board. The game
screen uses the full content width, and each side's cards are centered in the
space between the board and the sidebar or the window edge. Vertically, each side
spans the board and visit line with equal space above, between and below its
cards. On wide screens a single player’s card and current visit sit to the left of
the board, with the board slightly right of center. On narrow screens, and with the board hidden, the players form a row above the board
and visit. With Local Autodarts input, a halo around the board shows detection:
red when stopped or unavailable, yellow while waiting, and green when scoring;
its tooltip names the state, and details appear under the header. A dart
with a position gets a numbered dot; a dart without one, such as a manual dart,
lights up the segment it hit. The newest dart pulses or blinks; reduced-motion
settings stop the animation. The board clears when the next visit starts. Bot
darts always have a position. Detected darts keep the `coords` that the
Autodarts detector sends with each throw, read as `{x, y}` with 1.0 at the outer
edge of the double ring and y pointing up; positions outside ±10 or that are
not numbers are ignored without rejecting the dart. That orientation and scale
have not been confirmed against a live board.

Choose **Manual darts** to play without internet. Open the next dart slot,
choose a ring, then tap a number, Bull or Miss in the popup. **Undo dart** removes the most recent dart in the current
visit; select **Next turn** after removing the darts. **Recent visits**, a floating
button at the bottom right (icon only on phones), opens a dialog listing up to
100 visits, newest first. **End game** keeps the last result until another local game is
started. After a win, **Rematch** starts the same game again and **New game**
returns to setup.

The active game is atomically saved to `data/core/local-game.json` after every
change and resumes after an application restart. Historical events are not
replayed. Starting local play pauses the cloud event task; ending local play
restarts it with a silent baseline. Cloud matches are not changed or ended.
Integration settings and browser sound controls live under **Integrations**;
the local play screen contains only game controls.

The sidebar's **Local** indicator reports whether local Autodarts is running and
detecting, independently of whether a game is active. It is green for active throw
or takeout detection, amber while starting, calibrating or waiting, and red
when stopped or unavailable. A manual game does not change detector readiness.
Detector readiness alone does not confirm support for offline scored throws.

## Local Autodarts event input

**Local Autodarts** reads the detector already managed by Oche, at
`http://127.0.0.1:3180`. It does not install a detector, downgrade it,
change its configuration, or start a second server. There is no cloud fallback.

`app/core/autodarts/local.py` connects to `/api/events?type=state` over WebSocket
and reads the `data` object from each `{"type":"state","data":...}` message.
It also reconciles `/api/state` after two seconds without an event. If WebSocket
connection setup fails, it falls back to HTTP polling every 200 ms. A lost stream
clears scoring readiness; reconnection must establish a safe clear-board baseline.
Events buffered during connection setup are part of that silent baseline.

The feed starts when a local game selects Autodarts input, the Events screen is
visible, debug recording is enabled, or a raw event WebSocket client subscribes.
It closes when no consumer needs it; the Events screen's monitoring lease expires
ten seconds after its last request. Monitoring does not start a game or generate
normalized game events. Raw local frames are tagged `source: autodarts-local`,
`channel: local`, `topic: board.state` and appear under **Raw events** on `/events`.
Both event views offer **All sources**, **Cloud** and **Local** filters. Pausing
the display leaves monitoring and recording running while that screen is visible.
The feed shares Oche's lifecycle, HTTP client, event bus and application port.
It only reads the detector; it never issues start, stop, reset or calibration
commands. Transport, endpoint, message counts and last-message time appear in
`/api/local` under `detector`.

For a detector on another LAN computer, set the following in this project's `.env`
and restart Oche (recreate the container when using Compose):

```dotenv
OCHE_CORE_LOCAL_AUTODARTS_URL=http://192.168.1.99:3180
```

This URL also controls the Local readiness indicator. The Autodarts process
indicator continues to describe Oche's bundled detector. Camera controls continue
to operate on the bundled detector; the event-source override does not redirect
camera management. No account credentials are needed to read the local feed.

Automatic scoring requires a local API that supplies scored segments. The adapter
accepts the following cumulative state contract:

```json
{
  "status": "Throw",
  "event": "Throw detected",
  "numThrows": 1,
  "throws": [{"segment": {"number": 20, "multiplier": 3}}]
}
```

The initial baseline must report `numThrows: 0`, an empty or omitted `throws` array, and
explicit readiness (`Throw`, `Ready`, or `Ready for throw`). Darts already on the
board when connecting are ignored. Repeated snapshots do not score twice. Corrections
to existing segments retain dart IDs and produce normalized correction events.
Takeout snapshots do not delete scored darts; a confirmed takeout advances the
visit, including a short visit. A manual detector reset preserves the visit and
requires the user to clear the board and select **Next turn**.

After a connection loss or application restart, detection waits for a clear
board. If the current visit contains scored darts, **Next turn** explicitly
accepts that visit after clearing the board; throws missed during the outage
are not inferred. An in-flight response from the previous turn cannot score
for the next player.

These notes were recorded with Autodarts v2.0.2. A live v2.0.2 LAN board was reachable
at port 3180 and accepted both HTTP state reads and a local WebSocket subscription.
After detection was started with the user's authorization, its WebSocket supplied
a scored T20; a later HTTP takeout snapshot contained T20 and S19. Empty snapshots
omitted `throws`. Reduced captured states are covered by replay tests, producing
two normalized local throws and a 79-point visit. If a poll first sees a final dart
in the takeout snapshot, the adapter accepts a strict extension of the known darts;
partial removal snapshots never undo or replace the scored visit.

A stopped detector preserves the interrupted visit and reports `Stopped`, rather
than asking to clear the board. Detection must run again before accepting the
interrupted visit and advancing to another player.

In an earlier isolated, network-disabled
container with no credentials or cameras, its `/api/state` returned
`{"connected":false,"event":"Starting","numThrows":0,"running":true,"status":"Starting"}`,
without `throws`. Startup also reported that calibration needs the cloud.
This proves API reachability, not offline camera scoring. The official
[headless installation guide](https://docs.autodarts.com/getting-started/detection/headless-installation/)
states that the old Board Manager is unsupported from version 2 onward.

**Automatic offline scoring with the bundled v2 detector remains unverified.**
The live throw test ran while the board remained connected to its cloud service;
it confirms a local scored-throw feed, not operation with the internet disconnected.
When `numThrows` is positive but scored throws are absent, Oche reports an
unsupported local feed and emits no scoring events. A calibrated board tested
with internet disconnected is required before declaring automatic offline play
supported. Complete live local-game scoring and physical integration output also
remain separate acceptance items.

## Integrations and API

Local games produce the same `MatchFrame` structure as cloud play and pass it
through `EventNormalizer`. Throws, corrections, removals, busts, visit totals,
player changes, takeout, checkouts and wins reach the existing event bus. WLED (both List and Visual views)
and Caller use the selected local game snapshot through
`Runtime.game_state()` and `/api/game`. Source switching is exclusive.

Each event integration has **Game sources** switches for **Autodarts Play** and
**Local play** on its card and settings page, with explicit **On/Off** labels.
Both are on by default for compatibility
with existing settings. The choices persist with the integration configuration;
Caller choices follow the active profile. Source selection is independent of
whether a cloud game's player uses the local physical board.

Disabled sources do not generate caller calls or lighting game effects. Lighting retains its waiting state when the selected source
is excluded. Queued events from the previous source are ignored after switching.
Manual integration tests remain available regardless of source selection.

Enable integrations in their existing settings. Caller voices must be installed
before disconnecting the internet. Browser output requires enabling sound on
the Caller settings page. Home stays attached when navigating to local play so
its browser player can continue. LAN WLED controllers and
the Oche host must remain reachable even when internet access is disconnected.

Game controls share the main application lifecycle, port and origin guards:

| Method | Path | Action |
| --- | --- | --- |
| GET | `/api/local` | Saved game, display snapshot and detector status |
| POST | `/api/local/start` | Start with `players`, `starting_score`, `double_out`, `input`, `bot_speed`, `show_board`, `random_names` |
| POST | `/api/local/stop` | End local play and resume cloud processing |
| POST | `/api/local/throw` | Manual dart: `number` and `multiplier` |
| POST | `/api/local/undo` | Undo a manual dart in the current visit |
| POST | `/api/local/next` | Accept the current visit and advance |

Each entry in `players` is a name, `{"name": ..., "bot": 1-11}` or
`{"name": ..., "auto": "easy" | "medium" | "hard"}` (not both); a plain name is a
human player. `bot_speed` is `slow`, `normal` (default) or `fast`.
`show_board` and `random_names` default to `true`; `random_names` only restores
the setup choice. Darts in `/api/local` frames include `coords`
when a position is known.
Input is `autodarts` (default) or `manual`. Manual throw and undo controls are
disabled for detector input to prevent diverging detection and scoring state.

## Local tournaments

Open **Tournament** beside **Choose a game** in Play. When no tournament is running,
new tournament setup opens directly, with **Past tournaments** listed below a divider.
If one is running, its card appears above the history instead. Enter a tournament name and
2–32 distinct player names using the numbered player rows (or **Paste player names**), then choose the format and X01 rules. Each match is
one 301/501 leg, with manual scoring or the vision engine selected in Play. Only
one match runs at a time on this board.

- **Single elimination:** one loss eliminates a player. Registration order is the
  initial seeding; higher seeds receive byes where needed.
- **Double elimination:** the first loss sends a player to the lower bracket;
  the second eliminates them. If the lower-bracket champion wins the grand final,
  a reset final decides the tournament, so the upper-bracket champion also gets
  two chances.
- **Swiss rounds:** choose the number of rounds. Players are paired by similar
  points, avoiding rematches where possible. An odd field gives a bye to the
  lowest-ranked player among those with the fewest previous byes. A win or bye is
  worth one point. Rankings use points, then opponents' total points (Buchholz),
  then registration order; a bye adds no opponent points.
- **Swiss + elimination:** after the Swiss rounds, the top 2, 4, 8, 16 or 32
  qualifiers are seeded into a single-elimination final bracket.

Use **Tournament** from a match to leave it in progress, then **Resume match** to
return. A winning dart saves the tournament result automatically. Completed
results are final, including for detected games. Ending an unfinished match lets
you replay it from the start; it does not award a win. **End tournament** cancels
the remaining matches and retains the played results. The tournament list keeps
completed and ended events alongside the active tournament; select an event to
review its standings and bracket. Creating another tournament preserves this history.

Tournament history is saved in `tournament.json` beside `local-game.json`. Existing
single-tournament saves are carried over automatically. The local game
also saves its tournament and match identifiers, so a result can be recovered
after a restart even if the bracket write was interrupted. These files contain
player names and use the same private, atomic storage as other local game data.

Checkout suggestions appear in the remaining dart slots as muted **Suggested**
labels. They update after each throw or undo and disappear when the visit ends.
Actual darts retain their scored appearance; selecting the next manual slot still
opens score entry without automatically applying the suggestion.

## Football

Choose **Arcade → Football**. Two players share a ball across all 82 dartboard
cells: bulls, inner singles, trebles, outer singles and doubles. Fixed and adaptive
bots can also play. D6 is the right goal and D11 the left; the goal labels sit in
the goal boxes, outside the playing field.

### Setup and bull-up

- **Goals:** first to 3 or 5 (default).
- **Rounds:** 10, 16, or a custom 2–200 rounds. Choose penalties, golden goal, or a
  draw as the tiebreaker. Optional stoppage time randomly adds 0–2 complete rounds
  per half; the allowances are saved with the game.
- **Kick-off:** outer bull only (default), either bull, or either bull plus any inner single.
- **Movement:** strict shared edges (default), or beginner diagonals as well.
- **Own goals:** off by default.

Both players throw one dart for the bull-up. The closest starts; equal distances
(within 0.01 mm) repeat the bull-up. Manual input asks for the measured distance
from the centre in millimetres; vision uses the hit coordinates. The ball starts
at the centre regardless of where the winning bull-up dart lands.

### Play and goals

Each visit allows three darts. A missed opening dart consumes a dart but the next
dart can still attempt kick-off. Once opened, the bull is an ordinary cell. Only
neighbouring cells move the ball; misses, the same cell, and non-neighbours leave
it still. Sideways and backward movement is allowed. Numbered sectors wrap around
(20 neighbours 5). Strict inner S17 neighbours inner S3, inner S2, T17 and outer
bull; beginner mode also allows T3 and T2. Players share the ball and continue
from its position on their next turn.

Player 1 initially attacks D6 and Player 2 D11. A legal move onto the attacking
goal scores. With own goals off, hitting your own goal leaves the ball still.
With own goals on, a legal move onto it scores for the opponent. A goal forfeits
remaining darts, resets the ball to the centre, and gives the conceding player
the next kick-off. Own goals can give the same player an extra visit. A round
ends once both players have had at least one visit; an extra own-goal visit does
not by itself advance the clock.

### Half-time and full-time

In Goals mode, half-time is due when either player reaches 2 of 3, or 3 of 5 goals.
In Rounds mode it is due at the midpoint, after any first-half stoppage rounds.
Always finish the current round first, then reset the ball, swap goals and let
the player who did not start the game kick off the second half. Odd round counts
put the extra round in the second half. Reaching the winning goal ends Goals mode
immediately, even if half-time was pending.

Rounds map to 90 minutes: 10 rounds show 9′, 18′, … 45′, then 54′, … 90′. Each half
maps separately to 45 minutes for odd counts. Added rounds show 45+1′, 90+2′, etc.
Half-time, full-time and final-minute notices appear on the pitch. After the final
round and stoppage time, the higher score wins; ties use the selected tiebreaker:

- **Penalties:** one dart each directly at the assigned goal double, without moving
  the ball through neighbouring cells. Both players shoot before a result is
  decided. Repeat tied pairs until one scores and the other misses. Penalty totals
  are shown separately from match goals.
- **Golden goal:** resume from a centre kick-off; the next goal wins.
- **Draw:** end the match with equal scores.

Transitions occur after removing darts or selecting **Next turn**, preserving
undo during the current visit. State, distances, half, clock and scores survive
restarts.

### Input and layout

Manual scoring distinguishes inner and outer singles. Vision maps coordinates
to cells and pauses for confirmation within 1.5 mm of a wire. Confirm the actual
cell in the manual scoring dialog; repeated detector snapshots retain the
confirmation. Without coordinates, singles and bull-up cannot be guessed.

The pitch and reference dartboard scale to available viewport height; small
screens place the compact board and input controls below the pitch. The board
highlights playable targets, and the pitch shows the ball's cell.

This is Oche's custom soccer rule set, using the left/right D11–D6 field idea from
[GLD's football darts description](https://gldproducts.com/blogs/all/how-to-play-football-darts).
It does not use that article's American-football downs, touchdowns or extra points.
