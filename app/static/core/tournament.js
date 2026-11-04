import { $, api, poll } from "./client.mjs";
import { tr } from "../js/i18n.mjs";

const formatLabels = { single: "Single elimination", double: "Double elimination", swiss: "Swiss rounds", swiss_elimination: "Swiss + elimination" };
const stageLabels = { swiss: "Swiss rounds", upper: "Upper bracket", lower: "Lower bracket", final: "Grand final", reset: "Grand final reset" };
let saved = null, busy = false, signature = "", generation = 0;
const format = () => document.querySelector('input[name="tournament-format"]:checked').value;
const selectedId = new URLSearchParams(window.location.search).get("id");
const names = () => [...$("tournament-players").querySelectorAll("input")].map(input => input.value.trim());
const validNames = players => players.length >= 2 && players.length <= 32 && players.every(name => name.length > 0 && name.length <= 60)
  && new Set(players.map(name => name.toLocaleLowerCase())).size === players.length;
function renderRoster() {
  const rows = [...$("tournament-players").children];
  rows.forEach((row, index) => {
    const input = row.querySelector("input"), label = tr("Player {0}", { 0: index + 1 });
    input.placeholder = label; input.setAttribute("aria-label", label);
    if (input.dataset.auto === "true") input.value = label;
    const remove = row.querySelector("button"); remove.disabled = rows.length <= 2;
    remove.setAttribute("aria-label", tr("Remove {0}", { 0: input.value || label }));
  });
  $("tournament-add-player").disabled = rows.length >= 32;
  $("tournament-player-count").textContent = tr("{0} of {1}", { 0: rows.length, 1: 32 });
}
function addPlayer(name) {
  if (names().length >= 32) return;
  const row = element("li"), input = element("input"), remove = element("button", "×", "game-remove btn-neutral");
  input.maxLength = 60; input.required = true; input.value = name || "";
  input.dataset.auto = String(name === undefined);
  input.addEventListener("input", () => { input.dataset.auto = "false"; renderRoster(); });
  remove.type = "button";
  remove.addEventListener("click", () => { row.remove(); renderRoster(); });
  row.append(input, remove); $("tournament-players").append(row); renderRoster();
}
$("tournament-add-player").addEventListener("click", () => { addPlayer(); $("tournament-players").lastElementChild.querySelector("input").focus(); });
$("tournament-import").addEventListener("click", () => {
  const players = $("tournament-names").value.split(/\r?\n/).map(name => name.trim()).filter(Boolean);
  if (!validNames(players)) { fail(new Error(tr("Enter 2–32 different player names, up to 60 characters each."))); return; }
  $("tournament-error").hidden = true;
  $("tournament-players").replaceChildren(); players.forEach(addPlayer);
  $("tournament-names").closest("details").open = false;
});
addPlayer(); addPlayer();
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
$("tournament-choose-format").addEventListener("click", () => {
  const players = names();
  if (!$("tournament-name").reportValidity()) return;
  if (!validNames(players)) { fail(new Error(tr("Enter 2–32 different player names, up to 60 characters each."))); return; }
  $("tournament-error").hidden = true;
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
$("tournament-edit-players").addEventListener("click", () => { step(1); $("tournament-players").querySelector("input").focus(); });
document.querySelectorAll('input[name="tournament-format"]').forEach(input => input.addEventListener("change", updateFormat));

async function action(run) {
  if (busy) return;
  busy = true; generation++;
  $("tournament-error").hidden = true;
  $("tournament-setup").inert = $("tournament-dashboard").inert = true;
  $("tournament-end").disabled = true;
  try { await run(); } catch (error) { fail(error); }
  finally {
    busy = false; generation++;
    $("tournament-setup").inert = $("tournament-dashboard").inert = false;
    $("tournament-end").disabled = false;
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
    window.ocheNavigate(`/local/tournament?id=${encodeURIComponent(result.tournament.id)}`);
  });
});
$("tournament-end").addEventListener("click", () => {
  if (confirm(tr("End this tournament? Played results will be kept, but remaining matches will not be played."))) {
    action(async () => { await api("/api/tournament/cancel", "POST"); window.ocheNavigate("/local/tournament"); });
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
    window.ocheNavigate("/local");
  });
});
function render(result) {
  if (result.error) { fail(new Error(tr(result.error))); $("tournament-loading").hidden = true; return; }
  $("tournament-loading").hidden = true;
  // A live detail returns to the list on completion; explicit history links stay reviewable.
  if (saved?.state === "active" && result.tournament?.state === "completed") {
    window.ocheNavigate("/local/tournament"); return;
  }
  saved = result.tournament;
  $("tournament-setup").hidden = true;
  $("tournament-list").hidden = true;
  $("tournament-dashboard").hidden = !saved;
  $("tournament-list-link").hidden = false;
  $("tournament-end").hidden = !saved || saved.state !== "active";
  if (!saved) { fail(new Error(tr("Tournament not found."))); return; }
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
function renderList(result) {
  $("tournament-loading").hidden = true;
  if (result.error) { fail(new Error(tr(result.error))); return; }
  const active = result.tournaments.filter(item => item.state === "active");
  const past = result.tournaments.filter(item => item.state !== "active");
  $("tournament-setup").hidden = active.length > 0;
  $("tournament-active").hidden = active.length === 0;
  $("tournament-list").hidden = past.length === 0;
  const nextSignature = JSON.stringify(result.tournaments);
  if (signature === nextSignature) return;
  signature = nextSignature;
  const card = item => {
    const link = element("a", undefined, "card tournament-history-item");
    link.href = `/local/tournament?id=${encodeURIComponent(item.id)}`;
    const text = element("div");
    text.append(element("h2", item.name), element("p", [tr(formatLabels[item.format]), tr("{0} players", { 0: item.players })].join(" · "), "hint"));
    if (item.champion) text.append(element("p", tr("Winner: {0}", { 0: item.champion }), "tournament-history-winner"));
    link.append(text, element("span", tr(item.state === "active" ? "In progress" : item.state === "completed" ? "Finished" : "Ended"), "badge"), element("span", "→"));
    return link;
  };
  $("tournament-current").replaceChildren(...active.map(card));
  $("tournament-history").replaceChildren(...past.map(card));
}
async function refresh() {
  if (busy) return;
  const current = generation;
  try {
    const result = await api(selectedId ? `/api/tournament/history/${encodeURIComponent(selectedId)}` : "/api/tournament/history");
    if (!busy && current === generation) (selectedId ? render : renderList)(result);
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
