import { tr } from "../js/i18n.mjs";
import { api, poll } from "./client.mjs";

// Switches for the integrations that registers themselves, and their engine status.
const cards = new Map([...document.querySelectorAll("[data-integration-card]")]
  .map(card => [card.dataset.integrationCard, card]));
let working = false, loading = false;

const part = (card, name) => card.querySelector(`[data-${name}]`);

function error(card, message = "") {
  part(card, "integration-error").textContent = message;
  part(card, "integration-error").hidden = !message;
}

function renderSwitch(card, enabled) {
  const toggle = part(card, "integration-switch");
  toggle.setAttribute("aria-checked", String(enabled));
  part(card, "switch-label").textContent = enabled ? tr("On") : tr("Off");
  const link = part(card, "integration-link");
  if (link) link.hidden = !enabled;
  for (const slot of document.querySelectorAll(`[data-integration-tools="${card.dataset.integrationCard}"]`)) {
    slot.hidden = !enabled;
  }
}

function renderEngines(data) {
  for (const engine of data.engines) {
    const card = cards.get(engine.name);
    if (!card) continue;
    const parts = [!engine.installed ? tr("Not installed") : engine.status === "running" ? tr("Running") : tr("Stopped")];
    if (engine.version) parts.push(tr("Version {0}", { 0: engine.version }));
    if (engine.enabled && engine.active) parts.push(tr("Used by Play"));
    if (!engine.scores && engine.note) parts.push(tr(engine.note));
    part(card, "engine-detail").textContent = parts.join(" · ");
  }
}

// The sidebar shows what switched-on integrations add; copy its visibility from a fresh page.
async function syncSidebar() {
  const response = await fetch(location.href, { cache: "no-store" });
  if (!response.ok) return;
  const updated = new DOMParser().parseFromString(await response.text(), "text/html");
  for (const source of updated.querySelectorAll("#app-sidebar [data-nav-sync][id]")) {
    const target = document.getElementById(source.id);
    if (target) target.hidden = source.hidden;
  }
}

async function refresh() {
  if (working || loading || !cards.size) return;
  loading = true;
  try {
    renderEngines(await api("/api/engines"));
  } catch (cause) {
    for (const card of cards.values()) error(card, cause.message);
  } finally { loading = false; }
}

for (const [name, card] of cards) {
  const toggle = part(card, "integration-switch");
  toggle.addEventListener("click", async () => {
    if (working) return;
    working = toggle.disabled = true;
    error(card);
    const enabled = toggle.getAttribute("aria-checked") !== "true";
    try {
      const list = await api(`/api/integrations/${name}`, "PUT", { enabled });
      renderSwitch(card, list.find(item => item.id === name)?.enabled ?? enabled);
      await syncSidebar();
    } catch (cause) { error(card, cause.message); }
    finally { working = toggle.disabled = false; await refresh(); }
  });
}
poll(refresh);
