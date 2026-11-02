import { tr } from "../js/i18n.mjs";
import { api, poll } from "./client.mjs";

// Detection engine cards: install from Updates, then choose which one reads the cameras.
const cards = new Map([...document.querySelectorAll("[data-engine]")].map(card => [card.dataset.engine, card]));
let working = false, loading = false, installing = null;

const part = (card, name) => card.querySelector(`[data-engine-${name}]`);
const action = (card, name) => card.querySelector(`[data-engine-action="${name}"]`);

function error(card, message = "") {
  part(card, "error").textContent = message;
  part(card, "error").hidden = !message;
}

function render(data) {
  for (const engine of data.engines) {
    const card = cards.get(engine.name);
    if (!card) continue;
    const installed = engine.installed;
    const running = engine.status === "running";
    const busy = installing === engine.name;
    const state = part(card, "state");
    state.textContent = busy ? tr("Installing…") : !installed ? tr("Not installed")
      : running ? (engine.active ? tr("Active · Running") : tr("Running")) : engine.active ? tr("Active · Stopped") : tr("Stopped");
    state.dataset.tone = busy ? "warning" : running ? "good" : "";
    card.classList.toggle("is-active", Boolean(engine.active && installed));
    part(card, "detail").textContent = !installed ? tr(engine.unavailable_reason || "")
      : engine.version ? tr("Version {0}", { 0: engine.version }) : "";
    action(card, "install").hidden = installed;
    action(card, "install").disabled = working || Boolean(installing);
    const use = action(card, "use");
    use.hidden = !installed || (engine.active && running);
    use.textContent = engine.active ? tr("Start") : tr("Use this engine");
    use.disabled = working;
    action(card, "stop").hidden = !running;
    action(card, "stop").disabled = working;
    const link = part(card, "link");
    if (engine.name === "ochecore") link.href = `http://${location.hostname}:${engine.port}/`;
    link.hidden = !installed || (engine.name === "ochecore" && !running);
  }
}

async function refresh() {
  if (working || loading || !cards.size) return;
  loading = true;
  try {
    render(await api("/api/engines"));
  } catch (cause) {
    for (const card of cards.values()) error(card, cause.message);
  } finally { loading = false; }
}

async function run(card, request) {
  if (working) return;
  working = true;
  error(card);
  try {
    render(await request());
  } catch (cause) { error(card, cause.message); }
  finally { working = false; await refresh(); }
}

// Installing runs as an update job; follow it until the engine's files are in place.
async function install(card) {
  const name = card.dataset.engine;
  installing = name;
  error(card);
  try {
    await api(`/updates/${name}/update`, "POST");
    for (;;) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      const status = await api("/updates/status");
      if (status.job.status !== "running") {
        if (status.job.status === "error") throw new Error(status.job.message);
        break;
      }
    }
  } catch (cause) { error(card, cause.message); }
  finally { installing = null; await refresh(); }
}

for (const card of cards.values()) {
  const name = card.dataset.engine;
  action(card, "install").addEventListener("click", () => install(card));
  action(card, "use").addEventListener("click", () => run(card, () => api(`/api/engines/${name}/use`, "POST")));
  action(card, "stop").addEventListener("click", () => run(card, () => api(`/api/engines/${name}/stop`, "POST")));
}
poll(refresh);
