# Event fixtures

`x01-state.json` is synthetic, with invented identities. Its structure follows the supplied
Tools for AutoDarts `utils/websocket-helpers.ts` interfaces (`IMatch`, `ITurn`, `IThrow`,
`ISegment`, `IPlayer`). It is not a recording of a live match.

The replay tests build transitions from this state. Newest-first turns, dart IDs that remain
stable during corrections, repeated `finishedAt` updates and winner fields follow
`utils/settle-game-data.ts`, `utils/win.ts` and darts-caller's `process_match_x01`.
Takeout strings follow darts-caller's board handler.

Keep account identities, coordinates, profiles, tokens and private live captures out of fixtures.

`local-v2-state.json` contains reduced v2.0.2 detector states from the controlled
October 28, 2026 hardware test. A ready state and T20 were received over the local
WebSocket; a subsequent HTTP state contained T20 and S19 during takeout. Coordinates
are removed, and no identities, credentials or configuration are included. The
HTTP state is wrapped in the same state envelope for adapter replay. Tests add a
synthetic clear-board transition to verify the resulting 79-point short visit.

`live-visits.json` contains reduced X01 and Cricket visits from the October 2, 2026 debug
capture. Identities and names are replaced; profiles, coordinates and account details are
removed. It preserves the observed zero `finishedAt` timestamp, scores and dart corrections.
Tests pair these states with the captured `Throw`, `Takeout` and takeout event shapes.

`test_caller.py` varies the common match envelope across all 14 modes listed in Tools'
`utils/game-modes.ts`, plus CountUp aliases and Cricket/Tactics settings. These are synthetic
contract tests, not captured matches. Target and Gotcha checkout tests use the field shapes
read by the reference callers; mode-specific live acceptance remains separate.
