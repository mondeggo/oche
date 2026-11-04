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
    card.querySelector('[data-engine-action="uninstall"]').disabled = working || !engine.installed;
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
  for (const button of card.querySelectorAll("[data-engine-action]")) {
    button.addEventListener("click", async () => {
      if (working) return;
      const action = button.dataset.engineAction;
      const warning = action === "clear-cache"
        ? tr("Reset all {name} engine data? This stops the engine and permanently deletes its settings, calibration and logs. You will need to configure and calibrate it again.", { name })
        : tr("Uninstall {name}? This stops the engine and removes all installed versions and rollback history. Settings and calibration are kept. You can reinstall it from Updates.", { name });
      if (!confirm(warning)) return;
      working = true;
      button.disabled = true;
      error(card);
      try {
        await api(`/api/engines/${name}/${action}`, "POST", { confirmed: true });
        location.reload();
      } catch (cause) { error(card, cause.message); }
      finally { working = false; button.disabled = false; await refresh(); }
    });
  }
  const toggle = part(card, "integration-switch");
  toggle.addEventListener("click", async () => {
    if (working) return;
    working = toggle.disabled = true;
    error(card);
    const enabled = toggle.getAttribute("aria-checked") !== "true";
    try {
      const list = await api(`/api/integrations/${name}`, "PUT", { enabled });
      renderSwitch(card, list.find(item => item.id === name)?.enabled ?? enabled);
      // Home shows what switched-on integrations add, with their scripts: reload it.
      location.reload();
      return;
    } catch (cause) { error(card, cause.message); }
    finally { working = toggle.disabled = false; await refresh(); }
  });
}
poll(refresh);
