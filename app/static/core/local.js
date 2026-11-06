import { tr } from "../js/i18n.mjs";
import { $, api } from "./client.mjs";
import { CRICKET, WDF_RINGS, boardMarkup, boardPoint, checkout, cricketMarks, funnyName, hitKey, hitRegion, playerStats, seats, targetBeds, targetHints, targetLabel } from "./game.mjs";

import { cellDart } from "./football-pitch.mjs";
import { dartRegion, regionLabel, renderFootball } from "./football.mjs";

let selectedMode = "x01";
let footballKeypad = false;
let footballBoard = true;
const maxPlayers = () => selectedMode === "football" ? 2 : 8;
const SVG = "http://www.w3.org/2000/svg";
const DETECTOR = { stopped: "Stopped", waiting: "Waiting", waiting_for_clear: "Clear the board",
  connected: "Connected", unavailable: "Unavailable" };
const AUTO = { easy: "Easy", medium: "Medium", hard: "Hard" };
// Bots outside X01 play by the game's rules at a fixed difficulty; switching modes keeps them close.
const DIFFICULTY_LEVEL = { easy: 3, medium: 6, hard: 9 };
const levelDifficulty = level => level <= 4 ? "easy" : level <= 8 ? "medium" : "hard";
const isBot = player => Boolean(player?.bot || player?.auto || player?.difficulty);
const ROUND_MODES = ["countup", "bobs27", "shanghai"];

/* The title, setup line and rules of each mode. */
function modeInfo(mode) {
  return {
    x01: ["X01", tr("One leg · Single in · Saved on this device"), ""],
    football: [tr("Football"), tr("Two players · Three darts per turn · Saved on this device"),
      tr("Move the shared ball between neighbouring regions. Player 1 attacks D6; Player 2 attacks D11. After a goal, the conceding player kicks off with three fresh darts.")],
    cricket: ["Cricket", tr("15 to 20 and the bull · Saved on this device"),
      tr("Three marks close a number: a single is one mark, a double two and a treble three; the outer bull is one and the bullseye two. Close every number with the best score to win.")],
    atc: [tr("Around the Clock"), tr("1 to 20 in order · Saved on this device"),
      tr("Hit each number in turn; each hit moves you to the next one. The first player to finish wins.")],
    countup: [tr("Count Up"), tr("Eight rounds · Saved on this device"),
      tr("Every dart scores its points. The highest total after eight rounds wins.")],
    bobs27: ["Bob's 27", tr("D1 to D20, then the bull · Saved on this device"),
      tr("Start on 27. Each round, every hit on its double adds the double's value; miss it with all three darts and lose that value. A player below zero is out.")],
    shanghai: ["Shanghai", tr("Seven rounds · Saved on this device"),
      tr("Only the round's number scores. A single, double and treble of it in one visit is a Shanghai and wins at once; otherwise the highest total after seven rounds wins.")],
  }[mode];
}
const TONE = { connected: "badge-green", waiting: "badge-yellow", waiting_for_clear: "badge-yellow",
  stopped: "badge-red", unavailable: "badge-red" };
// Before a game: whether the chosen vision engine is detecting.
const READINESS = { detecting: "Ready", starting: "Starting", calibrating: "Calibrating", waiting: "Waiting",
  stopped: "Stopped", unavailable: "Unavailable" };
const READY_TONE = { detecting: "badge-green", starting: "badge-yellow", calibrating: "badge-yellow",
  waiting: "badge-yellow", stopped: "badge-red", unavailable: "badge-red" };
// The board's halo shows detection: red when stopped, yellow while waiting, green when scoring.
const HALO = { connected: "green", waiting: "yellow", waiting_for_clear: "yellow", stopped: "red", unavailable: "red" };

// An integration can show Play as a TV view (read-only, with a practice board between games)
// or as a remote for a phone or tablet, both without the sidebar.
const DISPLAY = $("game-page").dataset.display || "";
const TV = DISPLAY === "tv", REMOTE = DISPLAY === "remote";
let state = null;
let working = false;
let revision = "";
let generation = 0;
let choosing = false;
let prefilled = false;
// The roster row whose bot level the dialog changes, or null to add a bot.
let editingBot = null;

const radio = name => document.querySelector(`input[name="${name}"]:checked`)?.value;
const setRadio = (name, value) => {
  const input = document.querySelector(`input[name="${name}"][value="${value}"]`);
  if (input) input.checked = true;
};

let shownView = null;
function show(view) {
  // Opening a game collapses the sidebar for room; leaving the game restores it.
  if (!DISPLAY && view !== shownView && (view === "match" || shownView === "match" || shownView === null)) window.ocheSidebar?.game(view === "match");
  shownView = view;
  $("game-page").dataset.view = view;
  $("game-modes").hidden = view !== "modes";
  $("local-setup").hidden = view !== "setup";
  $("local-match").hidden = view !== "match";
  if (TV) $("local-practice").hidden = view !== "practice";
}

/* Players */
function addPlayer(name = newName(), bot = null, auto = null, difficulty = null) {
  const rows = $("local-players");
  if (rows.children.length >= maxPlayers()) return null;
  const row = $("local-player-row").content.firstElementChild.cloneNode(true);
  const input = row.querySelector("input");
  input.value = name;
  // Generated names follow the Player names setting until the user types one.
  row.dataset.auto = "true";
  input.addEventListener("input", () => delete row.dataset.auto);
  row.querySelector(".game-remove").addEventListener("click", () => { row.remove(); renderRoster(); });
  row.querySelector(".game-bot-level").addEventListener("click", () => openBots(row));
  rows.append(row);
  setBot(row, bot, auto, difficulty);
  return input;
}

/* An X01 bot has a fixed level or, as an auto bot, a difficulty relative to the players.
   Other games' bots have a fixed difficulty. */
function setBot(row, level, auto = null, difficulty = null) {
  if (level) row.dataset.bot = String(level);
  else delete row.dataset.bot;
  if (auto) row.dataset.autoBot = auto;
  else delete row.dataset.autoBot;
  if (difficulty) row.dataset.difficulty = difficulty;
  else delete row.dataset.difficulty;
  const chip = row.querySelector(".game-bot-level");
  chip.hidden = !level && !auto && !difficulty;
  if (difficulty) {
    chip.textContent = tr(AUTO[difficulty]);
    chip.setAttribute("aria-label", tr("Change bot difficulty, currently {0}", { 0: tr(AUTO[difficulty]) }));
  } else if (auto) {
    chip.textContent = tr("Auto · {0}", { 0: tr(AUTO[auto]) });
    chip.setAttribute("aria-label", tr("Change bot level, currently auto ({0})", { 0: tr(AUTO[auto]) }));
  } else if (level) {
    chip.textContent = `${tr("Level {0}", { 0: level })} · ${10 + 10 * level}+`;
    chip.setAttribute("aria-label", tr("Change bot level, currently level {0}", { 0: level }));
  }
  renderRoster();
}

function renderRoster() {
  const rows = [...$("local-players").children];
  rows.forEach((row, index) => {
    const input = row.querySelector("input");
    const label = rowIsBot(row) ? tr("Bot {0}", { 0: index + 1 }) : tr("Player {0}", { 0: index + 1 });
    input.placeholder = label;
    input.setAttribute("aria-label", label);
    if (row.dataset.auto && !randomNames()) input.value = label;
    const remove = row.querySelector(".game-remove");
    remove.disabled = rows.length === 1;
    remove.setAttribute("aria-label", tr("Remove player {0}", { 0: index + 1 }));
  });
  $("local-add-player").disabled = $("local-add-bot").disabled = rows.length >= maxPlayers();
  $("local-player-count").textContent = tr("{0} of {1}", { 0: rows.length, 1: maxPlayers() });
}

const rowIsBot = row => Boolean(row.dataset.bot || row.dataset.autoBot || row.dataset.difficulty);

function openBots(row = null) {
  editingBot = row;
  $("local-bot-dialog").returnValue = "";
  const x01 = selectedMode === "x01";
  $("local-bot-levels").hidden = !x01;
  $("local-bot-difficulty").hidden = x01;
  const current = row?.dataset.difficulty ? `difficulty-${row.dataset.difficulty}`
    : row?.dataset.autoBot ? `auto-${row.dataset.autoBot}` : row?.dataset.bot;
  for (const button of $("local-bot-dialog").querySelectorAll("button[name=level]")) {
    button.setAttribute("aria-pressed", String(Boolean(row) && button.value === current));
  }
  showAuto(x01 && Boolean(row?.dataset.autoBot));
  $("local-bot-dialog").showModal();
}

/* Auto opens the difficulty choices under the play speed instead of closing the dialog. */
function showAuto(open) {
  $("local-bot-auto").hidden = !open;
  $("local-bot-auto-open").setAttribute("aria-pressed", String(open));
}

function closeBots() {
  const value = $("local-bot-dialog").returnValue;
  const row = editingBot;
  editingBot = null;
  const auto = value.startsWith("auto-") ? value.slice(5) : null;
  const difficulty = value.startsWith("difficulty-") ? value.slice(11) : null;
  const level = auto || difficulty ? null : Number(value);
  if (!(auto in AUTO) && !(difficulty in AUTO) && !(Number.isInteger(level) && level >= 1)) return;
  if (row?.isConnected) setBot(row, level, auto, difficulty);
  else addPlayer(newName(true), level, auto, difficulty);
}

const randomNames = () => radio("names") === "random";

/* A playful name, or an empty one that renderRoster numbers. */
function newName(bot = false) {
  if (!randomNames()) return "";
  return funnyName([...$("local-players").querySelectorAll("input")].map(input => input.value), bot);
}

function renameGenerated() {
  const rows = [...$("local-players").querySelectorAll("li[data-auto]")];
  for (const row of rows) row.querySelector("input").value = "";
  for (const row of rows) row.querySelector("input").value = newName(rowIsBot(row));
  renderRoster();
}

function configureMode(mode) {
  selectedMode = mode;
  const football = mode === "football", x01 = mode === "x01";
  if (football) footballBoard = true;
  const [title, description, rules] = modeInfo(mode);
  $("local-setup-title").textContent = title;
  $("local-setup-description").textContent = description;
  for (const id of ["score", "out"]) $("local-" + id + "-option").hidden = !x01;
  $("local-board-option").hidden = football;
  $("local-cricket-option").hidden = mode !== "cricket";
  for (const id of ["ring", "bull"]) $("local-" + id + "-option").hidden = mode !== "atc";
  for (const id of ["goals", "kickoff", "movement", "own-goals", "format", "rounds", "tiebreak", "stoppage"]) $("local-" + id + "-option").hidden = !football;
  footballSettings();
  $("local-rules").hidden = !rules;
  $("local-rules").textContent = rules;
  // X01 bots have levels; the other games' bots have a difficulty.
  for (const row of $("local-players").children) {
    if (!x01 && (row.dataset.bot || row.dataset.autoBot)) setBot(row, null, null, row.dataset.autoBot || levelDifficulty(Number(row.dataset.bot)));
    else if (x01 && row.dataset.difficulty) setBot(row, DIFFICULTY_LEVEL[row.dataset.difficulty]);
  }
  while ($("local-players").children.length > maxPlayers()) $("local-players").lastElementChild.remove();
  while ($("local-players").children.length < (football ? 2 : 1)) addPlayer();
  renderRoster();
}

function prefill(value) {
  if (prefilled) return;
  prefilled = true;
  const options = value.options;
  // A fresh setup starts with numbered names; Rematch keeps the previous roster.
  setRadio("names", "numbered");
  $("local-players").replaceChildren();
  addPlayer();
  setRadio("score", options.starting_score);
  setRadio("out", options.double_out ? "double" : "straight");
  setRadio("speed", options.bot_speed);
  setRadio("board", options.show_board ? "show" : "hide");
  setRadio("goals", options.goals_to_win || 5);
  setRadio("football-format", options.football_format || "goals");
  setRadio("football-rounds", [10, 16].includes(options.football_rounds || 10) ? String(options.football_rounds || 10) : "custom");
  $("football-custom-rounds").value = options.football_rounds || 10;
  setRadio("football-tiebreak", options.football_tiebreak || "penalties");
  setRadio("football-stoppage", options.football_stoppage ? "on" : "off");
  footballSettings();
  setRadio("kickoff", options.football_kickoff || "outer");
  setRadio("movement", options.football_beginner ? "beginner" : "strict");
  setRadio("own-goals", options.football_own_goals ? "on" : "off");
  setRadio("cricket-scoring", options.cricket_scoring || "standard");
  setRadio("atc-ring", options.atc_ring || "any");
  setRadio("atc-bull", options.atc_bull ? "on" : "off");
  configureMode(options.mode || "x01");
  renderInput();
}

// Play's vision engine comes from the integrations; any engine that scores speaks the
// Autodarts-compatible board API, so the game reads it as board input.
const engineChoice = () => radio("engine") || "manual";
function renderInput() {
  $("local-detection").hidden = engineChoice() === "manual";
}

/* Detection */
function renderDetector(value) {
  const detector = value.detector;
  const manual = value.options.input === "manual" && value.active;
  const label = manual ? tr("Manual input") : tr(DETECTOR[detector.state] || "Waiting");
  $("local-detector").textContent = label;
  $("local-detector").className = `badge ${manual ? "badge-green" : TONE[detector.state] || ""}`.trim();
  // The game's detector only reads the board during a game, so setup shows the engine's readiness.
  const ready = value.readiness;
  const setup = value.active || !ready ? [label, TONE[detector.state], ""]
    : [tr(READINESS[ready.state] || "Waiting"), READY_TONE[ready.state], tr(ready.reason)];
  $("local-setup-detector").textContent = setup[0];
  $("local-setup-detector").className = `badge ${setup[1] || ""}`.trim();
  $("local-setup-detector").title = setup[2];
  const board = $("local-board");
  board.dataset.halo = manual || !value.active ? "" : HALO[detector.state] || "";
  board.title = manual || !value.active ? "" : tr("Local detection: {0}", { 0: label });
  const detail = detector.error || value.error;
  $("local-detector-detail").textContent = detail ? tr(detail)
    : value.active && !manual ? tr("Clear the board. Detection connects automatically.") : "";
  $("local-detector-detail").hidden = !$("local-detector-detail").textContent;
}

/* Game screen */
function dartSlot(dart, index, latest, next = false, hint = null, football = false, bullup = false, removable = false, caption = tr("Suggested")) {
  const slot = document.createElement("li");
  slot.className = "game-dart";
  if (!dart) {
    slot.dataset.empty = "true";
    slot.textContent = "–";
    slot.setAttribute("aria-label", tr("Dart {0}: not thrown", { 0: index + 1 }));
    if (next) {
      slot.dataset.next = "true";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "game-dart-entry";
      button.setAttribute("aria-label", tr("Enter dart {0}", { 0: index + 1 }));
      button.setAttribute("aria-haspopup", "dialog");
      button.setAttribute("aria-controls", "local-manual");
      const number = document.createElement("span");
      number.textContent = hint ? tr(hint) : "–";
      button.append(number);
      if (hint) {
        const label = document.createElement("small");
        label.textContent = caption;
        button.append(label);
      }
      slot.replaceChildren(button);
    }
    if (hint) {
      slot.dataset.hint = "true";
      slot.setAttribute("aria-label", tr("Dart {0}: suggested {1}", { 0: index + 1, 1: tr(hint) }));
      if (next) slot.firstElementChild.setAttribute("aria-label",
        tr("Enter dart {0}, suggested {1}", { 0: index + 1, 1: tr(hint) }));
      else {
        const label = document.createElement("span"); label.textContent = tr(hint);
        const small = document.createElement("small"); small.textContent = caption;
        slot.replaceChildren(label, small);
      }
    }
    return slot;
  }
  const name = bullup && dart.coords ? (Math.hypot(dart.coords.x, dart.coords.y) * 170).toFixed(1) : football ? (dartRegion(dart) ? regionLabel(dartRegion(dart)) : tr("Miss")) : dart.segment.name === "MISS" ? tr("Miss")
    : dart.segment.number === 25 ? (dart.segment.multiplier === 2 ? tr("Bull") : "25") : dart.segment.name;
  const points = document.createElement("small");
  points.textContent = bullup ? "mm" : football ? "" : String(dart.segment.number * dart.segment.multiplier);
  const label = document.createElement("span");
  label.textContent = name;
  slot.append(label, points);
  slot.dataset.latest = String(latest);
  if (removable) {
    // Manual darts are taken back by tapping the last one; there is no separate undo button.
    const button = document.createElement("button");
    button.type = "button";
    button.className = "game-dart-remove";
    button.title = tr("Tap to take this dart back");
    button.setAttribute("aria-label", tr("Take back dart {0}: {1}", { 0: index + 1, 1: name }));
    button.append(label, points);
    slot.replaceChildren(button);
  }
  return slot;
}

function playerCard(player, index, frame, stats, won, setup, value) {
  const mode = value.options.mode, targets = value.targets;
  const card = document.createElement("article");
  card.className = "game-player";
  // Rows without the board list players in turn order; the sides seat them clockwise.
  card.style.setProperty("--seat", String(index));
  card.dataset.active = String(!won && index === frame.player);
  card.dataset.winner = String(frame.winner === index);
  const name = document.createElement("p");
  name.className = "game-player-name";
  name.textContent = player.name;
  if (isBot(setup)) {
    const tag = document.createElement("span");
    tag.className = "game-tag";
    tag.textContent = setup.difficulty ? tr("Bot · {0}", { 0: tr(AUTO[setup.difficulty]) })
      : setup.auto ? tr("Bot · Auto · {0}", { 0: tr(AUTO[setup.auto]) })
      : tr("Bot · {0}+", { 0: 10 + 10 * setup.bot });
    name.append(" ", tag);
  }
  const score = document.createElement("strong");
  score.className = "game-player-score";
  score.textContent = frame.gameScores[index];
  const meta = document.createElement("p");
  meta.className = "game-player-meta";
  const { average, last, busted, marks } = stats[index];
  const bullup = value.football?.phase === "bullup" ? value.football.bullup : null;
  if (bullup) {
    // The bull-up shows each distance; the closer player is marked once both have thrown.
    const distance = bullup[index];
    score.textContent = distance === null ? "–" : distance.toFixed(1);
    const decided = bullup.every(item => item !== null) && Math.abs(bullup[0] - bullup[1]) > 0.01;
    card.dataset.winner = String(decided && distance === Math.min(...bullup));
    if (decided) card.dataset.active = "false";
    meta.textContent = distance === null ? tr("Throw at the bull") : tr("mm from the bull");
    card.append(name, score, meta);
    return card;
  }
  const parts = [mode === "cricket" ? tr("MPR {0}", { 0: marks === null ? "–" : marks.toFixed(1) })
    : tr("Avg {0}", { 0: average === null ? "–" : average.toFixed(1) })];
  if (mode === "atc") {
    // The next target is the big number; a finished player shows a tick.
    const next = targets.sequence[targets.progress[index]];
    score.textContent = next === undefined ? "✓" : tr(targetLabel(next, value.options.atc_ring));
    parts.splice(0, 1, tr("{0} of {1} hit", { 0: targets.progress[index], 1: targets.sequence.length }));
  }
  // Bob's 27 gains and loses doubles, so an average says nothing.
  if (mode === "bobs27") parts.splice(0, 1);
  if (targets?.out[index]) {
    card.dataset.out = "true";
    parts.push(tr("Out"));
  } else if (busted) parts.push(tr("Last: bust"));
  else if (last !== undefined) parts.push(tr("Last: {0}", { 0: mode === "bobs27" && last > 0 ? `+${last}` : last }));
  meta.textContent = parts.join(" · ");
  card.append(name, score);
  if (mode === "cricket") card.append(cricketRow(targets.marks[index]));
  card.append(meta);
  return card;
}

/* Cricket marks: a slash, a cross, then a circled cross once a number is closed. */
function cricketRow(marks) {
  const row = document.createElement("ol");
  row.className = "game-marks";
  CRICKET.forEach((number, position) => {
    const item = document.createElement("li");
    const count = Math.min(marks[position], 3);
    item.dataset.closed = String(count === 3);
    item.setAttribute("aria-label", tr("{0}: {1} of 3 marks", { 0: number === 25 ? tr("Bull") : number, 1: count }));
    const label = document.createElement("span");
    label.textContent = number === 25 ? "B" : String(number);
    const mark = document.createElement("strong");
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = ["", "/", "X", "Ⓧ"][count];
    item.append(label, mark);
    row.append(item);
  });
  return row;
}

/* Marks the visit's darts like OcheCore's live board: each dart's bed is tinted, and a dart
   with coordinates is a small dot in its own colour with its number above. The newest one pulses. */
function renderBoard(turn, board = $("local-board-svg")) {
  for (const path of board.querySelectorAll("[data-state]")) delete path.dataset.state;
  const marks = [];
  turn.throws.forEach((dart, index) => {
    const state = index === turn.throws.length - 1 ? "latest" : "hit";
    const region = hitRegion(dart.segment, dart.coords);
    const key = hitKey(dart.segment);
    const beds = region ? board.querySelectorAll(`[data-region="${region}"]`)
      : key ? board.querySelectorAll(`[data-hit="${key}"]`) : [];
    // Without a position, the blinking bed is the only sign of the newest dart.
    for (const path of beds) path.dataset.state = dart.coords ? "hit" : state;
    if (!dart.coords) return;
    const [x, y] = boardPoint(dart.coords);
    const mark = document.createElementNS(SVG, "g");
    mark.setAttribute("class", "game-dart-mark");
    mark.dataset.state = state;
    mark.dataset.dart = String(index % 3 + 1);
    mark.setAttribute("transform", `translate(${x} ${y})`);
    const pulse = document.createElementNS(SVG, "circle");
    pulse.setAttribute("class", "game-dart-pulse");
    pulse.setAttribute("r", "2.2");
    // About the size of a dart's barrel, so it marks a spot within a ring.
    const dot = document.createElementNS(SVG, "circle");
    dot.setAttribute("class", "game-dart-dot");
    dot.setAttribute("r", "2.2");
    const label = document.createElementNS(SVG, "text");
    label.setAttribute("y", "-5.6");
    label.textContent = String(index + 1);
    mark.append(pulse, dot, label);
    marks.push(mark);
  });
  board.querySelector(".board-darts").replaceChildren(...marks);
}

/* Players sit on both sides of the board. In X01 the current visit is a line above the board,
   beside End game, as on OcheCore's live board. In football it stays below, and a single player keeps it under
   their card so the board can use the full height. */
let boardRings = null;
function placeStage(count, board, football) {
  const rings = football ? "oche" : "wdf";
  if (rings !== boardRings) {
    boardRings = rings;
    $("local-board-svg").innerHTML = boardMarkup(football ? undefined : WDF_RINGS);
    revision = "";
  }
  const stage = $("local-stage");
  stage.dataset.board = String(board);
  stage.dataset.count = String(count);
  $("local-board").hidden = !board;
  // The scoreboard sits beside End game; without the reference board the visit joins it, so the pitch keeps its size.
  stage.hidden = football && !board;
  const solo = board && count === 1;
  $("local-match").dataset.solo = String(solo);
  const turn = $("local-turn"), guidance = $("football-guidance");
  const scoreboard = document.querySelector(".football-scoreboard");
  scoreboard.hidden = !football;
  if (football && scoreboard.parentElement !== $("local-bar-visit")) $("local-bar-visit").prepend(scoreboard);
  if (!football || !board) {
    if (turn.parentElement !== $("local-bar-visit")) $("local-bar-visit").append(turn);
    // Without the board, the guidance is one line beside who is throwing.
    if (football && guidance.parentElement !== $("local-turn-head")) $("local-turn-head").append(guidance);
    return;
  }
  if (guidance.parentElement !== $("local-center")) $("local-center").prepend(guidance);
  const home = solo ? $("local-side-left") : $("local-center");
  if (turn.parentElement !== home || turn.previousElementSibling?.id === "football-guidance") home.append(turn);
}

let dartsRevision = "";
let entryVisit = null;
function manualEntry(value) {
  if (TV) return null;
  if (value?.football?.pending) return JSON.stringify(value.football.pending);
  if (["bullup", "finished"].includes(value?.football?.phase)) return null;
  if (!value?.active || value.options.input !== "manual" || value.finished || value.frame.winner >= 0
    || isBot(value.options.players[value.frame.player])) return null;
  const turn = value.frame.turns[0];
  if (turn.finishedAt || turn.throws.length >= 3) return null;
  return JSON.stringify([value.frame.id, turn.id, turn.playerId, turn.round, turn.throws]);
}

/* The game's details beside its title, as short badges; an empty one is hidden. */
function renderChips(value) {
  const { options, frame, targets } = value, mode = options.mode;
  const chips = {
    score: mode === "x01" ? String(options.starting_score)
      : mode === "football" ? (options.football_format === "rounds" ? tr("{0} rounds", { 0: options.football_rounds }) : tr("First to {0} goals", { 0: options.goals_to_win }))
      : mode === "cricket" ? tr(options.cricket_scoring === "cutthroat" ? "Cut-throat" : "Standard")
      : mode === "atc" ? tr({ any: "Any", double: "Doubles", triple: "Trebles" }[options.atc_ring])
      : tr("{0} rounds", { 0: targets.rounds }),
    out: mode === "x01" ? tr(options.double_out ? "Double out" : "Straight out")
      : mode === "football" ? tr(options.football_beginner ? "Beginner" : "Strict")
      : mode === "atc" && options.atc_bull ? tr("Bull finish") : "",
    round: mode === "football" && options.football_format === "rounds" ? value.football.clock
      : ROUND_MODES.includes(mode) ? tr("Round {0} of {1}", { 0: Math.min(frame.round + 1, targets.rounds), 1: targets.rounds })
      : tr("Round {0}", { 0: frame.round + 1 }),
  };
  for (const [id, text] of Object.entries(chips)) {
    $("local-chip-" + id).textContent = text;
    $("local-chip-" + id).parentElement.hidden = !text;
  }
}

/* Who won, or how a game without a winner ended. Solo practice games report the result. */
function resultText(value) {
  const { frame, options, targets } = value, mode = options.mode;
  const solo = frame.players.length === 1 && targets;
  const name = index => frame.players[index].name;
  if (solo) {
    const darts = frame.turns.reduce((total, turn) => total + turn.throws.length, 0);
    if (targets.out[0]) return tr("{0} is out", { 0: name(0) });
    return ["atc", "cricket"].includes(mode) ? tr("{0} finished in {1} darts", { 0: name(0), 1: darts })
      : tr("{0} scored {1}", { 0: name(0), 1: frame.gameScores[0] });
  }
  if (frame.winner >= 0) return tr("{0} wins!", { 0: name(frame.winner) });
  return targets?.out.every(Boolean) ? tr("Everyone is out") : tr("Draw");
}

/* Who throws the bull-up dart now, how far it landed, and who starts. */
function bullupText(value) {
  const { frame, football } = value, turn = frame.turns[0];
  const name = index => frame.players[index].name;
  const [first, second] = football.bullup;
  if (first !== null && second !== null) {
    return Math.abs(first - second) <= 0.01 ? tr("Tie — both throw again")
      : tr("{0} is closer and starts", { 0: name(first < second ? 0 : 1) });
  }
  const distance = football.bullup[frame.player];
  if (turn.finishedAt && distance !== null) return tr("{0} is {1} mm from the bull", { 0: name(frame.player), 1: distance.toFixed(1) });
  return football.event === "bullup_tie" ? tr("Bull-up tied — throw again")
    : tr("{0}: throw one dart at the bull", { 0: name(frame.player) });
}

/* Outlines the beds the current player aims for, like football's legal targets. */
function markTargets(value) {
  if (!value.targets || value.finished) return;
  const beds = targetBeds(value.options.mode, value.targets, value.frame.player, value.options.atc_ring);
  for (const key of beds) {
    for (const node of $("local-board-svg").querySelectorAll(`[data-hit="${key}"]`)) node.dataset.target = "true";
  }
}

function renderMatch(value) {
  const frame = value.frame;
  const mode = value.options.mode;
  const football = mode === "football";
  // Football's bull-up uses the X01 screen and board; the pitch opens once a player starts.
  const bullup = football && value.football.phase === "bullup";
  $("local-match").dataset.variant = bullup ? "bullup" : mode;
  $("football-guidance").hidden = !football || bullup;
  $("football-board-toggle").hidden = $("football-help").hidden = !football || bullup || REMOTE;
  $("local-match-title").textContent = modeInfo(mode)[0];
  $("local-single-bed").hidden = !football || !footballKeypad || radio("ring") !== "1";
  $("football-input-board").hidden = !football || footballKeypad;
  $("local-rings").hidden = $("local-keys").hidden = football && !footballKeypad;
  $("football-input-toggle").hidden = !football;
  $("football-input-toggle").textContent = tr(footballKeypad ? "Use round board" : "Use keypad");
  $("local-history-score-label").textContent = football ? tr("Goals") : mode === "x01" ? tr("Remaining")
    : mode === "cricket" ? tr("Marks") : mode === "atc" ? tr("Hits") : tr("Total");
  const turn = frame.turns[0];
  const manual = value.options.input === "manual";
  const won = value.finished;
  if (value.tournament) $("local-tournament-return").href = `/local/tournament?id=${encodeURIComponent(value.tournament.id)}`;
  $("local-tournament-return").hidden = $("local-tournament-continue").hidden = !value.tournament;
  $("local-rematch").hidden = $("local-new").hidden = Boolean(value.tournament);
  const player = frame.players[frame.player];
  const bot = !won && isBot(value.options.players[frame.player]);
  renderChips(value);
  $("local-winner").hidden = !won;
  // The remote leaves the board to the TV, except for tapping the bull-up dart.
  const boardShown = bullup || (!REMOTE && (football ? footballBoard : value.options.show_board));
  placeStage(frame.players.length, boardShown, football && !bullup);
  $("local-football").parentElement.dataset.reference = String(!football || footballBoard);
  $("football-board-toggle").textContent = tr(footballBoard ? "Hide dartboard" : "Show dartboard");
  $("football-board-toggle").setAttribute("aria-pressed", String(footballBoard));
  $("local-turn").hidden = $("local-turn-head").hidden = won;
  const entry = manualEntry(value);
  if ($("local-manual").open && entry !== entryVisit) $("local-manual").close();
  // The remote keeps dart entry open below the visit instead of in a popup.
  if (REMOTE && entry !== null && !$("local-manual").open && !working) openEntry(tr("Enter dart {0}", { 0: turn.throws.length + 1 }), false);
  // With the board shown, its halo replaces the detection badge.
  $("local-detector").parentElement.hidden = boardShown && !manual;
  $("local-next").title = manual || bot ? ""
    : tr("Detected visits advance when you remove the darts. Use Next turn after clearing the board to finish an interrupted visit.");
  if (won) $("local-winner-name").textContent = resultText(value);
  // A manual bull-up dart is placed by tapping the board where it landed.
  $("local-board").dataset.pick = String(bullup && manual && !bot && !turn.finishedAt && !working);
  $("local-phase").textContent = bullup && (turn.finishedAt || !bot) ? bullupText(value)
    : bot ? tr(turn.busted ? "{0} · Bust" : "{0} is throwing…", { 0: player.name })
    : turn.busted ? tr("{0} · Bust — remove darts", { 0: player.name })
    : tr("{0} to throw", { 0: player.name });
  $("local-phase").dataset.bust = String(Boolean(turn.busted));
  // X01 suggests a checkout; target games show what to aim for next.
  const route = football || turn.finishedAt || won ? null
    : value.targets ? targetHints(mode, value.targets, frame.player, turn.throws, value.options.atc_ring)
    : checkout(frame.gameScores[frame.player], 3 - turn.throws.length, value.options.double_out);
  const removable = !TV && manual && !bot && !(won && Boolean(value.tournament));
  const dartsSignature = JSON.stringify([turn.throws, entry, route, football, removable]);
  if (dartsSignature !== dartsRevision) {
    dartsRevision = dartsSignature;
    $("local-darts").replaceChildren(...(value.football && ["bullup", "penalties"].includes(value.football.phase) ? [0] : [0, 1, 2]).map(index => dartSlot(turn.throws[index], index,
      index === turn.throws.length - 1, entry !== null && index === turn.throws.length,
      route?.[index - turn.throws.length], football, value.football?.phase === "bullup",
      removable && index === turn.throws.length - 1, value.targets ? tr("Target") : tr("Suggested"))));
  }
  for (const button of $("local-darts").querySelectorAll("button")) button.disabled = working;
  $("local-visit").textContent = turn.busted ? tr("Bust") : turn.points;
  $("local-next").disabled = working || won || bot || !turn.throws.length || Boolean(value.football?.pending);
  $("local-stop").disabled = $("local-rematch").disabled = $("local-new").disabled = working;
  const closed = working || bot || ((won || Boolean(turn.finishedAt)) && !value.football?.pending);
  const triple = radio("ring") === "3";
  for (const key of $("local-keys").querySelectorAll("button")) {
    if (key.dataset.number === "25") key.textContent = football ? tr(radio("ring") === "1" ? "Outer bull" : "Bullseye") : tr("Bull");
    key.disabled = closed || (triple && key.dataset.number === "25");
  }
  const signature = JSON.stringify([frame, value.football]);
  if (signature === revision) return;
  revision = signature;
  // Only redraw on changes so the blinking animation is not restarted by every poll.
  renderBoard(turn);
  renderFootball(value);
  if (REMOTE) $("local-football").hidden = true;
  markTargets(value);
  const stats = playerStats(frame);
  const cards = frame.players.map((item, index) =>
    playerCard(item, index, frame, stats, won, value.options.players[index], value));
  const { left, right } = seats(cards.length);
  // Keep the visit line when it sits under a single player's card.
  $("local-side-left").replaceChildren(...left.map(index => cards[index]), ...$("local-side-left").querySelectorAll("#local-turn"));
  $("local-side-right").replaceChildren(...right.map(index => cards[index]));
  const visits = frame.turns.filter(item => item.throws.length);
  $("local-history-empty").hidden = visits.length > 0;
  $("local-history").replaceChildren(...visits.map(item => {
    const row = document.createElement("tr");
    const owner = frame.players.find(candidate => candidate.id === item.playerId);
    for (const text of [item.round + 1, owner?.name || "", item.throws.map(dart => football && dartRegion(dart) ? regionLabel(dartRegion(dart)) : dart.segment.name).join(" · "),
      item.busted ? tr("Bust") : item.points, mode === "cricket" ? cricketMarks(item) : item.score]) {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.append(cell);
    }
    return row;
  }));
}

/* Sizes the board so the game screen fits the window without scrolling. Phones, and windows too
   short for a 300 px board, still scroll. */
let fitted = "";
function fitBoard(force = false) {
  const board = $("local-board");
  const root = document.documentElement;
  const wide = matchMedia("(min-width: 901px)").matches;
  const key = [innerWidth, innerHeight, $("main").clientWidth, $("local-match").hidden, board.hidden,
    $("local-winner").hidden, $("local-detector-detail").hidden, $("local-stage").dataset.count, state?.football?.phase, state?.football?.pending, state?.football?.event, state?.frame?.turns[0]?.finishedAt].join();
  if (!force && key === fitted) return;
  fitted = key;
  if (state?.options.mode === "football" && state.football?.phase !== "bullup" && !$("local-match").hidden) {
    const arena = $("local-football").parentElement;
    const available = innerHeight - arena.getBoundingClientRect().top - scrollY - parseFloat(getComputedStyle($("main")).paddingBottom)
      - $("local-foot").offsetHeight - 4;
    const compact = innerWidth <= 700;
    const extra = $("local-football").offsetHeight - $("local-football").querySelector(".football-field").offsetHeight;
    const controls = compact ? $("local-stage").offsetHeight + 8 : 0;
    arena.style.setProperty("--football-field-height", `${Math.max(90, available - extra - controls)}px`);
    arena.style.setProperty("--football-board-size", `${Math.max(100, Math.min(250, available - 155))}px`);
    board.style.removeProperty("--board-fit");
    return;
  }
  if ($("local-match").hidden || board.hidden || !wide) {
    board.style.removeProperty("--board-fit");
    return;
  }
  board.style.setProperty("--board-fit", "10000px");
  const overflow = root.scrollHeight - root.clientHeight;
  const size = board.getBoundingClientRect().width;
  board.style.setProperty("--board-fit", `${Math.max(300, Math.floor(size - Math.max(0, overflow)))}px`);
}

function render(value) {
  state = value;
  prefill(value);
  renderDetector(value);
  if (value.active) {
    choosing = false;
    show("match");
    renderMatch(value);
    fitBoard();
  } else {
    revision = "";
    if ($("local-manual").open) $("local-manual").close();
    show(TV ? "practice" : choosing ? "setup" : "modes");
  }
  $("local-start").disabled = working;
}

/* The TV view between games: the darts on the board, like OcheCore's live view. */
let practiceRevision = "";
function renderPractice(live) {
  const ready = live.readiness;
  $("local-practice-state").textContent = tr(READINESS[ready?.state] || "Waiting");
  $("local-practice-state").className = `badge ${READY_TONE[ready?.state] || ""}`.trim();
  $("local-practice-state").title = ready ? tr(ready.reason) : "";
  const darts = (live.board?.darts || []).map((dart, index) => ({ id: String(index), ...dart }));
  const signature = JSON.stringify(darts);
  if (signature === practiceRevision) return;
  practiceRevision = signature;
  renderBoard({ throws: darts }, $("local-practice-svg"));
  $("local-practice-darts").replaceChildren(...[0, 1, 2].map(index => dartSlot(darts[index], index, index === darts.length - 1)));
  $("local-practice-total").textContent = darts.reduce((total, dart) => total + dart.segment.number * dart.segment.multiplier, 0);
}

/* Actions */
function fail(cause) {
  $("local-error").textContent = tr(cause.message);
  $("local-error").hidden = false;
  if ($("local-manual").open) {
    $("local-manual-error").textContent = tr(cause.message);
    $("local-manual-error").hidden = false;
  }
}

async function action(run) {
  if (working) return;
  working = true;
  generation++;
  if (state) render(state);
  $("local-error").hidden = true;
  try {
    state = await run();
  } catch (cause) {
    fail(cause);
  } finally {
    working = false;
    generation++;
    if (state) render(state);
  }
}
const post = (path, body = {}) => api(`/api/local/${path}`, "POST", body);

function setupOptions() {
  return {
    mode: selectedMode,
    goals_to_win: Number(radio("goals")),
    football_format: radio("football-format"),
    football_rounds: radio("football-rounds") === "custom" ? Number($("football-custom-rounds").value) : Number(radio("football-rounds")),
    football_tiebreak: radio("football-tiebreak"),
    football_stoppage: radio("football-stoppage") === "on",
    football_kickoff: radio("kickoff"),
    football_beginner: radio("movement") === "beginner",
    football_own_goals: radio("own-goals") === "on",
    cricket_scoring: radio("cricket-scoring"),
    atc_ring: radio("atc-ring"),
    atc_bull: radio("atc-bull") === "on",
    players: [...$("local-players").children].map((row, index) => {
      const bot = Number(row.dataset.bot) || null;
      const auto = row.dataset.autoBot || null;
      const difficulty = row.dataset.difficulty || null;
      const name = row.querySelector("input").value.trim()
        || (rowIsBot(row) ? tr("Bot {0}", { 0: index + 1 }) : tr("Player {0}", { 0: index + 1 }));
      return difficulty ? { name, difficulty } : bot ? { name, bot } : auto ? { name, auto } : { name };
    }),
    starting_score: Number(radio("score")),
    double_out: radio("out") === "double",
    input: engineChoice() === "manual" ? "manual" : "autodarts",
    bot_speed: radio("speed"),
    show_board: selectedMode === "football" || radio("board") === "show",
    random_names: randomNames(),
  };
}

document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => {
  configureMode(button.dataset.mode);
  choosing = true;
  show("setup");
  $("local-players").querySelector("input")?.select();
}));
function filterModes() {
  const category = radio("category") || "all";
  for (const card of $("game-modes").querySelectorAll(".game-mode")) card.hidden = category !== "all" && card.dataset.category !== category;
  try { localStorage.setItem("oche.play.category", category); } catch (_) { /* Keep it for this visit. */ }
}
try { setRadio("category", localStorage.getItem("oche.play.category") || "all"); } catch (_) { /* Show every game. */ }
filterModes();
document.querySelectorAll('input[name="category"]').forEach(input => input.addEventListener("change", filterModes));
$("local-back").addEventListener("click", () => { choosing = false; show("modes"); });
$("local-add-player").addEventListener("click", () => addPlayer()?.select());
$("local-add-bot").addEventListener("click", () => openBots());
$("local-bot-dialog").addEventListener("close", closeBots);
$("local-bot-auto-open").addEventListener("click", () => {
  showAuto(true);
  $("local-bot-auto").querySelector("button[value=auto-medium]").focus();
});
$("local-history-open").addEventListener("click", () => $("local-history-dialog").showModal());
let startingEngine = false;
function renderEngineStart(data) {
  const engine = data.engines.find(item => item.name === engineChoice());
  const button = $("local-engine-start");
  button.hidden = !engine || !engine.enabled || !engine.installed || engine.status === "running";
  button.disabled = startingEngine;
  button.textContent = startingEngine ? tr("Starting…") : tr("Start {name}", { name: engine?.label || "" });
}
$("local-engine-start").addEventListener("click", async () => {
  if (startingEngine || engineChoice() === "manual") return;
  startingEngine = true;
  $("local-engine-start").disabled = true;
  $("local-engine-start").textContent = tr("Starting…");
  const error = $("game-engine-error");
  error.hidden = true;
  try {
    renderEngineStart(await api(`/api/engines/${engineChoice()}/use`, "POST"));
    window.ocheSidebar?.refresh();
  } catch (cause) { error.textContent = cause.message; error.hidden = false; }
  finally { startingEngine = false; $("local-engine-start").disabled = false; await refresh(); }
});
let engineSaved = engineChoice();
document.querySelectorAll('input[name="engine"]').forEach(input => input.addEventListener("change", async () => {
  const error = $("game-engine-error");
  error.hidden = true;
  renderInput();
  try {
    // Choosing an engine starts it and stops the other one, which shares the cameras.
    await api(`/api/engines/${engineChoice()}/use`, "POST");
    engineSaved = engineChoice();
    window.ocheSidebar?.refresh();
    await refresh();
  } catch (cause) {
    setRadio("engine", engineSaved);
    renderInput();
    error.textContent = cause.message;
    error.hidden = false;
  }
}));
document.querySelectorAll('input[name="names"]').forEach(input => input.addEventListener("change", renameGenerated));
$("local-setup").addEventListener("submit", event => {
  event.preventDefault();
  if (selectedMode === "football" && $("local-players").children.length !== 2) {
    fail(new Error(tr("Football requires exactly two players."))); return;
  }
  action(() => post("start", setupOptions())).then(() => {
    if (state?.active) $("local-match").scrollIntoView({ block: "start" });
  });
});
function openEntry(title, modal = !REMOTE) {
  entryVisit = manualEntry(state);
  if (entryVisit === null && !state?.football?.pending) return;
  setRadio("ring", "1");
  $("local-manual-title").textContent = title;
  $("local-manual-error").hidden = true;
  if (modal) { renderMatch(state); $("local-manual").showModal(); } else $("local-manual").show();
}
$("local-darts").addEventListener("click", event => {
  if (!event.target.closest(".game-dart-entry") || working) return;
  openEntry(tr("Enter dart {0}", { 0: state.frame.turns[0].throws.length + 1 }));
});
$("local-keys").addEventListener("click", event => {
  const key = event.target.closest("button[data-number]");
  if (!key || key.disabled || !$("local-manual").open || manualEntry(state) !== entryVisit) return;
  const multiplier = key.dataset.number === "0" ? 1 : Number(radio("ring"));
  submitManualDart({ number: Number(key.dataset.number), multiplier,
    bed: state.options.mode === "football" && multiplier === 1 && Number(key.dataset.number) < 21 && Number(key.dataset.number) > 0 ? radio("single-bed") : null });
});
function submitManualDart(dart) {
  $("local-manual-error").hidden = true;
  action(async () => {
    const result = await post(state.football?.pending ? "football/confirm" : "throw", dart);
    $("local-manual").close();
    return result;
  }).then(() => {
    if (!$("local-manual").open) {
      const target = state.finished ? $(state.tournament ? "local-tournament-continue" : "local-rematch")
        : $("local-darts").querySelector(".game-dart-entry") || $("local-next");
      target.focus();
    }
  });
}
$("football-input-svg").innerHTML = boardMarkup();
$("football-input-svg").addEventListener("click", event => {
  const cell = event.target.closest("[data-region]")?.dataset.region;
  if (cell && !working && $("local-manual").open && manualEntry(state) === entryVisit) submitManualDart(cellDart(cell));
});
document.querySelectorAll('input[name="ring"]').forEach(input => input.addEventListener("change", () => {
  if (state?.active) renderMatch(state);
}));
$("local-darts").addEventListener("click", event => {
  if (event.target.closest(".game-dart-remove") && !working) action(() => post("undo"));
});
$("local-next").addEventListener("click", () => action(() => post("next")));
$("local-stop").addEventListener("click", () => {
  const message = state?.tournament && state.frame.winner < 0
    ? tr("Abandon this match and replay it from the start? Use Tournament to leave the match in progress.")
    : tr("End this game? The score is kept until you start another game.");
  if (confirm(message)) action(() => post("stop"));
});
$("local-new").addEventListener("click", () => action(async () => {
  const result = await post("stop");
  configureMode(state.options.mode || "x01");
  choosing = true;
  return result;
}));
$("local-rematch").addEventListener("click", () => action(async () => {
  const options = state.options;
  await post("stop");
  return post("start", options);
}));

async function refresh() {
  if (working || document.hidden) return;
  const currentGeneration = generation;
  try {
    const [value, engines] = await Promise.all([api("/api/local"), api("/api/engines")]);
    renderEngineStart(engines);
    if (!working && currentGeneration === generation) render(value);
    // Polling the board's darts keeps the read-only feed open for the practice board.
    if (TV && !value.active) renderPractice(await api("/api/local/live"));
  } catch (cause) {
    fail(cause);
  }
}
$("local-board-svg").innerHTML = boardMarkup();
if (TV) $("local-practice-svg").innerHTML = boardMarkup(WDF_RINGS);
setInterval(refresh, 1000);
document.addEventListener("visibilitychange", refresh);
addEventListener("resize", () => fitBoard(true));
// Also refit when the content width changes, such as when the sidebar collapses.
new ResizeObserver(() => fitBoard()).observe($("main"));
refresh();

function footballSettings() {
  const football = selectedMode === "football", rounds = radio("football-format") === "rounds";
  $("local-goals-option").hidden = !football || rounds;
  for (const id of ["rounds", "tiebreak", "stoppage"]) $("local-" + id + "-option").hidden = !football || !rounds;
  $("football-custom-rounds").parentElement.hidden = radio("football-rounds") !== "custom";
}
for (const name of ["football-format", "football-rounds"]) document.querySelectorAll(`input[name="${name}"]`).forEach(input => input.addEventListener("change", footballSettings));
$("local-board-svg").addEventListener("click", event => {
  if ($("local-board").dataset.pick !== "true" || working) return;
  // 100 board units are the double ring's outer edge, 170 mm from the centre; y points up.
  const point = new DOMPoint(event.clientX, event.clientY).matrixTransform($("local-board-svg").getScreenCTM().inverse());
  const hit = event.target.closest("[data-hit]")?.dataset.hit;
  const mm = units => Math.max(-300, Math.min(300, Number((units * 1.7).toFixed(1))));
  action(() => post("throw", { number: hit ? Number(hit.slice(1)) : 0, multiplier: hit ? "SDT".indexOf(hit[0]) + 1 : 1,
    x_mm: mm(point.x), y_mm: mm(-point.y) }));
});
$("football-confirm").addEventListener("click", () => openEntry(tr("Confirm hit near wire"), true));

$("football-input-toggle").addEventListener("click", () => { footballKeypad = !footballKeypad; renderMatch(state); });
$("football-input-miss").addEventListener("click", () => {
  if (!working && $("local-manual").open && manualEntry(state) === entryVisit) submitManualDart({ number: 0, multiplier: 1 });
});

$("football-board-toggle").addEventListener("click", () => { footballBoard = !footballBoard; renderMatch(state); fitBoard(true); });
