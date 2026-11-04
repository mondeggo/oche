import { $ } from "./client.mjs";
import { tr } from "../js/i18n.mjs";

import { ORDER, CELLS, cellPath, cellPosition } from "./football-pitch.mjs";
export function regionLabel(region) {
  if (region === "B") return tr("Bullseye");
  if (region === "OB") return tr("Outer bull");
  if (region[0] === "I") return tr("Inner S{0}", { 0: region.slice(1) });
  if (region[0] === "O") return tr("Outer S{0}", { 0: region.slice(1) });
  return region;
}
export function dartRegion(dart) {
  const { number, multiplier } = dart.segment;
  if (!number) return null;
  if (number === 25) return multiplier === 2 ? "B" : "OB";
  if (multiplier !== 1) return `${multiplier === 3 ? "T" : "D"}${number}`;
  return `${Math.hypot(dart.coords.x, dart.coords.y) * 170 < 99 ? "I" : "O"}${number}`;
}
export const pitchPosition = cellPosition;
let pitchState = null, inspected = null, morph = 1, pitchReady = false, matchId = null;

function drawPitch() {
  if (!pitchState) return;
  const { football: game, frame, options } = pitchState;
  const finished = Boolean(frame.turns[0].finishedAt) || game.phase === "finished";
  const selected = inspected || game.ball;
  const targets = inspected
    ? game.neighbours[inspected].filter(cell => options.football_own_goals || cell !== game.attacks[1 - frame.player])
    : finished ? [] : game.targets;
  for (const node of $("football-cells").children) {
    const cell = node.dataset.cell;
    node.setAttribute("d", cellPath(cell, morph));
    node.dataset.selected = String(cell === selected);
    node.dataset.neighbour = String(targets.includes(cell));
    node.setAttribute("aria-pressed", String(cell === inspected));
    node.tabIndex = cell === selected ? 0 : -1;
  }
  for (const node of $("football-cell-labels").children) {
    const [x, y] = node.dataset.goal ? cellPosition(node.dataset.goal, morph) : cellPosition("O" + node.dataset.number, morph);
    node.setAttribute("x", x); node.setAttribute("y", y);

  }
  const [x, y] = cellPosition(game.ball, morph);
  $("football-ball").setAttribute("transform", `translate(${x} ${y})`);
  const goal = cellPosition(game.attacks[frame.player], morph);
  $("football-attack").setAttribute("points", `${x},${y} ${goal[0]},${goal[1] - 28} ${goal[0]},${goal[1] + 28}`);

  $("football-follow").disabled = !inspected;
  $("football-explore").textContent = inspected
    ? tr("Exploring {0}: {1}", { 0: regionLabel(inspected), 1: targets.map(regionLabel).join(" · ") })
    : tr("Select a cell to explore its neighbours.");
  $("football-explore").title = $("football-explore").textContent;
}

function preparePitch() {
  if (pitchReady) return;
  pitchReady = true;
  const ns = "http://www.w3.org/2000/svg";
  for (const cell of CELLS) {
    const path = document.createElementNS(ns, "path");
    path.dataset.cell = cell;
    path.dataset.ring = cell === "B" || cell === "OB" ? cell : cell[0];
    path.dataset.alt = String(ORDER.indexOf(Number(cell.slice(1))) % 2 === 0);
    path.dataset.goal = String(cell === "D6" || cell === "D11");
    path.setAttribute("role", "button");
    path.setAttribute("aria-label", regionLabel(cell));
    const title = document.createElementNS(ns, "title"); title.textContent = regionLabel(cell); path.append(title);
    $("football-cells").append(path);
  }
  for (const number of ORDER) {
    const label = document.createElementNS(ns, "text");
    label.dataset.number = number; label.textContent = number;
    $("football-cell-labels").append(label);
  }
  for (const cell of ["D6", "D11"]) {
    const label = document.createElementNS(ns, "text");
    label.dataset.goal = cell; label.textContent = cell;
    $("football-cell-labels").append(label);
  }
  $("football-cells").addEventListener("click", event => {
    const cell = event.target.closest("[data-cell]")?.dataset.cell;
    if (cell) { inspected = cell; drawPitch(); }
  });
  $("football-cells").addEventListener("keydown", event => {
    const cell = event.target.closest("[data-cell]")?.dataset.cell;
    if (!cell) return;
    if (["Enter", " ", "ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      const step = ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1;
      inspected = event.key.startsWith("Arrow") ? CELLS[(CELLS.indexOf(cell) + step + CELLS.length) % CELLS.length] : cell;
      drawPitch(); $("football-cells").querySelector(`[data-cell="${inspected}"]`).focus();
    }
    if (event.key === "Escape") { inspected = null; drawPitch(); }
  });
  $("football-morph").addEventListener("input", event => { morph = Number(event.target.value) / 100; drawPitch(); });
  $("football-follow").addEventListener("click", () => { inspected = null; drawPitch(); });
}

export function renderFootball(value) {
  const game = value.football, frame = value.frame;
  $("local-football").hidden = !game;
  for (const node of $("local-board-svg").querySelectorAll("[data-target]")) delete node.dataset.target;
  if (!game) return;
  if (matchId !== frame.id) { inspected = null; matchId = frame.id; }
  pitchState = value;
  preparePitch();
  for (let index = 0; index < 2; index++) {
    $("football-name-" + index).textContent = frame.players[index].name;
    $("football-score-" + index).textContent = frame.gameScores[index];
    $("football-direction-" + index).textContent = tr(game.attacks[index] === "D6" ? "Attacks D6 →" : "← Attacks D11");
    $("football-team-" + index).dataset.active = String(game.phase !== "finished" && frame.player === index);
    $("football-team-" + index).dataset.winner = String(frame.winner === index);
  }
  $("football-attack").setAttribute("visibility", "hidden");
  const finished = Boolean(frame.turns[0].finishedAt);
  const goal = game.goal_by !== null;
  $("football-message").textContent = frame.winner >= 0 ? tr("{0} wins!", { 0: frame.players[frame.winner].name })
    : goal ? tr(game.event === "own_goal" ? "Own goal! {0} scores." : "Goal for {0}!", { 0: frame.players[game.goal_by].name })
    : game.event === "invalid" ? tr("No move — the ball stays put.")
    : game.kickoff ? tr("Kick-off") : tr("Ball: {0}", { 0: regionLabel(game.ball) });
  $("football-target").textContent = frame.winner >= 0 ? tr("Final score: {0} – {1}", { 0: frame.gameScores[0], 1: frame.gameScores[1] })
    : goal ? tr("{0} kicks off next.", { 0: frame.players[1 - game.goal_by].name })
    : finished ? tr("Remove the darts, then continue.")
    : game.kickoff ? tr(value.options.football_kickoff === "outer" ? "Hit the outer bull to start."
      : value.options.football_kickoff === "bull" ? "Hit either bull to start." : "Hit either bull or an inner single to start.")
    : game.ball === "OB" ? tr("Next targets: bullseye or any inner single.")
    : tr("Next targets: {0}", { 0: game.targets.map(regionLabel).join(" · ") });
  const bullup = game.phase === "bullup", penalties = game.phase === "penalties";
  $("football-bullup-entry").hidden = !bullup || value.options.input !== "manual" || finished || value.options.players[frame.player].bot || value.options.players[frame.player].auto;
  $("football-bullup-submit").disabled = finished;
  $("football-confirm").hidden = !game.pending;
  $("football-clock").textContent = value.options.football_format === "rounds" ? game.clock : tr("Half {0}", { 0: game.half });
  if (bullup) {
    $("football-message").textContent = tr(game.event === "bullup_tie" ? "Bull-up tied — throw again" : "Bull-up");
    $("football-target").textContent = game.bullup.map((distance, index) => `${frame.players[index].name}: ${distance === null ? "—" : distance.toFixed(1) + " mm"}`).join(" · ") + " · " + tr("Closest to the bull starts. One dart each.");
  } else if (penalties) {
    $("football-clock").textContent = tr("Full-time") + " · 90′";
    $("football-message").textContent = tr("Penalty shootout");
    $("football-target").textContent = tr("One dart at {0}. Both players shoot before deciding the winner.", { 0: game.attacks[frame.player] }) + ` (${game.penalty_score.join(" – ")})`;
  } else if (game.phase === "finished") {
    $("football-clock").textContent = tr("Full-time");
    $("football-target").textContent = tr("Final score: {0} – {1}", { 0: frame.gameScores[0], 1: frame.gameScores[1] });
    if (game.penalty_score.some(score => score > 0)) $("football-target").textContent += " · " + tr("Penalties: {0} – {1}", { 0: game.penalty_score[0], 1: game.penalty_score[1] });
    $("football-message").textContent = frame.winner < 0 ? tr("Draw") : tr("{0} wins!", { 0: frame.players[frame.winner].name });
  } else if (game.event === "halftime") {
    $("football-message").textContent = tr("Half-time");
    $("football-target").textContent = tr("Goals swapped. {0} kicks off the second half.", { 0: frame.players[frame.player].name });
  } else if (game.phase === "golden") {
    $("football-clock").textContent = tr("Full-time · Golden goal");
  } else if (value.options.football_format === "rounds") {
    const halfLength = game.half === 1 ? Math.floor(value.options.football_rounds / 2) : Math.ceil(value.options.football_rounds / 2);
    const played = game.half === 1 ? game.rounds : game.rounds - Math.floor(value.options.football_rounds / 2) - game.added[0];
    if (played >= halfLength - 1) $("football-clock").textContent += " · " + tr("Final minutes");
  }
  if (game.pending) $("football-target").textContent = tr("Hit near a wire. Confirm its cell before play continues.");
  const targets = penalties ? [game.attacks[frame.player]] : game.targets.filter(region => value.options.football_own_goals || region !== game.attacks[1 - frame.player]);
  drawPitch();
  if (!finished && game.phase !== "finished") for (const region of targets) {
    for (const node of $("local-board-svg").querySelectorAll(`[data-region="${region}"]`)) node.dataset.target = "true";
  }
}
