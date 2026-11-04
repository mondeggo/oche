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
  if (region === "D11") return [27, 320];
  if (region === "D6") return [973, 320];
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
    $("football-direction-" + index).textContent = tr(game.attacks[index] === "D6" ? "Attacks D6 →" : "← Attacks D11");
    $("football-team-" + index).dataset.active = String(game.phase !== "finished" && frame.player === index);
    $("football-team-" + index).dataset.winner = String(frame.winner === index);
  }
  const [x, y] = pitchPosition(game.ball);
  $("football-ball").setAttribute("transform", `translate(${x} ${y})`);
  const goalX = game.attacks[frame.player] === "D6" ? 950 : 50;
  $("football-attack").setAttribute("points", `${x},${y} ${goalX},270 ${goalX},370`);
  $("football-attack").setAttribute("visibility", game.phase !== "finished" && game.phase !== "bullup" ? "visible" : "hidden");
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
  if (!finished && game.phase !== "finished") for (const region of targets) {
    for (const node of $("local-board-svg").querySelectorAll(`[data-region="${region}"]`)) node.dataset.target = "true";
  }
}
