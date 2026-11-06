# Validation

## X01 dartboard look (2026-11-06)

- Automated: 830 Python tests passed, 2 skipped and 82 subtests passed. Both
  JavaScript files passed 71 tests, including the board's single wire layer (six
  rings, twenty spokes) between the beds and the numbers.
- Browser acceptance (headless Microsoft Edge, 1400 × 900 at 1× and 2×, dark and
  light): a manual X01 game with S20 (outer), T20 and S1 (inner) shows each dart's
  bed tinted yellow, darts 1–3 as cream, cyan and coral dots numbered above, and
  the newest pulsing. Not verified: detected darts from a running engine, the
  faded board while detection is stopped, and the football boards that share the
  drawing.
- Then, following OcheCore's live page: the X01 visit line sits above the board,
  with larger dart and visit text and no double or triple colours; Undo dart is
  gone, and the last manual dart is a button that takes it back (checked in the
  rendered page and through `/api/local/undo`); the surround is 222 mm with the
  numbers at 199 mm. Headless Edge, dark theme, 1400 × 900.
- Then: the game's badges sit above End game, who throws sits under the
  board, empty dart slots read "–", and the X01 board uses OcheCore's 9.7 mm
  rings and a soft shadow (football keeps Oche's scoring rings). Headless Edge,
  light theme, 1400 × 900, beside a screenshot of OcheCore's live page.
- Then: the visit sits beside End game at its full 640 px width, and the game
  screen fills the window so its footer (badges at the bottom left, who throws in
  the middle) is on the Recent visits line. The board grows from about 510 to
  675 px at 1400 × 900. A football game keeps its pitch and reference board in
  view with the same footer. At 600 px wide the header and footer stack and
  nothing overflows. Headless Edge, light theme.

## OcheCore scoring and the Autodarts 2.1.1 board format (2026-11-05)

- Automated: 825 Python tests passed, 2 skipped and 82 subtests passed. Both
  JavaScript files passed 71 tests. Ruff on the CI scope and the changed tests,
  translation coverage and whitespace checks passed.
- Replay, not committed: the board states Oche recorded from the installed
  Autodarts 2.1.1 board (263 states, 18 visits, 53 darts) and OcheCore's contract
  fixture (`autodarts-2.1.1-board-state.jsonl`, 123 states, OcheCore `76e0b19`)
  each went through `LocalGames.consume_board` without an error. With four 501
  players, all 53 darts scored, every takeout advanced the visit (18 `turn_end`,
  `takeout_started` and `takeout_finished` events) and no `manual_reset` was
  emitted. Before this change every state with darts was rejected as an
  inconsistent throw count.
- A second recording (`autodarts-20261105T220437178595Z-9bc003b4.jsonl`, 137
  states, 12 visits) adds real single bulls (bed `Single`, name `25`), double bulls
  (bed `Double`, name `Bull`) and misses (`M15`, `M18`). It also shows a bull 1.6 s
  after a takeout, cleared by the board's reset 2.06 s after that takeout. Oche used
  to stop scoring there for the rest of the recording; it now drops that dart as
  Autodarts does (S25, S25, S10) and scores all 12 visits, emitting one
  `manual_reset`.
- Committed tests replay one captured visit, the reset that clears a quick dart,
  OcheCore's chosen encoding of bulls, misses and unplaced darts, camelCase
  readiness, and the board URL following the chosen engine (Autodarts 3180,
  OcheCore 5180, manual and a LAN override).
- Docker, browser and hardware acceptance were not run: no game was scored from
  live throws with either engine, and OcheCore's states were not read over its
  real port.

## Navigation and football defaults (2026-11-05)

- Automated: 813 Python tests passed, 2 skipped and 82 subtests passed. Both
  JavaScript files passed 71 tests, including direct browser navigation, legacy
  URL canonicalisation, football defaults and the absence of nested Oche
  documents.
- Ruff on the repository's configured CI scope, translation coverage, JavaScript
  syntax and whitespace checks passed.
- WLED event automation remains in the server-side runtime. The navigation change
  does not alter effect dispatch; WLED list edits now retain the existing
  unsaved-changes protection when leaving the page.
- New football games default to rounds, bull-or-inner-single kick-off, beginner
  movement and own goals. The reference dartboard is shown in the right panel.
- The retired Visual WLED import, migration notice and backup path were removed.
  Existing obsolete metadata is discarded on load, and an old `wled2.json` is
  ignored instead of being merged.
- Runtime and sidebar versions now resolve from the release VERSION file, project
  metadata or installed package instead of a hardcoded application constant.
- Browser interaction and visual acceptance were not run for this change.
- Docker and hardware acceptance were not run. No camera or physical WLED
  controller was used.

## Integration status and manual fallback (2026-11-04)

- All 721 Python tests and 82 subtests passed. Sidebar/API checks cover no enabled
  engines, both enabled engines, explicit Manual selection, engine selection,
  and disabling the last engine.
- Both JavaScript test files passed, covering live indicator replacement,
  inactive engines, combined service/detection/event health, and local event
  connections without a cloud dependency. Translation, changed-file Ruff and
  whitespace checks passed.
- These are automated checks, not physical camera or cloud-account acceptance.
  OcheCore still reports unsupported scoring rather than claiming readiness.

## Engine setup and installation (2026-11-04)

- All 720 Python tests and 82 subtests passed, including both engines' terminal
  origin/input/session-limit/cleanup checks, PTY client arguments, automatic
  installation, retries and updater locking.
- Both JavaScript test files passed, including installation progress, error and
  retry controls. All four translation catalogues passed their coverage check.
- Engine pages render the shared setup terminal and links to their HTTP UIs.
  Existing installed engines are reused; background installation does not start
  an engine or take cameras away from the selected engine.
- Real camera setup and a browser session against the engine binaries were not
  exercised. Python tests ran outside the sandbox because even a minimal FastAPI
  TestClient request hung on its internal socket communication inside it.

The automated suite covers authentication, reconnect, event normalization, caller
and voice handling, lighting and simulation, profiles, camera tools,
terminal boundaries, browser origin checks, HTTPS, release validation, activation
and rollback state.

Interface checks cover duplicate IDs, local assets, shared theme persistence,
configuration, a single runtime, restart serialization, and a sidebar that reaches
every Board and System page and Settings with exactly one current item. Design tokens live in
`theme.css`; Home's integration styling is scoped to `.core-workspace` and maps
those tokens.

Verified:

- 530 Python tests passed, with 64 subtests; the Linux PTY test was skipped on Windows.
- 24 JavaScript helper tests passed, and all standalone/inline scripts passed syntax checks.
- Ten pages rendered with valid IDs and settings behavior.
- The Python wheel built with the complete runtime and presentation assets.
- Docker Compose configuration and the Linux AMD64 image build passed.
- The running AMD64 container served 12 main pages/APIs and restarted successfully
  through its launcher, producing a healthy new boot.
- The container exported a valid application package with the integrated runtime
  and UI. Stable launcher files were excluded; no separate Core CLI, package or
  listener on port 9180 was present.

Browser checks (headless Microsoft Edge on Windows, 2026-10-23, sidebar redesign;
separate from the automated suite):

- Every page was captured in dark and light themes at 1440 px and at a 390 px
  phone viewport, without horizontal scrolling.
- Collapsing to the icon rail, keeping that state on reload, and expanding again worked.
- Sidebar links from Settings opened Home sections, and Home's own sidebar switched sections.
- The phone menu opened and closed with Escape; the theme toggle switched themes.
- No script errors were reported on the visited pages.

Remaining acceptance checks:

- Touch use of the sidebar and camera tools on a real phone or tablet.
- ARM64 image build and execution.
- Real AutoDarts account login, board play, reconnect, caller audio and WLED devices.
- Camera permissions, calibration and focus on the board host.
- A published application feed exercised inside the container, including rollback.

An unavailable browser or Docker engine does not count as a successful acceptance
check. Native HTTP/restart tests and mocked device tests are recorded separately.

## Local games (2026-10-28)

Automated checks:

- The final Python suite passed: 592 tests and 72 subtests; one Linux PTY
  test skipped on Windows.
- The local-game suite now has 34 passing tests for X01 rules, normalized events
  and Caller visit contracts, undo, persistence, source selection, origin guards,
  duplicate snapshots, corrections, reconnect baselines, manual detector reset,
  malformed feeds and responses arriving after a turn changes.
- All 24 frontend helper tests, Ruff checks and local JavaScript syntax checks passed.

Docker acceptance (separate from automated tests):

- A fresh Linux AMD64 container using the existing `oche:v3` image and the updated
  application source mounted read-only passed with `--network none`: local page,
  manual 301 scoring, normalized throw events, player advance, restart persistence
  and ending local play. No runtime dependencies or image files were changed.
- The bundled Autodarts v2.0.2 binary served its local state API without networking
  in a separate empty container. It reported missing credentials/cloud calibration
  and did not return scored throws. This is not camera-scoring acceptance.

Browser acceptance:

- Not completed. The browser host was unavailable; native Firefox access was
  stopped by Computer Use because URL policy enforcement is unsupported for that
  Windows browser. Layout, touch behavior and audible browser playback remain
  unverified for the new page.

Hardware acceptance:

- Not completed. No physical board, cameras, offline v2 scored-throw feed, WLED
  controller or speaker was exercised. Legacy local API fixtures are synthetic.
- Calibrated detector scoring, unplugged-internet operation, takeout timing,
  corrections, reconnect and actual caller/lighting outputs remain required.

## Play source controls and navigation (2026-10-28)

Automated checks:

- 604 Python tests and 72 subtests passed; one Linux PTY test skipped on Windows.
- 25 frontend helper tests, Ruff and changed JavaScript syntax checks passed.
- New checks cover source defaults and persistence for all four integrations,
  Caller browser delivery, lighting event and phase filtering, rejection of queued
  events from a previous source, clean local play controls, status order and the
  native Windows detection explanation.
- Autodarts, Local and Cloud use a non-wrapping row, including the collapsed rail.

Docker acceptance:

- A fresh Linux AMD64 container, existing `oche:v3` image, updated application
  source mounted read-only and `--network none`: bundled Autodarts was installed;
  all source choices persisted; local throws included their normalized source;
  the local game screen contained no caller or integration controls.
- No new image build, camera play or v2 offline scoring was verified.

Browser and hardware acceptance:

- Not performed for this follow-up. Visual layout of the single-row status and
  source buttons, actual browser sound, cameras and WLED controllers remain
  manual acceptance items. The earlier browser-access limitation is unchanged.

## Local detector status (2026-10-28)

- Automated: 83 relevant Python tests passed, including 13 new detector-health
  cases; Ruff and sidebar JavaScript syntax checks passed. Checks cover a detector
  ready without a game or cloud connection, takeout, startup, calibration, invalid
  responses, API failure, cache invalidation on stop/restart, and manual games
  leaving stopped detection off.
- Docker, browser and hardware acceptance: not performed for this follow-up.
  Live camera state transitions and displayed indicator colors remain unverified.

## Sidebar failure descriptions (2026-10-28)

- Automated: all 31 frontend tests and five workspace tests passed; JavaScript
  syntax and diff checks passed. New cases cover red failure states, missing
  native detection, sign-in and board setup, offline boards, cloud error details,
  rejected subscriptions, intentional local-play pauses and healthy connections.
- Known failures use red dots. Connecting, approval, calibration and intentional
  cloud pauses use amber. Tooltips and accessible labels include a cause and a
  recovery action, preserving sanitized error details supplied by the backend.
- Browser, Docker and hardware acceptance were not performed for this follow-up.

## Interface translations and profile appearance (2026-10-28)

Automated:

- 627 Python tests and 72 subtests passed; the Linux PTY test was skipped on Windows.
- 35 JavaScript tests passed, including translation interpolation, dynamic message
  formatting, English fallback, save-before-reload, rejected appearance changes
  and delayed responses after a profile switch.
- All four catalogues passed coverage and placeholder validation (1,187 messages
  per language). Ruff and 72 external/inline JavaScript syntax checks passed.
- Eleven page routes rendered in all four languages. Profile appearance migration,
  clone/select, import/export, deletion, restart persistence and save rollback were
  exercised; user-defined names and runtime identifiers remained unchanged.

Docker acceptance:

- A fresh Linux AMD64 container with `--network none`, the existing `oche:v3`
  image and updated app source mounted read-only served all four languages and
  local catalogue assets, selected the scoreboard locale, restored profile theme
  and language, exported appearance and persisted it across application restart.
- No image rebuild or changes to the stable launcher/runtime were needed.

Browser/hardware acceptance:

- Native visual checks at desktop/mobile widths, dropdown operation, caller audio
  during normal navigation and native-speaker review were not performed here.
- Physical cameras, detection and WLED hardware were not exercised by this change.

## Navigation grouping (2026-10-28)

- Automated: 45 relevant Python tests and five subtests passed, as did all 35
  JavaScript tests, four-language catalogue validation, Ruff, settings JavaScript
  syntax and diff checks. Coverage includes active links, camera discovery under
  Integrations, Autodarts under System, saved page visibility, per-page collapse
  preferences and System visibility when only Autodarts remains enabled.
- Browser acceptance: not performed for this follow-up; visual layout and live
  settings interactions remain manual acceptance items.
- Docker acceptance: not performed for this navigation change.
- Hardware acceptance: not performed; camera tools and detector behavior were
  not changed.

## Integration switches and Visual WLED loading (2026-10-28)

- Automated: 64 relevant Python tests and all 40 JavaScript tests passed, along
  with catalogue validation, Ruff and diff checks. The frontend suite now parses
  every Home JavaScript module in CI, covering the duplicate declaration that
  prevented Visual WLED from initializing.
- Regression checks execute Visual WLED initialization and On/Off/unavailable
  rendering, synchronize duplicate game-source switches, persist camera sidebar
  visibility, preserve state after rejected saves and reject stale visibility
  polls after newer changes. Source filtering and persistence checks also passed.
- Browser acceptance: not performed for this follow-up; visual switch layout and
  native browser interactions remain manual acceptance items.
- Docker acceptance: not performed for this frontend change.
- Hardware acceptance: not performed; camera detection and physical WLED output
  were not changed.

## Source choices on initial Integrations load (2026-10-28)

- Automated: all 42 JavaScript tests and 232 relevant Python tests passed, as did
  catalogue and diff checks. New tests load the actual WLED and Caller scripts
  directly on Integrations, check saved source states and later configuration
  changes, and verify that opening Caller still loads its voice choices.
- Native read-only API smoke: health, WLED status, Visual WLED status, Caller
  status, Scoreboard settings and sidebar settings returned HTTP 200 on port 8180.
  The source switches' misleading Unavailable labels came from deferred client
  configuration loading, despite successful service responses.
- Browser acceptance: not performed for this follow-up.
- Docker acceptance: not performed for this follow-up.
- Hardware acceptance: not performed; this change only loads saved settings.

## Local detector event adapter (2026-10-28)

Automated:

- 642 Python tests and 72 subtests passed; one Linux PTY test was skipped on
  Windows. All 42 JavaScript tests, four-language catalogue checks and Ruff passed.
- New tests exercise a real loopback WebSocket server, normalization of throw and
  correction events, duplicate suppression, omitted empty `throws`, takeout,
  reconnects during a visit, silent startup backlog, polling fallback, socket
  cleanup, endpoint validation and remote readiness without a local binary.
- Existing interrupted-request, scoring, persistence and integration source
  filtering tests still pass.

Docker acceptance:

- A fresh `oche:v3` Linux AMD64 container with updated application source mounted
  read-only and `--network none` passed a synthetic local WebSocket game: empty
  baseline without `throws`, one T20 delivered twice, one normalized local throw,
  takeout completion and automatic player advance. No image rebuild was performed.

Browser acceptance:

- Not performed for this backend change.

Hardware acceptance:

- Read-only access to the user-provided LAN board at `192.168.1.99:3180` returned
  version `2.0.2`, a running/connected `Throw` state with no darts, and accepted
  `/api/events?type=state`. A subsequent `Takeout started` event and `Takeout in
  progress` state were observed; both omitted `throws` with `numThrows: 0`.
- The new adapter connected to that board over WebSocket. No reset, start, stop,
  calibration, configuration or update commands were sent to the board or old Oche.
- The board remained in takeout during observation. No scored physical throw or
  disconnected-internet camera session was captured; offline scoring acceptance
  remains pending.

## Live local throw capture and detector stop handling (2026-10-28)

Automated:

- 644 Python tests and 72 subtests passed; the Linux PTY test was skipped on
  Windows. Ruff, four-language catalogue validation and diff checks passed.
- Reduced live states replay as T20 and S19, producing two normalized local
  throws and a 79-point visit (422 remaining from 501). Tests cover a final dart
  first seen in a takeout snapshot, duplicate snapshots, partial removal, and
  stopped detection preserving the visit while clearing scoring readiness.

Docker acceptance:

- A fresh `oche:v3` container with updated app source and the reduced fixture
  mounted read-only, `--network none`, replayed T20/S19, advanced on a clear-board
  control snapshot and reported stopped detection without scoring readiness.

Browser acceptance:

- Not performed for this follow-up.

Hardware acceptance:

- The LAN detector subsequently reported `Stopped`, with all cameras at 0 FPS.
  At the user's explicit request, `PUT /api/start` returned HTTP 200; the detector
  reported `Throw` / `Started`, and camera rates reached 18, 11 and 16 FPS.
- The local WebSocket delivered a real scored T20 (60). A later HTTP state
  contained T20 and S19 (19) during takeout. The temporary local game had joined
  after the first dart, so it ignored that existing visit and armed after the
  clear-board transition. Complete live local-game scoring was not claimed.
- These observations confirm a scored local v2.0.2 feed while cloud connectivity
  was still present. Internet-disconnected physical play and physical integration
  output remain unverified. No reset or calibration commands were issued.

## Camera Enhancer naming (2026-10-28)

- Automated: 16 workspace and localization tests passed; all four catalogues
  passed coverage and placeholder validation. The active navigation label, page
  title, Integrations card and sidebar settings now use Camera Enhancer.
- Browser acceptance: not performed for this text change.
- Docker acceptance: not performed for this text change.
- Hardware acceptance: not performed; camera behavior was not changed.

## Direct routes, local event diagnostics and device history (2026-10-28)

Automated:

- The full Python suite passed: 654 tests and 72 subtests; the Linux PTY test was
  skipped on Windows. Final targeted workspace/history checks also passed. All
  48 JavaScript tests, catalogue validation, Ruff and diff checks passed.
- Routes reload directly under `/integrations/...`, `/profiles` and `/events`.
  Legacy camera URLs redirect to `/integrations/cameras`; old hash links resolve
  to their direct routes. Tests preserve camera state on back navigation, reuse
  the Home document, and apply the latest route when an iframe loads late.
- Local raw frames are captured while Events is visible, recording is on or a
  raw client subscribes. Tests verify capture without a game and no fabricated
  normalized game events. The viewer distinguishes and filters cloud/local data.
- History tests cover background sampling, retention pruning, sample limits,
  disabling/clearing, persisted preferences, API validation and lifecycle cleanup.
  Frontend checks cover timestamp spacing, missing values, configuration saving
  and rejected edits. No 60-minute wall-clock soak was performed.
- Measured sample-buffer object memory with nine numeric fields per sample:
  59.4 KiB at the 120-sample default; 2,424.2 KiB at the 5,000-sample limit. This
  excludes the rest of Oche, response serialization and browser rendering.

Docker acceptance:

- A fresh Linux AMD64 `oche:v3` container with current app source mounted
  read-only and `--network none` served direct routes, redirected the camera alias,
  captured mocked local raw states without a game, collected default history,
  cleared it when disabled and restored the disabled preference after restart.
  System status rendered in all four languages. No image rebuild was performed.

Browser acceptance:

- The computer-use/browser inventory reported `The browser access host is
  unavailable`. Native visual checks and real browser interactions remain pending;
  automated retained-navigation and form tests are recorded separately above.

Hardware acceptance:

- No camera, physical WLED controller or live-board commands were used for this
  follow-up. Previous local throw acceptance is unchanged.

## Compact history toolbar (2026-10-28)

- Automated: 17 workspace/localization tests and all 48 JavaScript tests passed;
  catalogue validation and diff checks passed. The title, toggle, retention,
  interval, Save button and sample count now share one non-wrapping row. Full
  descriptions remain available through tooltips and accessible labels.
- Browser acceptance: visual checks remain pending because the browser host is
  unavailable. Narrow screens can scroll the toolbar horizontally.
- Docker acceptance: not repeated for this layout change.
- Hardware acceptance: not repeated; sampling behavior was not changed.

## Play on Oche game screen (2026-10-28)

- Automated: 654 Python tests (one skipped, 72 subtests) and all 50 JavaScript
  tests passed, as did catalogue validation, Ruff and diff checks. New JavaScript
  tests cover checkout suggestions (preferred doubles, darts left, bogey scores,
  straight out) and averages from completed visits only.
- Browser acceptance (headless Microsoft Edge on Windows, application started from
  the branch with a temporary data directory and an unreachable detector URL):
  game mode tiles, X01 setup with player add/remove and segmented options, a manual
  three-player 501 game through the tap keypad, Next turn, visit history, checkout
  hint at 61, a 301 straight-out win with Rematch/New game, light theme, a 390 px
  mobile layout and the Local Autodarts waiting state. Scores matched the service
  (361/444/501) and the only console error was the existing missing favicon. The
  run found and fixed blank player names blocking Start game.
- Not performed: interactive use in a native desktop or touch browser, Docker
  acceptance and hardware acceptance. Scoring and detector code were not changed;
  detected-dart display on the game screen with a live board remains a manual item.

## Play on Oche bots and setup layout (2026-10-28)

- Automated: 665 Python tests (one skipped, 72 subtests) and all 53 JavaScript
  tests passed, as did catalogue validation and Ruff on the changed files. New
  tests cover board geometry, bot aiming and checkout routes, level averages,
  self-playing bot visits that block manual input, ignored detection during bot
  visits, bot level validation, unique playful default names, kept and rejected
  dart positions, and board segment keys and dot placement. Level scatter was
  calibrated by simulation to about 10 × level + 15 points per three darts at T20.
- Browser acceptance (headless Microsoft Edge on Windows, application started from
  the branch with a temporary data directory): the colored **Play on Oche** sidebar
  entry, the larger back button, the combined players and settings panel with
  settings stacked vertically and centered, a one-player start with random playful
  names (with a `[Bot]` prefix for bots), the bot dialog, a changeable bot level, the
  bottom **Start game** bar, a 390 px mobile layout (which found and fixed roster
  rows overflowing the panel), dark and light themes, and a manual game in which
  a level-6 bot threw and advanced its own visits with controls disabled. The
  Dartboard setting showed the board beside the visit; manual T20 and S5 lit their
  segments with the newest blinking, and bot darts appeared as numbered dots with
  the newest pulsing. A follow-up check showed the settings as labelled fields
  with full-width 40 px choices and descriptions, the detection status inside the
  Input field, a centered 680 px setup column in which players and settings share one width, a slim card-coloured start footer (44 px button, hint moved under Input) spanning the content area from the sidebar to the right edge, one playfully named player when an earlier two-player game was
  saved, and no horizontal scrolling at 390 px in either theme.
- Game screen layout (same headless setup, games started over the API with
  manual input): the board centered with one player on the left and the visit on
  the right, two, three and eight players split around the board with the visit
  below it, the board hidden with one and two players, and one and two players at
  390 px without horizontal scrolling.
- Player names setting (same headless setup): Numbered renamed the generated
  player to Player 1, added rows as Player 2, Bot 3 and Player 4, renumbered them
  after removing the first row, switched generated names back to playful ones on
  Random while keeping a typed name, started a game with the numbered names, and
  restored Numbered with a Player 1 row after reloading the page.
- Game header and visit (same headless setup, both themes): the settings in the
  order starting score, finish, player names, dartboard and input; the End game
  exit button at the top left with game, status, score, finish and round badges
  below it; a larger board; fixed-size dart tiles in one row with the visit total and
  actions under the board, and beside a single player; and the header stacked on
  the left at 390 px.
- Visit line, history dialog and detection halo (same headless setup, both
  themes): the current visit as a line under the board for one, two, three and
  eight players and with the board hidden; the Recent visits dialog listing a
  manual visit; a red halo and no detection badge for a Local Autodarts game on
  native Windows, where detection is unavailable; and the 390 px layout. The
  yellow and green halo states were not seen because no detector ran.
- Single-player board and floating history (same headless setup): one player with
  the header, card and visit on the left and the board on the right, centered
  vertically at about 630 px on an 800 px tall window (up from about 490 px), with
  the card and visit centered on the board's line at 800 and 900 px tall, and
  the winner card in the left column after a finished leg, and the Recent visits button floating
  at the bottom right, icon only at 390 px.
- Clockwise seating and larger cards (same headless setup): two, three, four, five
  and eight players seated clockwise with larger score cards, the board kept at full size (about
  500 px on an 800 px tall window) and each card column centered in the
  space between the board and the sidebar or window edge (about 32 px on each
  side at 1400 × 900 and about 104 px at 1920 × 1080, where the game screen now
  uses the full content width and the keypad keeps a 1176 px maximum), and each side's
  cards spread evenly over the board and visit line's height (about 145 px above,
  between and below two cards at 1920 × 1080; about 14 px with four cards at
  1400 × 900); and centered scores sized from each card's width (about
  100 px beside the board at 1400 × 900, 120 px for one player at 1920 × 1080,
  58 px with eight players, capped at 120 px with the board hidden) for one to
  eight players, with the board hidden and at 390 px; and the turn line in the
  header row level with End game, centered over the board for one to eight
  players at both window sizes and under the badges at 390 px; with one player the board stays on the right and the card and visit are
  centered in the space to its left, 24 px between stacked cards, the smaller halo
  clear of the visit line, and four players in turn order at 390 px. A JavaScript
  test covers the seating for one to eight players.
- Auto bots (same headless setup): Auto first in the level grid, the Auto
  difficulty choices appearing under the play speed with Medium focused, an
  Auto · Hard chip that reopens with its choice selected and switches to a fixed
  level, and a manual game where an Easy auto bot played its visits with a
  Bot · Auto · Easy tag. Python tests cover the level formula, its limits, the
  accuracy between whole levels (level 4.5 averaging 54 to 66 per visit over
  6,000 darts), a game that counts only human visits, and invalid auto values.
  Not performed: how auto difficulties feel over full games with real players.
- Fit to the window (same headless setup, measuring page overflow): no scrolling
  for one to eight players with Local Autodarts input, the board hidden, and a
  finished game, at 1400 × 900 and 1920 × 1080; with manual input, no scrolling
  at 1920 × 1080 for one to five players, while eight players (102 px) and
  shorter windows with the keypad (63 to 282 px at 1400 × 900, where the board
  reaches its 300 px minimum) still scroll. Phones scroll as before.
- Not performed: interactive use in a native desktop or touch browser, Docker
  acceptance, hardware acceptance of a bot visit with Local Autodarts input, and
  confirmation of the detector's `coords` orientation and scale on a live board.

## Autodarts in the data folder and runtime 2 (2026-11-02)

Automated:

- The store accepts any valid module name, keeps native releases outside the
  per-runtime folders across image upgrades, flattens the folders older images
  created, and rolls back an interrupted native update without a bundle.
- A first install verifies and stores Autodarts without starting it, repeats as a
  no-op, and is offered as installable without counting as an update. From
  Updates, a first install keeps Autodarts running when it starts on boot.
- Downloads identify as `Oche/<version>`; the installer installs Autodarts in
  Easy mode, defaults to Yes in Detailed mode, honours No, and continues if the
  download fails.

Docker acceptance (Docker Desktop 28.5.1 on Windows, new image built locally,
named volumes, official Autodarts releases over the Internet):

- The image contains only the Oche bundle, with no `/opt/autodarts`.
- The installer's one-off command installed Autodarts 2.1.0 into
  `data/modules/autodarts/versions/2.1.0`, owned by UID 1000; a second run made no
  change. The launcher then started Oche with Autodarts running.
- Python's default User-Agent got HTTP 403 from the Autodarts release server, while
  curl got 200; the `Oche/<version>` User-Agent fixed it. Earlier images had the
  same failure in their in-app Autodarts update check.
- A volume in the older per-runtime layout was flattened on first start, and
  Autodarts ran from it.
- Without Autodarts, Oche started and explained how to install it. After a check,
  Updates offered Install; installing from the app left Autodarts running.
- Not verified: the full `install.sh` run on a Linux board computer, ARM64,
  cameras and calibration with the new layout, or upgrading a real board.

Browser acceptance (headless Microsoft Edge): the Updates row shows a neutral
"Not installed" chip with Install, then "Up to date" after installing.

## Detection engines: Autodarts and OcheCore (2026-11-02)

Automated:

- Autodarts is the default engine; choosing an engine saves it, stops the other
  and starts it when installed; boot starts only the active engine, and only when
  Start on boot is on. The engine API rejects unknown names.
- OcheCore releases are accepted only as stable GitHub releases of
  `mondeggo/ochecore`, with the archive and its `.sha256` from that release page;
  the archive installs flattened into `data/modules/ochecore/versions/<version>`.
- Play on Autodarts is hidden until Autodarts is installed.

Docker acceptance (Docker Desktop 28.5.1 on Windows, image built locally, fresh
named volume, official releases over the Internet):

- Autodarts 2.1.0 installed through `install_module`, and the launcher started it as
  the active engine.
- Installing OcheCore from Updates downloaded v0.1.5 from GitHub, verified it,
  passed the health check on port 5180 and left it stopped while Autodarts stayed
  active.
- Use OcheCore stopped Autodarts and started OcheCore; its page answered HTTP 200
  through the container's published port. It saved `data/ochecore/board.toml`
  (UID 1000) with `bind = "0.0.0.0"` and `port = 5180`, and logged events to
  `data/logs/ochecore.log`.
- Use Autodarts reversed it, and the choice survived a container restart.
- Not verified: cameras, calibration or detection with either engine on the board,
  ARM64, or both engines contending for real cameras.

Browser acceptance (headless Microsoft Edge, dark and light): Integrations shows
the Detection engines section; the active card has the lime lip with
"Active · Running", Stop and "Open OcheCore ↗", and the other offers "Use this
engine".

## Integration registry, Play and vision engines (2026-11-02)

Automated: registered integrations load in a fixed order; switches persist per
integration; Updates list only switched-on integrations; the chosen engine falls
back to the first one that scores, else manual; choosing one stops the others, manual
stops nothing, and a switched-off integration gives up the choice and stops; boot
starts only the chosen engine; Settings accept only the sidebar keys integrations
register. The Python suite also passed in a Linux Python 3.12 container.

Docker acceptance (image built locally, fresh named volume): Autodarts installed
through `install_module` and ran as the chosen engine. Integration pages, Play on
Autodarts, the setup terminal and the integrations' static files answered HTTP 200.
Play offered Autodarts, OcheCore (disabled) and Manual. Switching OcheCore on added
it to Updates; switching Autodarts off stopped it, removed it from Updates, hid Play
on Autodarts and left only manual darts; switching it back on showed it again
without starting it.

Browser acceptance (headless Microsoft Edge, dark and light): Play first with Play
on Autodarts below it, the vision engine choice above the games, the Vision engines
cards with switches, and the OcheCore page. Not verified: the Autodarts setup
terminal, Camera Enhancer and both engines on the board's cameras.

## Autodarts account and status dots in the integration (2026-11-02)

Automated: with Autodarts switched off, `/api/status` has no account or cloud
section, the account API answers 409, Home has no account card, view or script,
and the Autodarts and Cloud dots are hidden while Local stays; switching it on
restores them. Every script in Home and the integrations parses as a module. The
Python suite also passed in a Linux Python 3.12 container.

Docker acceptance (image built locally, fresh named volume): Autodarts 2.1.1
installed and ran. The account settings saved through `/api/autodarts/config`, the
account view and the integration's scripts answered HTTP 200, and the sidebar
listed the Autodarts, Local and Cloud dots in that order. Switching Autodarts off
removed the account and cloud from the status and stopped the process; switching it
on restored them.

Browser acceptance (headless Microsoft Edge): Overview with Autodarts on (board
name, cloud tile, three dots) and off (generic board panel, no cloud tile, no Play
on Autodarts, only the Local dot), and the Autodarts account view. Not verified:
signing in to a real Autodarts account and receiving cloud games.
