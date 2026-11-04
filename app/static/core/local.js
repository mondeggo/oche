import { tr } from "../js/i18n.mjs";
import { $, api } from "./client.mjs";
import { boardMarkup, boardPoint, checkout, funnyName, hitKey, playerStats, seats } from "./game.mjs";

const MAX_PLAYERS = 8;
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
  if (rows.children.length >= MAX_PLAYERS) return null;
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
  $("local-add-player").disabled = $("local-add-bot").disabled = rows.length >= MAX_PLAYERS;
  $("local-player-count").textContent = tr("{0} of {1}", { 0: rows.length, 1: MAX_PLAYERS });
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

const randomNames = () => radio("names") !== "numbered";

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

function prefill(value) {
  if (prefilled) return;
  prefilled = true;
  const options = value.options;
  // A new game starts with one player under a playful name; Rematch keeps the previous roster.
  setRadio("names", options.random_names === false ? "numbered" : "random");
  $("local-players").replaceChildren();
  addPlayer();
  setRadio("score", options.starting_score);
  setRadio("out", options.double_out ? "double" : "straight");
  setRadio("speed", options.bot_speed);
  setRadio("board", options.show_board ? "show" : "hide");
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
    $(id).className = `badge ${manual ? "" : TONE[detector.state] || ""}`.trim();
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
function dartSlot(dart, index, latest) {
  const slot = document.createElement("li");
  slot.className = "game-dart";
  if (!dart) {
    slot.dataset.empty = "true";
    slot.textContent = String(index + 1);
    slot.setAttribute("aria-label", tr("Dart {0}: not thrown", { 0: index + 1 }));
    return slot;
  }
  const name = dart.segment.name === "MISS" ? tr("Miss")
    : dart.segment.number === 25 ? (dart.segment.multiplier === 2 ? tr("Bull") : "25") : dart.segment.name;
  const points = document.createElement("small");
  points.textContent = String(dart.segment.number * dart.segment.multiplier);
  const label = document.createElement("span");
  label.textContent = name;
  slot.append(label, points);
  slot.dataset.ring = String(dart.segment.multiplier);
  slot.dataset.latest = String(latest);
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

/* Marks the visit's darts: a numbered dot where coordinates exist, else the segment hit. The newest one blinks. */
function renderBoard(turn) {
  const board = $("local-board-svg");
  for (const path of board.querySelectorAll("[data-state]")) delete path.dataset.state;
  const marks = [];
  turn.throws.forEach((dart, index) => {
    const state = index === turn.throws.length - 1 ? "latest" : "hit";
    if (!dart.coords) {
      const key = hitKey(dart.segment);
      if (key) for (const path of board.querySelectorAll(`[data-hit="${key}"]`)) path.dataset.state = state;
      return;
    }
    const [x, y] = boardPoint(dart.coords);
    const mark = document.createElementNS(SVG, "g");
    mark.setAttribute("class", "game-dart-mark");
    mark.dataset.state = state;
    mark.setAttribute("transform", `translate(${x} ${y})`);
    const pulse = document.createElementNS(SVG, "circle");
    pulse.setAttribute("class", "game-dart-pulse");
    pulse.setAttribute("r", "6");
    const dot = document.createElementNS(SVG, "circle");
    dot.setAttribute("r", "6");
    const label = document.createElementNS(SVG, "text");
    label.textContent = String(index + 1);
    mark.append(pulse, dot, label);
    marks.push(mark);
  });
  board.querySelector(".board-darts").replaceChildren(...marks);
}

/* Players sit on both sides of the board, with the current visit in a line below it.
   A single player keeps the visit under their card so the board can use the full height. */
function placeStage(count, board) {
  const stage = $("local-stage");
  stage.dataset.board = String(board);
  stage.dataset.count = String(count);
  $("local-board").hidden = !board;
  const solo = board && count === 1;
  $("local-match").dataset.solo = String(solo);
  const home = solo ? $("local-side-left") : $("local-center");
  if ($("local-turn").parentElement !== home) home.append($("local-turn"));
}

function renderMatch(value) {
  const frame = value.frame;
  const turn = frame.turns[0];
  const manual = value.options.input === "manual";
  const won = frame.winner >= 0;
  const player = frame.players[frame.player];
  const bot = !won && isBot(value.options.players[frame.player]);
  $("local-chip-score").textContent = String(value.options.starting_score);
  $("local-chip-out").textContent = tr(value.options.double_out ? "Double out" : "Straight out");
  $("local-chip-round").textContent = tr("Round {0}", { 0: frame.round + 1 });
  $("local-winner").hidden = !won;
  placeStage(frame.players.length, value.options.show_board);
  $("local-turn").hidden = $("local-turn-head").hidden = won;
  $("local-manual").hidden = !manual || won;
  // With the board shown, its halo replaces the detection badge.
  $("local-detector").parentElement.hidden = value.options.show_board && !manual;
  $("local-next").title = manual || bot ? ""
    : tr("Detected visits advance when you remove the darts. Use Next turn after clearing the board to finish an interrupted visit.");
  if (won) $("local-winner-name").textContent = tr("{0} wins!", { 0: frame.players[frame.winner].name });
  $("local-phase").textContent = bot ? tr(turn.busted ? "{0} · Bust" : "{0} is throwing…", { 0: player.name })
    : turn.busted ? tr("{0} · Bust — remove darts", { 0: player.name })
    : tr("{0} to throw", { 0: player.name });
  $("local-phase").dataset.bust = String(Boolean(turn.busted));
  const route = turn.finishedAt || won ? null
    : checkout(frame.gameScores[frame.player], 3 - turn.throws.length, value.options.double_out);
  $("local-checkout").hidden = !route;
  $("local-checkout").textContent = route ? tr("Checkout: {0}", { 0: route.map(dart => tr(dart)).join(" · ") }) : "";
  $("local-darts").replaceChildren(...[0, 1, 2].map(index => dartSlot(turn.throws[index], index, index === turn.throws.length - 1)));
  $("local-visit").textContent = turn.busted ? tr("Bust") : turn.points;
  $("local-undo").hidden = !manual;
  $("local-undo").disabled = working || bot || !turn.throws.length;
  $("local-next").disabled = working || won || bot || !turn.throws.length;
  $("local-stop").disabled = $("local-rematch").disabled = $("local-new").disabled = working;
  const closed = working || won || bot || Boolean(turn.finishedAt);
  const triple = radio("ring") === "3";
  for (const key of $("local-keys").querySelectorAll("button")) {
    key.disabled = closed || (triple && key.dataset.number === "25");
  }
  const signature = JSON.stringify(frame);
  if (signature === revision) return;
  revision = signature;
  // Only redraw on changes so the blinking animation is not restarted by every poll.
  renderBoard(turn);
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
    for (const text of [item.round + 1, owner?.name || "", item.throws.map(dart => dart.segment.name).join(" · "),
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
  const key = [innerWidth, innerHeight, $("main").clientWidth, $("local-match").hidden, board.hidden, $("local-manual").hidden,
    $("local-winner").hidden, $("local-detector-detail").hidden, $("local-stage").dataset.count].join();
  if (!force && key === fitted) return;
  fitted = key;
  if ($("local-match").hidden || board.hidden || !wide) {
    board.style.removeProperty("--board-fit");
    return;
  }
  const pad = $("local-manual");
  $("local-match").style.setProperty("--pad-space", pad.hidden ? "0px" : `${pad.offsetHeight + 16}px`);
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
    show(choosing ? "setup" : "modes");
  }
  $("local-start").disabled = working;
}

/* Actions */
function fail(cause) {
  $("local-error").textContent = tr(cause.message);
  $("local-error").hidden = false;
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
    show_board: radio("board") === "show",
    random_names: randomNames(),
  };
}

document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => {
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
  action(() => post("start", setupOptions()));
});
$("local-keys").addEventListener("click", event => {
  const key = event.target.closest("button[data-number]");
  if (!key || key.disabled) return;
  const multiplier = key.dataset.number === "0" ? 1 : Number(radio("ring"));
  setRadio("ring", "1");
  action(() => post("throw", { number: Number(key.dataset.number), multiplier }));
});
document.querySelectorAll('input[name="ring"]').forEach(input => input.addEventListener("change", () => {
  if (state?.active) renderMatch(state);
}));
$("local-undo").addEventListener("click", () => action(() => post("undo")));
$("local-next").addEventListener("click", () => action(() => post("next")));
$("local-stop").addEventListener("click", () => {
  if (confirm(tr("End this game? The score is kept until you start another game."))) action(() => post("stop"));
});
$("local-new").addEventListener("click", () => action(async () => {
  const result = await post("stop");
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
