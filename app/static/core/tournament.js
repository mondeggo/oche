import { $, api, poll } from "./client.mjs";
import { tr } from "../js/i18n.mjs";

const formatLabels = { single: "Single elimination", double: "Double elimination", swiss: "Swiss rounds", swiss_elimination: "Swiss + elimination" };
const stageLabels = { swiss: "Swiss rounds", upper: "Upper bracket", lower: "Lower bracket", final: "Grand final", reset: "Grand final reset" };
let saved = null, busy = false, creating = false, signature = "", generation = 0;
const format = () => document.querySelector('input[name="tournament-format"]:checked').value;
const names = () => $("tournament-names").value.split(/\r?\n/).map(name => name.trim()).filter(Boolean);
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function fail(error) {
  $("tournament-error").textContent = error.message;
  $("tournament-error").hidden = false;
  $("tournament-error").scrollIntoView({ block: "nearest" });
}
function step(number) {
  $("tournament-players-step").hidden = number !== 1;
  $("tournament-format-step").hidden = number !== 2;
  for (const [id, active] of [["tournament-step-players", number === 1], ["tournament-step-format", number === 2]]) {
    if (active) $(id).setAttribute("aria-current", "step"); else $(id).removeAttribute("aria-current");
  }
}
function updateFormat() {
  const swiss = format().startsWith("swiss");
  $("tournament-rounds-field").hidden = !swiss;
  $("tournament-rounds").disabled = !swiss;
  $("tournament-finalists-field").hidden = format() !== "swiss_elimination";
  $("tournament-finalists").disabled = format() !== "swiss_elimination";
  $("tournament-format-detail").textContent = format() === "double"
    ? tr("The lower-bracket winner must beat the unbeaten finalist twice. A grand final reset is added if needed.")
    : swiss ? tr("Pairings avoid repeat opponents where possible. Byes go to the lowest-ranked player who has had the fewest byes.")
      : tr("Registration order sets the initial seeds. Byes advance players automatically when the bracket is uneven.");
}
$("tournament-names").addEventListener("input", () => {
  $("tournament-names").setCustomValidity("");
  $("tournament-player-count").textContent = tr("{0} players", { 0: names().length });
});
$("tournament-choose-format").addEventListener("click", () => {
  const players = names();
  const valid = players.length >= 2 && players.length <= 32 && players.every(name => name.length <= 60)
    && new Set(players.map(name => name.toLocaleLowerCase())).size === players.length;
  $("tournament-names").setCustomValidity(valid ? "" : tr("Enter 2–32 different player names, up to 60 characters each."));
  if (!$("tournament-name").reportValidity() || !$("tournament-names").reportValidity()) return;
  $("tournament-rounds").max = String(Math.min(10, players.length - 1));
  $("tournament-rounds").value = String(Math.min(players.length - 1, Math.ceil(Math.log2(players.length))));
  $("tournament-finalists").replaceChildren();
  for (let count = 2; count <= players.length; count *= 2) {
    const option = element("option", String(count));
    option.value = String(count);
    option.selected = count === Math.min(4, 2 ** Math.floor(Math.log2(players.length)));
    $("tournament-finalists").append(option);
  }
  step(2);
  updateFormat();
  document.querySelector('input[name="tournament-format"]:checked').focus();
});
$("tournament-edit-players").addEventListener("click", () => { step(1); $("tournament-names").focus(); });
document.querySelectorAll('input[name="tournament-format"]').forEach(input => input.addEventListener("change", updateFormat));

async function action(run) {
  if (busy) return;
  busy = true; generation++;
  $("tournament-error").hidden = true;
  $("tournament-setup").inert = $("tournament-dashboard").inert = true;
  $("tournament-new").disabled = $("tournament-end").disabled = true;
  try { await run(); } catch (error) { fail(error); }
  finally {
    busy = false; generation++;
    $("tournament-setup").inert = $("tournament-dashboard").inert = false;
    $("tournament-new").disabled = $("tournament-end").disabled = false;
  }
}
$("tournament-setup").addEventListener("submit", event => {
  event.preventDefault();
  if (!$("tournament-format-step").hidden) action(async () => {
    const result = await api("/api/tournament", "POST", {
      name: $("tournament-name").value.trim(), players: names(), format: format(),
      swiss_rounds: Number($("tournament-rounds").value), finalists: Number($("tournament-finalists").value),
      starting_score: Number($("tournament-score").value), double_out: $("tournament-out").value === "double",
      input: $("tournament-input").value,
    });
    creating = false; signature = ""; render(result);
  });
});
$("tournament-new").addEventListener("click", () => {
  creating = true; $("tournament-setup").hidden = false; $("tournament-dashboard").hidden = true;
  $("tournament-new").hidden = true; $("tournament-title").textContent = tr("New tournament");
  step(1); $("tournament-name").focus();
});
$("tournament-end").addEventListener("click", () => {
  if (confirm(tr("End this tournament? Played results will be kept, but remaining matches will not be played."))) {
    action(async () => render(await api("/api/tournament/cancel", "POST")));
  }
});
function playButton(match, running) {
  if (match.running) {
    const link = element("a", tr("Resume match"), "tournament-action"); link.href = "/local"; return link;
  }
  const button = element("button", tr("Play match"));
  button.type = "button"; button.dataset.match = match.id; button.disabled = running;
  return button;
}
$("tournament-dashboard").addEventListener("click", event => {
  const button = event.target.closest("button[data-match]");
  if (!button || button.disabled) return;
  action(async () => {
    await api(`/api/tournament/matches/${button.dataset.match}/start`, "POST");
    window.location.assign("/local");
  });
});
function render(result) {
  if (result.error) { fail(new Error(tr(result.error))); $("tournament-loading").hidden = true; return; }
  saved = result.tournament;
  $("tournament-loading").hidden = true;
  if (creating) return;
  $("tournament-setup").hidden = Boolean(saved);
  $("tournament-dashboard").hidden = !saved;
  $("tournament-new").hidden = !saved || saved.state === "active";
  $("tournament-end").hidden = !saved || saved.state !== "active";
  if (!saved) {
    if (signature !== "empty") { step(1); signature = "empty"; }
    return;
  }
  const nextSignature = JSON.stringify(saved);
  if (nextSignature === signature) return;
  signature = nextSignature;
  $("tournament-title").textContent = saved.options.name;
  $("tournament-summary").textContent = [tr(formatLabels[saved.options.format]),
    tr("{0} players", { 0: saved.players.length }), String(saved.options.starting_score),
    tr(saved.options.double_out ? "Double out" : "Straight out"),
    tr(saved.options.input === "manual" ? "Manual" : "Selected vision engine")].join(" · ");
  const played = saved.matches.filter(match => match.completed && match.players.every(Boolean)).length;
  $("tournament-progress").textContent = tr("{0} matches played", { 0: played });
  const playerNames = new Map(saved.players.map(player => [player.id, player.name]));
  $("tournament-champion").hidden = saved.state !== "completed";
  $("tournament-champion-name").textContent = playerNames.get(saved.champion) || "";
  $("tournament-cancelled").hidden = saved.state !== "cancelled";
  const running = saved.matches.some(match => match.running);
  const next = saved.matches.find(match => match.running) || saved.matches.find(match => match.ready);
  $("tournament-next").hidden = !next || saved.state !== "active";
  if (next) {
    const intro = element("div");
    intro.append(element("p", tr(next.running ? "Match in progress" : "Next match"), "eyebrow"),
      element("h2", next.players.map(id => playerNames.get(id)).join(" · ")));
    $("tournament-next").replaceChildren(intro, playButton(next, running));
  }
  const swiss = saved.options.format.startsWith("swiss");
  $("tournament-standings-section").hidden = !swiss;
  const qualifying = saved.options.format === "swiss_elimination";
  $("tournament-qualification").hidden = !qualifying;
  $("tournament-qualification").textContent = tr("The top {0} players qualify for the final bracket.", { 0: saved.options.finalists });
  $("tournament-standings").replaceChildren(...saved.standings.map((player, index) => {
    const row = element("tr"); row.dataset.qualified = String(qualifying && index < saved.options.finalists);
    for (const value of [index + 1, player.name, player.points, player.played, player.buchholz, player.byes]) row.append(element("td", String(value)));
    return row;
  }));
  const matches = new Map(saved.matches.map((match, index) => [match.id, { ...match, number: index + 1 }]));
  function slotText(slot) {
    if (slot.kind === "player") return playerNames.get(slot.ref) || tr("Bye");
    const source = matches.get(slot.ref);
    if (source.completed) return playerNames.get(slot.kind === "winner" ? source.winner
      : source.players.find(id => id && id !== source.winner)) || tr("Bye");
    return tr(slot.kind === "winner" ? "Winner of match {0}" : "Loser of match {0}", { 0: source.number });
  }
  const sections = [];
  const stageOrder = saved.options.format === "swiss_elimination" && saved.matches.some(match => match.stage === "upper")
    ? ["upper", "swiss"] : Object.keys(stageLabels);
  for (const stage of stageOrder) {
    const items = [...matches.values()].filter(match => match.stage === stage);
    if (!items.length) continue;
    const section = element("section", undefined, "tournament-bracket");
    section.dataset.stage = stage;
    const stageTitle = stage === "upper" && saved.options.format !== "double" ? tr("Elimination bracket") : tr(stageLabels[stage]);
    section.append(element("h2", stageTitle));
    const rounds = element("div", undefined, "tournament-rounds");
    rounds.tabIndex = 0; rounds.setAttribute("role", "region"); rounds.setAttribute("aria-label", stageTitle);
    for (const number of [...new Set(items.map(match => match.round))]) {
      const column = element("section", undefined, "tournament-round");
      column.append(element("h3", tr("Round {0}", { 0: number })));
      const cards = element("div", undefined, "tournament-round-matches");
      for (const match of items.filter(match => match.round === number)) {
        const card = element("article", undefined, "tournament-match"); card.dataset.ready = String(match.ready);
        const header = element("header"); header.append(element("span", tr("Match {0}", { 0: match.number })));
        if (match.completed) header.append(element("span", tr(match.players.every(Boolean) ? "Finished" : "Bye")));
        card.append(header);
        match.slots.forEach((slot, index) => {
          const line = element("p", slotText(slot));
          line.dataset.winner = String(Boolean(match.winner) && match.players[index] === match.winner);
          if (line.dataset.winner === "true") line.append(element("span", " ✓"));
          card.append(line);
        });
        if (match.ready) card.append(playButton(match, running));
        cards.append(card);
      }
      column.append(cards); rounds.append(column);
    }
    section.append(rounds); sections.push(section);
  }
  $("tournament-brackets").replaceChildren(...sections);
}
async function refresh() {
  if (busy) return;
  const current = generation;
  try {
    const result = await api("/api/tournament");
    if (!busy && current === generation) render(result);
  } catch (error) { fail(error); $("tournament-loading").hidden = true; }
}
api("/api/engines").then(data => {
  const engine = data.engines.find(item => item.active && item.enabled && item.scores && item.installed);
  if (engine) {
    $("tournament-automatic").disabled = false;
    $("tournament-automatic").textContent = engine.label;
    $("tournament-input").value = "autodarts";
  }
}).catch(fail);
updateFormat();
poll(refresh);
