import { tr as defaultTranslate } from "./i18n.mjs";
/* Status descriptions shared by the sidebar and its checks. */
export function localHealth(detector, tr = defaultTranslate) {
  const tone = detector?.detecting ? "good"
    : ["starting", "calibrating", "waiting"].includes(detector?.state) ? "warn" : "bad";
  return [tone, detector?.detecting ? tr("Detecting") : tr("Not detecting"),
    tr(detector?.reason) || tr("Cannot read local detector readiness. Check the vision engine's page in Integrations.")];
}

export function manualHealth(tr = defaultTranslate) {
  return ["good", tr("Manual scoring"),
    tr("Darts are entered manually. Select a vision engine in Play for automatic scoring.")];
}

export function localEventsHealth(local, tr = defaultTranslate) {
  if (!local) return ["bad", tr("Status unavailable"), tr("Cannot read local event status.")];
  if (!local.active) return ["neutral", tr("Waiting for local play"),
    tr("Local events connect when an automatic local game starts.")];
  if (local.input === "manual") return ["neutral", tr("Paused for local play"),
    tr("This game uses manual scoring.")];
  if (local.detector?.error || local.feed?.error) return ["bad", tr("Connection error"),
    tr(local.detector?.error || local.feed.error)];
  if (local.detector?.connected) return ["good", tr("Connected locally"),
    tr("Board events are received locally without a cloud connection.")];
  return ["warn", tr("Connecting"), tr("Waiting for local board events.")];
}

export function engineHealth(engine, detector, events, tr = defaultTranslate) {
  if (!engine) return ["bad", tr("Status unavailable"), ""];
  if (!engine.active) return ["neutral", tr("Inactive"), tr("This engine is not selected in Play.")];
  if (engine.installed === false || engine.status === "nofile") return ["bad", tr("Not installed"),
    tr(engine.unavailable_reason) || tr("Install this engine from Integrations.")];
  if (engine.status !== "running") return ["bad", tr("Stopped"),
    tr("Start this engine from its integration page.")];
  if (!engine.scores) return ["warn", tr("Not ready"),
    tr(engine.note) || tr("This engine does not support automatic scoring yet.")];
  const detection = localHealth(detector, tr);
  const priority = { neutral: 0, good: 1, warn: 2, bad: 3 };
  const worst = priority[events[0]] > priority[detection[0]] ? events : detection;
  const detail = [tr("Service: {state}", { state: tr("Running") }),
    tr("Detection: {state}", { state: detection[1] }), detection[2],
    tr("Events: {state}", { state: events[1] }), events[2]].filter(Boolean).join(" · ");
  return [worst[0], worst[0] === "good" ? tr("Ready") : worst[1], detail];
}

// Engines with a local event source, such as OcheCore, register this module.
export function health(_id, status, tr = defaultTranslate) {
  return localEventsHealth(status?.local, tr);
}

export function syncHealthRows(container, entries, tr = defaultTranslate) {
  const keep = new Set(entries.map(entry => entry.id));
  for (const row of container.querySelectorAll('[data-health]')) {
    if (!keep.has(row.dataset.health)) row.remove();
  }
  const existing = new Map([...container.querySelectorAll('[data-health]')].map(row => [row.dataset.health, row]));
  return entries.map((entry, index) => {
    let row = existing.get(entry.id);
    if (!row) {
      row = container.ownerDocument.createElement('a');
      row.id = `health-${entry.id}`;
      row.className = 'health-row';
      row.dataset.health = entry.id;
      const dot = container.ownerDocument.createElement('span');
      dot.className = 'health-dot';
      dot.setAttribute('aria-hidden', 'true');
      const label = container.ownerDocument.createElement('span');
      label.className = 'nav-text';
      row.append(dot, label);
    }
    row.href = entry.href;
    row.dataset.label = tr(entry.label);
    row.dataset.healthModule = entry.module || '';
    row.querySelector('.nav-text').textContent = tr(entry.label);
    // Leave unchanged rows in place so polling preserves keyboard focus.
    if (container.children[index] !== row) container.insertBefore(row, container.children[index] || null);
    return row;
  });
}
