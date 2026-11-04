import { $ } from "./client.mjs";
import { tr } from "../js/i18n.mjs";

const ORDER = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5];
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
export function pitchPosition(region) {
  if (region === "B") return [500, 320];
  if (region === "OB") return [500, 303];
  const radius = { I: 65, T: 103, O: 135, D: 166 }[region[0]] / 170;
  const angle = ORDER.indexOf(Number(region.slice(1))) * Math.PI / 10;
  return [500 + Math.sin(angle) * radius * 450, 320 - Math.cos(angle) * radius * 270];
}
export function renderFootball(value) {
  const game = value.football, frame = value.frame;
  $("local-football").hidden = !game;
  for (const node of $("local-board-svg").querySelectorAll("[data-target]")) delete node.dataset.target;
  if (!game) return;
  for (let index = 0; index < 2; index++) {
    $("football-name-" + index).textContent = frame.players[index].name;
    $("football-score-" + index).textContent = frame.gameScores[index];
    $("football-team-" + index).dataset.active = String(frame.winner < 0 && frame.player === index);
    $("football-team-" + index).dataset.winner = String(frame.winner === index);
  }
  const [x, y] = pitchPosition(game.ball);
  $("football-ball").setAttribute("transform", `translate(${x} ${y})`);
  const goalX = frame.player === 0 ? 950 : 50;
  $("football-attack").setAttribute("points", `${x},${y} ${goalX},270 ${goalX},370`);
  $("football-attack").setAttribute("visibility", frame.winner < 0 ? "visible" : "hidden");
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
  if (!finished && frame.winner < 0) for (const region of game.targets) {
    for (const node of $("local-board-svg").querySelectorAll(`[data-region="${region}"]`)) node.dataset.target = "true";
  }
}
