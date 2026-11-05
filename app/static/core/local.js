import { tr } from "../js/i18n.mjs";
import { $, api } from "./client.mjs";
import { WDF_RINGS, boardMarkup, boardPoint, checkout, funnyName, hitKey, hitRegion, playerStats, seats } from "./game.mjs";

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
const isBot = player => Boolean(player?.bot || player?.auto);
const TONE = { connected: "badge-green", waiting: "badge-yellow", waiting_for_clear: "badge-yellow",
  stopped: "badge-red", unavailable: "badge-red" };
// The board's halo shows detection: red when stopped, yellow while waiting, green when scoring.
const HALO = { connected: "green", waiting: "yellow", waiting_for_clear: "yellow", stopped: "red", unavailable: "red" };

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

function show(view) {
  $("game-page").dataset.view = view;
  $("game-modes").hidden = view !== "modes";
  $("local-setup").hidden = view !== "setup";
  $("local-match").hidden = view !== "match";
}

/* Players */
function addPlayer(name = newName(), bot = null, auto = null) {
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
  setBot(row, bot, auto);
  return input;
}

/* A bot has a fixed level or, as an auto bot, a difficulty relative to the players. */
function setBot(row, level, auto = null) {
  if (level) row.dataset.bot = String(level);
  else delete row.dataset.bot;
  if (auto) row.dataset.autoBot = auto;
  else delete row.dataset.autoBot;
  const chip = row.querySelector(".game-bot-level");
  chip.hidden = !level && !auto;
  if (auto) {
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
    const label = row.dataset.bot || row.dataset.autoBot ? tr("Bot {0}", { 0: index + 1 }) : tr("Player {0}", { 0: index + 1 });
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

function openBots(row = null) {
  editingBot = row;
  $("local-bot-dialog").returnValue = "";
  const current = row?.dataset.autoBot ? `auto-${row.dataset.autoBot}` : row?.dataset.bot;
  for (const button of $("local-bot-dialog").querySelectorAll("button[name=level]")) {
    button.setAttribute("aria-pressed", String(Boolean(row) && button.value === current));
  }
  showAuto(Boolean(row?.dataset.autoBot));
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
  const level = auto ? null : Number(value);
  if (!(auto in AUTO) && !(Number.isInteger(level) && level >= 1)) return;
  if (row?.isConnected) setBot(row, level, auto);
  else addPlayer(newName(true), level, auto);
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
  for (const row of rows) row.querySelector("input").value = newName(Boolean(row.dataset.bot || row.dataset.autoBot));
  renderRoster();
}

function configureMode(mode) {
  selectedMode = mode;
  const football = mode === "football";
  if (football) footballBoard = true;
  $("local-setup-title").textContent = football ? tr("Football") : "X01";
  $("local-setup-description").textContent = football ? tr("Two players · Three darts per turn · Saved on this device") : tr("One leg · Single in · Saved on this device");
  for (const id of ["score", "out", "board"]) $("local-" + id + "-option").hidden = football;
  for (const id of ["goals", "kickoff", "movement", "own-goals", "format", "rounds", "tiebreak", "stoppage"]) $("local-" + id + "-option").hidden = !football;
  footballSettings();
  $("local-football-rules").hidden = !football;
  $("local-football-rules").textContent = tr("Move the shared ball between neighbouring regions. Player 1 attacks D6; Player 2 attacks D11. After a goal, the conceding player kicks off with three fresh darts.");
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
  for (const id of ["local-detector", "local-setup-detector"]) {
    $(id).textContent = label;
    $(id).className = `badge ${manual ? "badge-green" : TONE[detector.state] || ""}`.trim();
  }
  const board = $("local-board");
  board.dataset.halo = manual || !value.active ? "" : HALO[detector.state] || "";
  board.title = manual || !value.active ? "" : tr("Local detection: {0}", { 0: label });
  const detail = detector.error || value.error;
  $("local-detector-detail").textContent = detail ? tr(detail)
    : value.active && !manual ? tr("Clear the board. Detection connects automatically.") : "";
  $("local-detector-detail").hidden = !$("local-detector-detail").textContent;
}

/* Game screen */
function dartSlot(dart, index, latest, next = false, hint = null, football = false, bullup = false, removable = false) {
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
        label.textContent = tr("Suggested");
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
        const caption = document.createElement("small"); caption.textContent = tr("Suggested");
        slot.replaceChildren(label, caption);
      }
    }
    return slot;
  }
  const name = bullup && dart.coords ? `${(Math.hypot(dart.coords.x, dart.coords.y) * 170).toFixed(1)} mm` : football ? (dartRegion(dart) ? regionLabel(dartRegion(dart)) : tr("Miss")) : dart.segment.name === "MISS" ? tr("Miss")
    : dart.segment.number === 25 ? (dart.segment.multiplier === 2 ? tr("Bull") : "25") : dart.segment.name;
  const points = document.createElement("small");
  points.textContent = football ? "" : String(dart.segment.number * dart.segment.multiplier);
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

function playerCard(player, index, frame, stats, won, setup) {
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
    tag.textContent = setup.auto ? tr("Bot · Auto · {0}", { 0: tr(AUTO[setup.auto]) })
      : tr("Bot · {0}+", { 0: 10 + 10 * setup.bot });
    name.append(" ", tag);
  }
  const score = document.createElement("strong");
  score.className = "game-player-score";
  score.textContent = frame.gameScores[index];
  const meta = document.createElement("p");
  meta.className = "game-player-meta";
  const { average, last, busted } = stats[index];
  const parts = [tr("Avg {0}", { 0: average === null ? "–" : average.toFixed(1) })];
  if (busted) parts.push(tr("Last: bust"));
  else if (last !== undefined) parts.push(tr("Last: {0}", { 0: last }));
  meta.textContent = parts.join(" · ");
  card.append(name, score, meta);
  return card;
}

/* Marks the visit's darts like OcheCore's live board: each dart's bed is tinted, and a dart
   with coordinates is a small dot in its own colour with its number above. The newest one pulses. */
function renderBoard(turn) {
  const board = $("local-board-svg");
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
  const solo = board && count === 1;
  $("local-match").dataset.solo = String(solo);
  const turn = $("local-turn");
  if (!football) {
    if (turn.parentElement !== $("local-bar-visit")) $("local-bar-visit").append(turn);
    return;
  }
  const home = solo ? $("local-side-left") : $("local-center");
  if (turn.parentElement !== home || turn.previousElementSibling?.id === "football-guidance") home.append(turn);
}

let dartsRevision = "";
let entryVisit = null;
function manualEntry(value) {
  if (value?.football?.pending) return JSON.stringify(value.football.pending);
  if (["bullup", "finished"].includes(value?.football?.phase)) return null;
  if (!value?.active || value.options.input !== "manual" || value.frame.winner >= 0
    || isBot(value.options.players[value.frame.player])) return null;
  const turn = value.frame.turns[0];
  if (turn.finishedAt || turn.throws.length >= 3) return null;
  return JSON.stringify([value.frame.id, turn.id, turn.playerId, turn.round, turn.throws]);
}

function renderMatch(value) {
  const frame = value.frame;
  const football = value.options.mode === "football";
  $("local-match").dataset.variant = football ? "football" : "x01";
  $("football-guidance").hidden = !football;
  $("football-panel-actions").hidden = !football;
  $("local-match-title").textContent = football ? tr("Football") : "X01";
  $("local-single-bed").hidden = !football || !footballKeypad || radio("ring") !== "1";
  $("football-input-board").hidden = !football || footballKeypad;
  $("local-rings").hidden = $("local-keys").hidden = football && !footballKeypad;
  $("football-input-toggle").hidden = !football;
  $("football-input-toggle").textContent = tr(footballKeypad ? "Use round board" : "Use keypad");
  $("local-history-score-label").textContent = football ? tr("Goals") : tr("Remaining");
  const turn = frame.turns[0];
  const manual = value.options.input === "manual";
  const won = frame.winner >= 0 || value.football?.phase === "finished";
  if (value.tournament) $("local-tournament-return").href = `/local/tournament?id=${encodeURIComponent(value.tournament.id)}`;
  $("local-tournament-return").hidden = $("local-tournament-continue").hidden = !value.tournament;
  $("local-rematch").hidden = $("local-new").hidden = Boolean(value.tournament);
  const player = frame.players[frame.player];
  const bot = !won && isBot(value.options.players[frame.player]);
  $("local-chip-score").textContent = football ? (value.options.football_format === "rounds" ? tr("{0} rounds", { 0: value.options.football_rounds }) : tr("First to {0} goals", { 0: value.options.goals_to_win })) : String(value.options.starting_score);
  $("local-chip-out").textContent = football ? tr(value.options.football_beginner ? "Beginner" : "Strict") : tr(value.options.double_out ? "Double out" : "Straight out");
  $("local-chip-round").textContent = football && value.options.football_format === "rounds" ? value.football.clock : tr("Round {0}", { 0: frame.round + 1 });
  $("local-winner").hidden = !won;
  placeStage(frame.players.length, football ? footballBoard : value.options.show_board, football);
  $("local-football").parentElement.dataset.reference = String(footballBoard);
  $("football-board-toggle").textContent = tr(footballBoard ? "Hide dartboard" : "Show dartboard");
  $("football-board-toggle").setAttribute("aria-pressed", String(footballBoard));
  $("local-turn").hidden = $("local-turn-head").hidden = won;
  const entry = manualEntry(value);
  if ($("local-manual").open && entry !== entryVisit) $("local-manual").close();
  // With the board shown, its halo replaces the detection badge.
  $("local-detector").parentElement.hidden = (football ? footballBoard : value.options.show_board) && !manual;
  $("local-next").title = manual || bot ? ""
    : tr("Detected visits advance when you remove the darts. Use Next turn after clearing the board to finish an interrupted visit.");
  if (won) $("local-winner-name").textContent = frame.winner < 0 ? tr("Draw") : tr("{0} wins!", { 0: frame.players[frame.winner].name });
  $("local-phase").textContent = bot ? tr(turn.busted ? "{0} · Bust" : "{0} is throwing…", { 0: player.name })
    : turn.busted ? tr("{0} · Bust — remove darts", { 0: player.name })
    : tr("{0} to throw", { 0: player.name });
  $("local-phase").dataset.bust = String(Boolean(turn.busted));
  const route = football || turn.finishedAt || won ? null
    : checkout(frame.gameScores[frame.player], 3 - turn.throws.length, value.options.double_out);
  const removable = manual && !bot && !(won && Boolean(value.tournament));
  const dartsSignature = JSON.stringify([turn.throws, entry, route, football, removable]);
  if (dartsSignature !== dartsRevision) {
    dartsRevision = dartsSignature;
    $("local-darts").replaceChildren(...(value.football && ["bullup", "penalties"].includes(value.football.phase) ? [0] : [0, 1, 2]).map(index => dartSlot(turn.throws[index], index,
      index === turn.throws.length - 1, entry !== null && index === turn.throws.length,
      route?.[index - turn.throws.length], football, value.football?.phase === "bullup",
      removable && index === turn.throws.length - 1)));
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
  const stats = playerStats(frame);
  const cards = frame.players.map((item, index) =>
    playerCard(item, index, frame, stats, won, value.options.players[index]));
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
      item.busted ? tr("Bust") : item.points, item.score]) {
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
  if (state?.options.mode === "football" && !$("local-match").hidden) {
    const arena = $("local-football").parentElement;
    const available = innerHeight - arena.getBoundingClientRect().top - scrollY - parseFloat(getComputedStyle($("main")).paddingBottom) - 4;
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
    show(choosing ? "setup" : "modes");
  }
  $("local-start").disabled = working;
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
    players: [...$("local-players").children].map((row, index) => {
      const bot = Number(row.dataset.bot) || null;
      const auto = row.dataset.autoBot || null;
      const name = row.querySelector("input").value.trim()
        || (bot || auto ? tr("Bot {0}", { 0: index + 1 }) : tr("Player {0}", { 0: index + 1 }));
      return bot ? { name, bot } : auto ? { name, auto } : { name };
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
$("local-darts").addEventListener("click", event => {
  if (!event.target.closest(".game-dart-entry") || working) return;
  entryVisit = manualEntry(state);
  if (entryVisit === null) return;
  setRadio("ring", "1");
  $("local-manual-title").textContent = tr("Enter dart {0}", { 0: state.frame.turns[0].throws.length + 1 });
  $("local-manual-error").hidden = true;
  renderMatch(state);
  $("local-manual").showModal();
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
      const target = state.frame.winner >= 0 ? $(state.tournament ? "local-tournament-continue" : "local-rematch")
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
  } catch (cause) {
    fail(cause);
  }
}
$("local-board-svg").innerHTML = boardMarkup();
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
$("football-bullup-submit").addEventListener("click", () => {
  const input = $("football-distance");
  if (!input.value || !input.reportValidity() || working) return;
  action(() => post("throw", { number: 25, multiplier: 1, distance_mm: Number(input.value) })).then(() => { input.value = ""; });
});
$("football-confirm").addEventListener("click", () => {
  entryVisit = manualEntry(state);
  setRadio("ring", "1");
  $("local-manual-title").textContent = tr("Confirm hit near wire");
  $("local-manual-error").hidden = true;
  renderMatch(state);
  $("local-manual").showModal();
});

$("football-input-toggle").addEventListener("click", () => { footballKeypad = !footballKeypad; renderMatch(state); });
$("football-input-miss").addEventListener("click", () => {
  if (!working && $("local-manual").open && manualEntry(state) === entryVisit) submitManualDart({ number: 0, multiplier: 1 });
});

$("football-board-toggle").addEventListener("click", () => { footballBoard = !footballBoard; renderMatch(state); fitBoard(true); });
