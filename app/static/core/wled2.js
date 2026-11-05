import { chooseLightShape, lightShapeNames, registerLightingEditor } from "./lighting-view.mjs";
import { tr } from "../js/i18n.mjs";
import { notice, page, registerProfileEditor, profileChanging } from "./app.js";
import { $, api, fitPanel, poll, simulationFace, simulationGame, simulationPixel } from "./client.mjs";
import { sourceControls } from "./sources.mjs";

// The scene is a draft. Device traffic and game decisions stay in the service.
let layout, defaults, revision, live;
let selected = "board", dirty = false, busy = false, conflict = false;
let loading = null, wireSource = null, wiring = false, drag = null;
const playSources = sourceControls("wled2", (key, value) => {
  layout[key] = value;
  changed();
});
let lastTap = null, wirePointer = null, wireDrag = null;
let editingName = null;
let simulation = null, simulationTimer = null, animationTimer = null, livePlayback = null, simulationGeneration = 0;
const demoGame = simulationGame();
function stopSimulation() {
  simulationGeneration++;
  clearTimeout(simulationTimer); clearInterval(animationTimer);
  simulationTimer = animationTimer = null; simulation = null;
  $("w2-simulate").setAttribute("aria-pressed", "false");
  $("w2-stop").disabled = true;
  $("w2-simulation-status").textContent = livePlayback ? tr("Stopped - restoring live lights...") : tr("Canvas simulation stopped");
  renderCanvas();
}
function simulateEvent(event, phase, values = {}) {
  simulation = { event, phase, remaining: 501, local_remaining: 501,
    opponent_remaining: 501, turn_score: 0, last_dart: 0,
    ...values, started: performance.now() };
  $("w2-stop").disabled = false;
  $("w2-simulation-status").textContent = tr("Round {0} - {1}: {2} - You {3} / Opponent {4}", {"0": (simulation.round || 1), "1": (simulation.player === 1 ? tr("Opponent") : tr("You")), "2": (events[event] || phases[phase]), "3": (simulation.local_remaining), "4": (simulation.opponent_remaining)});
  clearInterval(animationTimer);
  animationTimer = setInterval(() => {
    if (page !== "wled2") { stopSimulation(); return; }
    if (!drag && !editingName && !canvas.contains(document.activeElement)) renderCanvas();
    else animateSimulation();
  }, 80);
  renderCanvas();
}
function animateSimulation() {
  if (!simulation || !layout) return;
  const elapsed = (performance.now() - simulation.started) / 1000;
  for (const light of layout.lights) {
    const rules = light.rules || defaults.rules;
    const effect = rules.effects[simulation.event];
    const active = effect?.enabled !== false && effect && elapsed < effect.duration;
    const style = active ? effect : light.kind === "matrix" ? light.matrix.appearance : rules.phases[simulation.phase];
    const group = [...canvas.querySelectorAll("[data-object]")].find(node => node.dataset.object === light.id);
    const leds = group?.querySelectorAll(".w2-led");
    leds?.forEach((led, index) => {
      if (!light.enabled) return;
      led.dataset.baseFill ||= led.getAttribute("fill");
      const matrixLit = light.kind === "matrix" && led.dataset.baseFill !== "#2b343d";
      const pixel = simulationPixel(style, elapsed, index, leds.length);
      led.setAttribute("fill", light.kind !== "matrix" || matrixLit ? pixel.color : "#2b343d");
      led.style.opacity = String(pixel.opacity);
    });
  }
}
async function runSimulation(index = 0) {
  if (!layout || page !== "wled2" || index >= demoGame.length) { stopSimulation(); return; }
  const { event, phase, ...values } = demoGame[index];
  simulateEvent(event, phase, values);
  $("w2-simulate").setAttribute("aria-pressed", "true");
  const duration = Math.max(1.5, ...layout.lights.map(light => {
    const effect = (light.rules || defaults.rules).effects[event];
    return effect?.enabled !== false ? effect?.duration || 0 : 0;
  }));
  const frame = simulation;
  const started = performance.now();
  try {
    await showLive(frame, duration);
    if (simulation !== frame) return;
    simulationTimer = setTimeout(() => runSimulation(index + 1),
      Math.max(0, (duration + .5) * 1000 - (performance.now() - started)));
  } catch (cause) { stopSimulation(); error(cause.message); }
}
async function showLive(frame, duration) {
  if (!$("w2-show-live").checked) return;
  const { event, phase, local_remaining, opponent_remaining, turn_score, last_dart, player } = frame;
  livePlayback = api("/api/wled2/simulate", "POST", {
    event: event || null, phase, local_remaining, opponent_remaining, turn_score, last_dart,
    player: player || 0, duration: Math.min(3, duration),
  });
  try { await livePlayback; }
  finally {
    livePlayback = null;
    if (!simulation) $("w2-simulation-status").textContent = tr("Simulation stopped - live preview ended");
  }
}
function startSimulation(game) {
  stopSimulation();
  const generation = simulationGeneration;
  work(async () => {
    if (livePlayback) await livePlayback;
    if ($("w2-show-live").checked) await save();
    if (generation !== simulationGeneration) return;
    if (game) { runSimulation(); return; }
    simulateEvent($("w2-event").value, $("w2-test-phase").value);
    showLive(simulation, 3).catch(cause => { stopSimulation(); error(cause.message); });
  });
}
const probes = new Map();
const canvas = $("w2-canvas");
const phases = { ready: tr("Ready to throw"), takeout: tr("Remove darts"), waiting: tr("Waiting"), idle: tr("Idle") };
const events = {
  throw: tr("Any dart"), single: tr("Single"), double: tr("Double"), triple: tr("Triple"),
  outer_bull: tr("Outer bull"), bull: tr("Bullseye"), miss: tr("Miss"), score_180: "180", bust: tr("Bust"),
  leg_win: tr("Leg won"), match_win: tr("Match won"), match_started: tr("Match started"), match_ended: tr("Match ended"),
  turn_started: tr("Turn started"), takeout_started: tr("Takeout started"), takeout_finished: tr("Takeout finished"),
  manual_reset: tr("Manual reset"), calibration_started: tr("Calibration started"), calibration_finished: tr("Calibration finished"),
};
const sources = {
  remaining: tr("Active player's score"), local_remaining: tr("My score (one local player)"),
  opponent_remaining: tr("Opponent's score"), player_remaining: tr("Specific player's score"),
  turn_score: tr("Active visit total"), last_dart: tr("Last dart"),
};
// getRandomValues also works when the local service is opened over plain LAN HTTP.
const uid = (prefix) => `${prefix}-${Array.from(crypto.getRandomValues(new Uint8Array(8)), n => n.toString(16).padStart(2, "0")).join("")}`;
const clone = (value) => structuredClone(value);
const current = () => layout?.controllers.find(c => c.id === selected) || layout?.lights.find(l => l.id === selected);
const controllerFor = (item) => item && (layout.controllers.includes(item) ? item : layout.controllers.find(c => c.id === item.connection?.controller_id));
const infoFor = (controller) => controller && (probes.get(controller.url) || controllerStatus(controller)?.info);
// The service normalizes device URLs (trailing slash, host casing), so match by the stable
// layout id first and only fall back to a normalized-URL comparison.
function controllerStatus(controller) {
  if (!controller) return null;
  const devices = live?.devices || [];
  return devices.find(d => d.id === controller.id)
    || devices.find(d => normalizeUrl(d.url) === normalizeUrl(controller.url));
}
const normalizeUrl = (url) => (url || "").trim().replace(/\/+$/, "").toLowerCase();
const isController = (item) => layout.controllers.includes(item);

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function svg(tag, attributes = {}, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}
function error(message = "") {
  $("w2-error").textContent = message;
  $("w2-error").hidden = !message;
}
function controls() {
  playSources.render(layout, busy || conflict);
  $("w2-save").disabled = !layout || busy || !dirty || conflict;
  $("w2-reload").disabled = busy;
  $("w2-enabled").disabled = !layout || busy;
  const state = busy ? "busy" : conflict ? "conflict" : dirty ? "dirty" : "saved";
  $("w2-save-state").dataset.state = state;
  $("w2-save-state").textContent = { busy: tr("Saving…"), conflict: tr("Reload required"), dirty: tr("Unsaved changes"), saved: tr("Saved") }[state];
  $("w2-save").hidden = $("w2-autosave").checked && !conflict;
  $("w2-test").disabled = busy || !current()?.connection?.controller_id;
  $("w2-rename").disabled = busy || !current();
  $("w2-connect").setAttribute("aria-pressed", String(wiring));
  for (const button of document.querySelectorAll("[data-w2-add], #w2-discover, #w2-connect")) button.disabled = !layout || busy;
  $("w2-inspector-form").inert = busy;
  $("w2-canvas").setAttribute("aria-busy", String(busy));
}
function changed() {
  dirty = true;
  controls();
  scheduleAutosave();
}
// Save shortly after the last edit, but never mid-drag, mid-rename or while a field has focus.
let autosaveTimer = null;
function scheduleAutosave(delay = 1000) {
  clearTimeout(autosaveTimer);
  if (!$("w2-autosave").checked || conflict || !dirty) return;
  autosaveTimer = setTimeout(() => {
    const active = document.activeElement;
    const typing = active && $("w2-inspector-form").contains(active) &&
      active.matches("input:not([type=checkbox]):not([type=radio]):not([type=range]), textarea");
    if (busy || drag || editingName || typing) scheduleAutosave(delay);
    else work(save);
  }, delay);
}
function guide(text) { $("w2-guide").textContent = text; }

async function load() {
  const result = await api("/api/wled2", "GET", undefined, { metadata: true });
  layout = result.data; revision = result.etag;
  wiring = false; wireSource = wirePointer = null;
  defaults ||= await api("/api/wled2/defaults");
  dirty = conflict = false;
  if (selected !== "board" && !current()) selected = "board";
  render();
}
async function save() {
  if (!dirty) return;
  if (conflict) throw new Error(tr("The saved layout changed. Reload before saving your draft."));
  const result = await api("/api/wled2", "PUT", layout, { etag: revision, metadata: true });
  layout = result.data; revision = result.etag; dirty = false;
  render();
}
async function work(action) {
  if (busy) return;
  busy = true; error(); controls();
  try { if (loading) await loading; await action(); }
  catch (cause) { conflict ||= cause.status === 412; error(cause.message); }
  finally { busy = false; controls(); await refresh(); }
}
async function refresh() {
  if (loading || busy || editingName || profileChanging) return;
  loading = (async () => {
    try {
      live = await api("/api/wled2/status");
      if (!layout) await load();
      else if (!dirty && page === "wled2" && !drag) {
        const saved = await api("/api/wled2", "GET", undefined, { metadata: true });
        if (!dirty && !busy && !drag && !editingName && saved.etag !== revision) { layout = saved.data; revision = saved.etag; render(); }
      }
      $("w2-live").textContent = `${phases[live.game.phase] || "Waiting"} · ${live.enabled ? "Following game events" : "Not following games"}`;
      if (page === "wled2" && !drag && !canvas.contains(document.activeElement)) renderCanvas();
      if (live.error) error(live.error);
      updateConnectionStatus();
      updateColourControls();
    } catch (cause) {
      live = null;
      error(cause.message);
    }
    finally { loading = null; controls(); }
  })();
  return loading;
}

function renderBoard() {
  const group = svg("g", { transform: `translate(${layout.board.x} ${layout.board.y})`, "data-object": "board", tabindex: 0, role: "button", "aria-label": tr("Dartboard") });
  group.classList.toggle("w2-selected", selected === "board");
  group.append(svg("circle", { r: 128, fill: "#101216", stroke: "#59616b", "stroke-width": 2 }));
  const numbers = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5];
  const point = (r, angle) => `${Math.cos(angle) * r},${Math.sin(angle) * r}`;
  for (let i = 0; i < 20; i++) {
    const start = (i * 18 - 99) * Math.PI / 180, end = start + Math.PI / 10;
    for (const [outer, inner, colour] of [[108, 0, i % 2 ? "#b8b39d" : "#292d32"], [108, 101, i % 2 ? "#467267" : "#a14d53"], [66, 59, i % 2 ? "#467267" : "#a14d53"]]) {
      group.append(svg("path", { d: `M${point(inner, start)} L${point(outer, start)} A${outer},${outer} 0 0 1 ${point(outer, end)} L${point(inner, end)} A${inner},${inner} 0 0 0 ${point(inner, start)} Z`, fill: colour, stroke: "#52565c", "stroke-width": .5 }));
    }
    const angle = start + Math.PI / 20;
    group.append(svg("text", { x: Math.cos(angle) * 119, y: Math.sin(angle) * 119 + 4, "text-anchor": "middle", class: "w2-small" }, numbers[i]));
  }
  group.append(svg("circle", { r: 12, fill: "#467267" }), svg("circle", { r: 5, fill: "#a14d53" }));
  group.append(svg("circle", { r: 135, class: "w2-selection" }));
  group.append(svg("text", { y: 154, "text-anchor": "middle", class: "w2-small" }, tr("YOUR BOARD")));
  return group;
}
function renderCanvas() {
  if (!layout || editingName) return;
  const defs = svg("defs"), pattern = svg("pattern", { id: "w2-grid", width: 25, height: 25, patternUnits: "userSpaceOnUse" });
  pattern.append(svg("circle", { cx: 1, cy: 1, r: 1, fill: "#ffffff12" })); defs.append(pattern);
  canvas.replaceChildren(defs, svg("rect", { width: 1000, height: 700, fill: "url(#w2-grid)" }));
  for (const light of layout.lights) {
    const controller = controllerFor(light);
    const upstream = layout.lights.find(l => l.id === light.connection?.after_light_id) || controller;
    if (!upstream) continue;
    const a = wireAnchor(upstream), b = wireAnchor(light);
    canvas.append(svg("path", { d: `M${a.x},${a.y} C${a.x},${a.y - 80} ${b.x},${b.y - 80} ${b.x},${b.y}`, class: `w2-wire ${selected === light.id || selected === upstream.id ? "w2-wire-selected" : ""}`, "data-wire": light.id }));
  }
  canvas.append(renderBoard());
  for (const light of layout.lights) {
    const group = svg("g", { transform: `translate(${light.position.x} ${light.position.y}) rotate(${light.rotation})`, "data-object": light.id, tabindex: 0, role: "button", "aria-label": light.name });
    group.classList.toggle("w2-wire-target", wiring && light.id !== wireSource);
    group.classList.toggle("w2-selected", selected === light.id);
    const w = light.width, h = light.height;
    const rules = light.rules || defaults.rules;
    const colour = !light.enabled ? "#52606a" : light.kind === "matrix" ? light.matrix.appearance.color : rules.phases[live?.game.phase || "idle"].color;
    if (light.kind === "ring") {
      group.append(svg("ellipse", { rx: w / 2, ry: h / 2, fill: "none", stroke: "#353d46", "stroke-width": 16 }));
      for (let i = 0; i < 48; i++) {
        const angle = i * Math.PI / 24;
        group.append(svg("circle", { cx: Math.cos(angle) * w / 2, cy: Math.sin(angle) * h / 2, r: 4, fill: colour, class: "w2-led" }));
      }
    } else if (light.kind === "matrix") {
      group.append(svg("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 6, fill: "#242b34", stroke: "#48525d" }));
      // The service reports the frame this panel gets: score glyphs, or its four corner
      // markers while the corner toggle is on. Draw that, with unlit pixels kept dark.
      const face = faceFor(light);
      const cols = light.matrix.width, rows = light.matrix.height;
      for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
        const code = face?.[y]?.[x] || ".";
        group.append(svg("circle", {
          cx: -w / 2 + (x + .5) * w / cols, cy: -h / 2 + (y + .5) * h / rows,
          r: Math.min(4, w / cols * .3, h / rows * .3),
          fill: code === "." ? "#2b343d" : code === "1" ? colour : corners[Number(code) - 2],
          class: "w2-led",
        }));
      }
    } else {
      group.append(svg("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 6, fill: "#242b34", stroke: "#48525d" }));
      const cols = Math.max(2, Math.min(32, Math.floor(w / 13)));
      for (let x = 0; x < cols; x++) {
        group.append(svg("circle", { cx: -w / 2 + (x + .5) * w / cols, cy: 0, r: Math.min(4, w / cols * .3, h * .3), fill: colour, class: "w2-led" }));
      }
    }
    group.append(svg("rect", { x: -w / 2 - 9, y: -h / 2 - 9, width: w + 18, height: h + 18, rx: 10, class: "w2-selection" }));
    if (selected === light.id) resizeHandle(group, light);
    objectControls(group, light);
    group.append(svg("text", { y: h / 2 + 27, "text-anchor": "middle", "data-name": light.id }, light.name));
    group.append(svg("text", { y: h / 2 + 43, "text-anchor": "middle", class: "w2-small" }, light.connection?.controller_id ? tr("Segment {0}{1}", {"0": (light.connection.segment), "1": (light.kind === "matrix" ? ` · ${light.matrix.width} × ${light.matrix.height}` : "")}) : light.connection ? tr("Chain awaiting controller") : tr("Not connected")));
    canvas.append(group);
  }
  for (const controller of layout.controllers) {
    const w = controller.width, h = controller.height;
    const group = svg("g", { transform: `translate(${controller.position.x} ${controller.position.y})`, "data-object": controller.id, tabindex: 0, role: "button", "aria-label": controller.name });
    group.classList.toggle("w2-selected", selected === controller.id);
    group.append(svg("rect", { x: -w / 2, y: -h / 2, width: w, height: h, rx: 10, fill: "#293640", stroke: "#627986", "stroke-width": 2 }));
    const status = controllerStatus(controller);
    const connected = status?.connected || probes.has(controller.url);
    group.append(svg("circle", { cx: -63, cy: -12, r: 4, fill: connected ? "#53c7bd" : "#dba15c" }));
    group.append(svg("text", { x: -51, y: -8, class: "w2-small" }, tr("WLED CONTROLLER")));
    group.append(svg("text", { y: 15, "text-anchor": "middle", "data-name": controller.id }, controller.name.length > 18 ? controller.name.slice(0, 17) + "…" : controller.name));
    group.append(svg("rect", { x: -w / 2 - 8, y: -h / 2 - 10, width: w + 16, height: h + 18, rx: 12, class: "w2-selection" }));
    if (selected === controller.id) resizeHandle(group, controller);
    objectControls(group, controller);
    canvas.append(group);
  }
  drawPendingWire();
  animateSimulation();
}

function resizeHandle(group, item) {
  const handle = svg("g", { transform: `translate(${item.width / 2} ${item.height / 2})`, class: "w2-resize", "data-resize": item.id, tabindex: 0, role: "button", "aria-label": tr("Resize {0}; drag or use arrow keys", {"0": (item.name)}) });
  handle.append(svg("title", {}, tr("Drag to resize")), svg("rect", { x: -14, y: -14, width: 28, height: 28, rx: 6 }));
  handle.append(svg("path", { d: "M-7,-7 L7,7 M-7,-1 V-7 H-1 M1,7 H7 V1", class: "w2-control-icon" }));
  group.append(handle);
}
function objectControls(group, item) {
  const remove = svg("g", { transform: `translate(${item.width / 2} ${-item.height / 2 - 16})`, "data-remove": item.id, class: "w2-remove", tabindex: 0, role: "button", "aria-label": tr("Remove {0}", {"0": (item.name)}) });
  remove.append(svg("title", {}, tr("Remove from canvas")), svg("circle", { r: 12 }), svg("path", { d: "M-4,-4 L4,4 M4,-4 L-4,4", class: "w2-control-icon" }));
  const port = svg("g", { transform: `translate(0 ${-item.height / 2 - 5})`, "data-port": item.id, class: "w2-port", tabindex: 0, role: "button", "aria-label": tr("Draw wire from {0}", {"0": (item.name)}) });
  port.append(svg("title", {}, tr("Hold and drag to a light, then release to connect")), svg("circle", { r: 12, class: "w2-port-hit" }), svg("circle", { r: 7 }));
  group.append(remove, port);
}
function wireAnchor(item) {
  const angle = (item.rotation || 0) * Math.PI / 180, offset = -item.height / 2 - 5;
  return { x: item.position.x - offset * Math.sin(angle), y: item.position.y + offset * Math.cos(angle) };
}
function drawPendingWire() {
  canvas.querySelector(".w2-pending-wire")?.remove();
  const source = [...layout.controllers, ...layout.lights].find(item => item.id === wireSource);
  if (!wiring || !source) return;
  const a = wireAnchor(source), b = wirePointer || { x: a.x, y: a.y - 60 };
  canvas.append(svg("path", { d: `M${a.x},${a.y} C${a.x},${a.y - 80} ${b.x},${b.y + 80} ${b.x},${b.y}`, class: "w2-pending-wire" }));
}
function beginWire(id, event = null) {
  if (busy) return;
  const item = [...layout.controllers, ...layout.lights].find(item => item.id === id);
  if (!item) return;
  cancelWire(); error(); wiring = true; wireSource = selected = id;
  if (event) {
    wireDrag = event.pointerId;
    wirePointer = new DOMPoint(event.clientX, event.clientY).matrixTransform(canvas.getScreenCTM().inverse());
  }
  guide(event ? tr("Keep holding, drag to a light, and release to attach. Release elsewhere to cancel.") : tr("Focus a light and press Enter to attach. Escape cancels."));
  render();
  if (event) canvas.setPointerCapture(event.pointerId);
  canvas.focus();
}
function cancelWire() {
  const pointer = wireDrag;
  wiring = false; wireSource = wirePointer = wireDrag = null;
  if (pointer !== null && canvas.hasPointerCapture(pointer)) canvas.releasePointerCapture(pointer);
  controls(); renderCanvas();
}
function focusConnector(id = selected) {
  cancelWire();
  canvas.querySelector(`[data-port="${id}"]`)?.focus();
  guide(tr("Hold a round connector, drag to a light, and release to attach."));
}
function finishWire(event) {
  if (wireDrag === null || event.pointerId !== wireDrag) return;
  if (event.type === "pointerup" && !busy) {
    const hit = document.elementFromPoint(event.clientX, event.clientY);
    const id = canvas.contains(hit) && !hit.closest("[data-remove], [data-resize]") ? hit.closest("[data-object]")?.dataset.object : null;
    if (id && id !== wireSource && layout.lights.some(light => light.id === id)) select(id);
  }
  if (wiring) { cancelWire(); guide(tr("Wire discarded. Hold a connector to try again.")); }
}
function removeItem(id) {
  if (busy) return;
  const item = [...layout.controllers, ...layout.lights].find(item => item.id === id);
  if (!item) return;
  if (!confirm(tr("Remove \"{0}\" from this layout?{1}", {"0": (item.name), "1": (isController(item) ? " Its lights will be kept but disconnected." : "")}))) return;
  if (isController(item)) {
    layout.controllers = layout.controllers.filter(c => c.id !== id);
    layout.lights.forEach(l => { if (l.connection?.controller_id === id) l.connection = null; });
  } else { detachChildren(id); layout.lights = layout.lights.filter(l => l.id !== id); }
  cancelWire(); selected = "board"; changed(); render();
}

function renameSelected() {
  if (!current() || busy) return;
  const input = $("w2-properties").querySelector('[aria-label="Name"]');
  input?.focus(); input?.select();
}
function renameInline(id) {
  if (busy || wiring || editingName) return;
  const item = [...layout.controllers, ...layout.lights].find(item => item.id === id);
  if (!item) return;
  selected = id; render();
  const group = canvas.querySelector(`[data-object="${id}"]`);
  const label = group.querySelector("[data-name]");
  const width = Math.max(180, Math.min(360, item.width));
  const editor = svg("foreignObject", { x: -width / 2, y: Number(label.getAttribute("y")) - 24, width, height: 36, "data-name-editor": id });
  const input = document.createElementNS("http://www.w3.org/1999/xhtml", "input");
  input.className = "w2-name-input"; input.type = "text"; input.maxLength = 80;
  input.value = item.name; input.setAttribute("aria-label", tr("Item name"));
  editingName = id;
  let finished = false;
  const finish = (accept) => {
    if (finished) return;
    finished = true; editingName = null;
    const name = input.value.trim();
    if (accept && name && name !== item.name) { item.name = name; changed(); }
    if (accept && !name) error(tr("The name cannot be empty. The previous name was kept."));
    render();
  };
  input.addEventListener("keydown", event => {
    event.stopPropagation();
    if (event.key === "Enter" || event.key === "Escape") {
      event.preventDefault(); finish(event.key === "Enter");
      canvas.querySelector(`[data-object="${id}"]`)?.focus();
    }
  });
  input.addEventListener("blur", () => finish(true));
  label.setAttribute("visibility", "hidden");
  editor.append(input); group.append(editor); input.focus(); input.select();
}
function detachChildren(id) {
  for (const light of layout.lights) if (light.connection?.after_light_id === id) light.connection.after_light_id = null;
}

function chain(light, upstream) {
  if (!upstream) return false;
  let ancestor = upstream;
  while (ancestor) {
    if (ancestor.id === light.id) { error(tr("A wire cannot loop back to the same light.")); return false; }
    ancestor = layout.lights.find(l => l.id === ancestor.connection?.after_light_id);
  }
  upstream.connection ||= { controller_id: null, after_light_id: null, segment: 0, whole_segment: false, start: 0, count: 60 };
  const wire = clone(upstream.connection);
  wire.after_light_id = upstream.id;
  const geometry = infoFor(controllerFor(upstream))?.segments.find(s => s.id === wire.segment);
  let note = "";
  if (geometry?.matrix) {
    if (light.kind !== "matrix" || upstream.kind !== "matrix") { error(tr("A WLED 2D segment can only chain matrix panels. Use a linear segment for strips.")); return false; }
    const fit = fitPanel(light, upstream, geometry.matrix);
    if (!fit) { error(tr("There is no room after this panel in the WLED segment. Reduce the panel sizes.")); return false; }
    if (fit !== "plain") note = ` WLED's ${geometry.matrix.width}×${geometry.matrix.height} segment now holds two panels.`;
  } else {
    const count = upstream.kind === "matrix" ? upstream.matrix.width * upstream.matrix.height : wire.count;
    wire.whole_segment = false; wire.start += count;
    wire.count = light.connection?.count || 60;
    if (geometry && wire.start + (light.kind === "matrix" ? light.matrix.width * light.matrix.height : wire.count) > geometry.length) {
      error(tr("The chained lights exceed this segment. Adjust LED counts or enlarge the segment in WLED.")); return false;
    }
    if (upstream.kind !== "matrix") upstream.connection.whole_segment = false;
    light.matrix.x = light.matrix.y = 0;
  }
  detachChildren(light.id);
  light.connection = wire;
  if (light.kind === "matrix" && upstream.kind === "matrix") applyScoreDefaults(light, upstream);
  guide(`${light.name} wired after ${upstream.name}.${note} Check its LED range or panel area, then save.`);
  return true;
}

// Two chained matrix panels are the classic my/opponent scoreboard: the first shows my
// remaining score in green, the second shows the opponent's in red. A panel that already
// has an explicit source (or a custom colour) is left untouched.
function applyScoreDefaults(light, upstream) {
  if (!upstream.matrix.source || upstream.matrix.source === "remaining") {
    upstream.matrix.source = "local_remaining";
    upstream.matrix.appearance.color = "#00ff00";
    if (/^(score panel|matrix.*)$/i.test(upstream.name)) upstream.name = tr("My score");
  }
  if (!light.matrix.source || light.matrix.source === "remaining") {
    light.matrix.source = "opponent_remaining";
    light.matrix.appearance.color = "#ff0000";
    if (/^(score panel|matrix.*)$/i.test(light.name)) light.name = tr("Opponent score");
  }
}

function attachChain(light, source) {
  const previous = clone(layout), edges = [], queue = [light.id];
  for (let i = 0; i < queue.length; i++) {
    for (const child of layout.lights.filter(l => l.connection?.after_light_id === queue[i])) {
      edges.push([child.id, queue[i]]); queue.push(child.id);
    }
  }
  const restore = () => { layout = previous; render(); return false; };
  // Reserve the chain's existing areas while choosing a new controller output.
  if (!source || isController(source)) {
    for (const [id] of edges) layout.lights.find(l => l.id === id).connection.controller_id = null;
    connect(light, source);
  } else if (!chain(light, source)) return restore();
  for (const [id, parent] of edges) {
    if (!chain(layout.lights.find(l => l.id === id), layout.lights.find(l => l.id === parent))) return restore();
  }
  for (const id of queue) {
    const item = layout.lights.find(l => l.id === id);
    if ((previous.lights.find(l => l.id === id).enabled && !item.enabled) || overlapsLight(item)) {
      error(tr("The chain does not fit a free LED range or panel area. Adjust its LED counts or choose another segment.")); return restore();
    }
  }
  return true;
}

function field(parent, title, value, onChange, options = {}) {
  const label = element("label", title);
  const input = element(options.choices ? "select" : "input");
  if (options.choices) {
    for (const [key, text] of Object.entries(options.choices)) input.add(new Option(text, key));
  } else input.type = options.type || "text";
  if (input.type === "checkbox") input.checked = value;
  else input.value = value ?? "";
  for (const key of ["min", "max", "step", "placeholder", "maxLength"]) if (options[key] !== undefined) input[key] = options[key];
  input.setAttribute("aria-label", title);
  input.addEventListener(options.choices || input.type === "checkbox" ? "change" : "input", () => {
    const next = input.type === "checkbox" ? input.checked : ["number", "range"].includes(input.type) ? Number(input.value) : input.value;
    onChange(next); changed(); renderCanvas();
  });
  label.append(input); parent.append(label);
  return input;
}
function button(parent, title, action, className) {
  const node = element("button", title, className); node.type = "button";
  node.addEventListener("click", action); parent.append(node); return node;
}
// A section header keeps its title and one action on the same line.
function section(parent, title, action) {
  const node = element("section", undefined, "w2-section");
  const heading = element("div", undefined, "w2-heading");
  heading.append(element("h3", title));
  if (action) button(heading, action.label, action.onClick);
  node.append(heading); parent.append(node); return node;
}
// One folded card per group of fields. Cards start folded and remember being opened.
const expanded = {};
function details(parent, key, title, className = "w2-section w2-collapse") {
  const node = element("details", undefined, className);
  node.open = expanded[key] === true;
  node.addEventListener("toggle", () => expanded[key] = node.open);
  node.append(element("summary", title)); parent.append(node);
  return node;
}
function number(parent, title, object, key, min, max) {
  return field(parent, title, object[key], v => object[key] = v, { type: "number", min, max, step: 1 });
}
function effectOptions(controller, value) {
  const result = { 0: tr("Solid") };
  const effects = infoFor(controller)?.effects || [];
  effects.forEach((name, index) => { if (name !== "RSVD" && name !== "-") result[index] = name; });
  if (!Object.hasOwn(result, value)) result[value] = tr("Effect {0} · check connection for names", {"0": (value)});
  return result;
}
function updateColourControls() {
  for (const input of document.querySelectorAll("[data-w2-colour-effect]")) {
    const controller = layout?.controllers.find(item => item.id === input.dataset.w2ColourController);
    const slots = infoFor(controller)?.effect_colors?.[Number(input.dataset.w2ColourEffect)];
    input.parentElement.hidden = Boolean(slots && !slots[0]);
  }
}
function appearance(parent, style, controller, native) {
  const grid = element("div", undefined, "w2-fields"); parent.append(grid);
  const colour = field(grid, tr("Colour"), style.color, v => style.color = v, { type: "color" });
  number(grid, tr("Brightness (0-255)"), style, "brightness", 0, 255);
  if (native) {
    colour.dataset.w2ColourController = controller?.id || "";
    const update = () => {
      colour.dataset.w2ColourEffect = String(style.effect);
      const slots = infoFor(controller)?.effect_colors?.[style.effect];
      colour.parentElement.hidden = Boolean(slots && !slots[0]);
    };
    field(parent, tr("Animation"), style.effect, v => { style.effect = Number(v); update(); },
      { choices: effectOptions(controller, style.effect) });
    update();
  }
}
// A new light starts with the standard rules; a matrix panel starts silent, because its
// event flashes are opted into one by one.
function newRules(kind) {
  const rules = clone(defaults.rules);
  if (kind === "matrix") for (const style of Object.values(rules.effects)) style.enabled = false;
  return rules;
}

function renderRules(parent, item) {
  const controller = controllerFor(item);
  const native = item.kind !== "matrix" && item.connection?.whole_segment !== false;
  // Folded like Size & position: the rules are set once, then left alone.
  const area = details(parent, "rules", tr("Game rules"));
  const rules = item.rules;
  if (item.kind !== "matrix") {
    for (const [key, title] of Object.entries(phases)) {
      const card = element("details", undefined, "w2-rule");
      card.append(element("summary", title)); appearance(card, rules.phases[key], controller, native);
      area.append(card);
    }
  }
  area.append(element("p", tr("Events temporarily replace the phase colour. Matrix events change the score's colour; native animations apply to whole segments."), "hint"));
  const rulesList = Object.entries(rules.effects);
  if (rulesList.length) {
    // One switch for the whole list, so a layout can be silenced or armed in one click.
    field(area, tr("Allow all event effects"), rulesList.every(([, style]) => style.enabled !== false), value => {
      for (const [, style] of rulesList) style.enabled = value;
      renderInspector();
    }, { type: "checkbox" });
  }
  for (const [key, style] of rulesList) {
    const card = element("details", undefined, "w2-rule");
    // The switch sits in the summary, so a rule can be turned on or off without opening it.
    const heading = element("summary");
    const enabled = element("input");
    enabled.type = "checkbox"; enabled.checked = style.enabled !== false;
    enabled.setAttribute("aria-label", `${events[key] || key} effect`);
    enabled.addEventListener("click", event => event.stopPropagation());
    enabled.addEventListener("change", () => { style.enabled = enabled.checked; changed(); renderCanvas(); });
    heading.append(enabled, events[key] || key);
    card.append(heading);
    appearance(card, style, controller, native);
    field(card, tr("Duration (seconds)"), style.duration, v => style.duration = v, { type: "number", min: .1, max: 30, step: .1 });
    const actions = element("div", undefined, "actions"); card.append(actions);
    button(actions, tr("Test event"), () => testLight(key));
    button(actions, tr("Remove rule"), () => { delete rules.effects[key]; changed(); renderInspector(); });
    area.append(card);
  }
  const remaining = Object.fromEntries(Object.entries(events).filter(([key]) => !rules.effects[key]));
  if (Object.keys(remaining).length) {
    const select = element("select"); select.setAttribute("aria-label", tr("Event to add"));
    for (const [key, name] of Object.entries(remaining)) select.add(new Option(name, key));
    area.append(select);
    button(area, "+ Add event rule", () => {
      const style = clone(defaults.rules.effects[select.value] || { color: "#ffffff", brightness: 128, effect: 0, duration: 2 });
      // A new rule on a matrix panel starts off, so nothing flashes until it is switched on.
      if (item.kind === "matrix") style.enabled = false;
      rules.effects[select.value] = style;
      changed(); renderInspector();
    });
  }
}

function segmentChoices(controller) {
  return Object.fromEntries((infoFor(controller)?.segments || []).map(segment => {
    const pins = (infoFor(controller)?.outputs || []).filter(o => !segment.matrix && o.start < segment.stop && o.stop > segment.start).flatMap(o => o.pins || []);
    return [segment.id, `${segment.name || "Segment " + segment.id}${pins.length ? " · GPIO " + pins.join(" / ") : ""}${segment.matrix ? ` · ${segment.matrix.width}×${segment.matrix.height}` : ` · ${segment.length} LEDs`}`];
  }));
}
const segmentGeometry = (controller, id) => (infoFor(controller)?.segments || []).find(segment => segment.id === Number(id ?? 0));

// Corner colours the service also sends to a real panel, in reading order.
const corners = ["#00ff00", "#ff0000", "#0000ff", "#ffffff"];

// Palette codes the service sends per panel pixel: 1 is the panel's own colour, 2-5 are the
// corner markers, "." is dark. An unsaved panel has no frame yet, so it stays dark.
const faceFor = (light) => simulation
  ? simulationFace(light.matrix, light.matrix.source === "player_remaining"
    ? (light.matrix.player_slot === 1 ? simulation.local_remaining : simulation.opponent_remaining)
    : simulation[light.matrix.source] ?? simulation.remaining)
  : controllerStatus(controllerFor(light))?.faces?.[light.id];

// A schematic of the panel: the four corner markers show which pixel is which, so the
// rotation and row wiring can be compared with the physical panel.
function matrixDiagram(item, canvas) {
  const matrix = item.matrix, columns = Math.min(matrix.width, 16), rows = Math.min(matrix.height, 16);
  const cell = Math.max(4, Math.min(10, Math.floor(128 / columns), Math.floor(80 / rows)));
  const pad = 10, width = columns * cell, height = rows * cell;
  const box = element("div", undefined, "w2-orientation");
  const node = svg("svg", { viewBox: `0 0 ${width + pad * 2} ${height + pad * 2}`, class: "w2-diagram", role: "img",
    "aria-label": tr("Panel {0} by {1}. Green is the first pixel, red the top-right, blue the bottom-left and white the bottom-right.", {"0": (matrix.width), "1": (matrix.height)}) });
  node.append(svg("rect", { x: pad - 5, y: pad - 5, width: width + 10, height: height + 10, rx: 6, class: "w2-diagram-frame" }));
  const markers = new Map([[`0:0`, 0], [`${columns - 1}:0`, 1], [`0:${rows - 1}`, 2], [`${columns - 1}:${rows - 1}`, 3]]);
  for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
    const marker = markers.get(`${x}:${y}`);
    node.append(svg("rect", { x: pad + x * cell, y: pad + y * cell, width: Math.max(2, cell - 1), height: Math.max(2, cell - 1), rx: 1.5, fill: marker === undefined ? "#2b343d" : corners[marker], class: marker === undefined ? "w2-diagram-cell" : "w2-diagram-corner" }));
  }
  box.append(node);
  const wiring = canvas ? tr("WLED runs this 2D canvas row by row.") : matrix.serpentine ? tr("Rows run in alternating directions.") : tr("Every row runs left to right.");
  box.append(element("p", tr("Green first pixel · red top-right · blue bottom-left · white bottom-right. {0}{1}", {"0": (wiring), "1": (matrix.rotation ? tr("Digits are turned {0}°.", {"0": (matrix.rotation)}) : "")}), "hint"));
  return box;
}

function connect(light, controller) {
  detachChildren(light.id);
  if (!controller) { light.connection = null; return; }
  const siblings = layout.lights.filter(l => l.id !== light.id && l.enabled && l.connection?.controller_id === controller.id);
  const segments = infoFor(controller)?.segments || [];
  const fitsKind = s => light.kind === "matrix" ? s.matrix || s.length >= 80 : !s.matrix;
  let segment = segments.find(s => fitsKind(s) && !siblings.some(l => l.connection.segment === s.id));
  segment ||= segments.find(fitsKind) || segments[0];
  light.connection = { controller_id: controller.id, segment: segment?.id || 0, whole_segment: true, start: 0, count: light.connection?.count || 60 };
  if (light.kind === "matrix" && segment?.matrix) {
    light.matrix.width = Math.min(16, segment.matrix.width);
    light.matrix.height = Math.min(16, segment.matrix.height);
    let free = null;
    for (let y = 0; y + light.matrix.height <= segment.matrix.height && !free; y += light.matrix.height) {
      for (let x = 0; x + light.matrix.width <= segment.matrix.width; x += light.matrix.width) {
        const overlaps = siblings.some(l => l.connection.segment === segment.id && (l.kind !== "matrix" || (x < l.matrix.x + l.matrix.width && x + light.matrix.width > l.matrix.x && y < l.matrix.y + l.matrix.height && y + light.matrix.height > l.matrix.y)));
        if (!overlaps) { free = { x, y }; break; }
      }
    }
    if (free) Object.assign(light.matrix, free);
    else light.enabled = false;
  } else {
    if (light.kind === "matrix" && segment?.length >= 80) {
      light.matrix.width = 16;
      light.matrix.height = Math.min(16, Math.floor(segment.length / 16));
      light.matrix.x = light.matrix.y = 0;
    }
    if (siblings.some(l => l.connection.segment === light.connection.segment)) light.enabled = false;
  }
  guide(light.enabled ? `${light.name} connected to ${controller.name}. Save and test it.` : tr("This segment is already used. The new light is disabled until you choose a free segment or pixel range."));
}

function renderInspector() {
  if (!layout) return;
  const parent = $("w2-properties"); parent.replaceChildren();
  const item = current();
  $("w2-object-title").textContent = item?.name || tr("Your board");
  $("w2-object-kind").textContent = item ? isController(item) ? tr("Controller") : item.kind : tr("Layout reference");
  if (!item) {
    parent.append(element("p", tr("Arrange your lights as they look around the board. Add a controller and enter its address; the service checks it directly and lists its outputs."), "hint"));
    const steps = element("ol");
    [tr("Add a controller and enter its address."), tr("Add a strip, ring or matrix."), tr("Connect a wire from the controller to the light."), tr("Save the layout and test the light."), tr("Give each light its game rules, then turn on Follow game events.")].forEach(text => steps.append(element("li", text)));
    parent.append(steps);
    parent.append(element("p", tr("List and Visual edit the same lights. Game rules are saved to your active app profile."), "hint"));
    const grid = element("div", undefined, "w2-fields"); parent.append(grid);
    number(grid, tr("Board X"), layout.board, "x", 0, 1000); number(grid, tr("Board Y"), layout.board, "y", 0, 700);
    return;
  }
  field(parent, tr("Name"), item.name, v => {
    item.name = v; $("w2-object-title").textContent = v;
    const entry = $("w2-objects").querySelector(`[data-id="${item.id}"]`);
    if (entry) entry.textContent = v;
  }, { maxLength: 80 });
  // Visual size and placement share one folded card: both only change the diagram.
  const shape = details(parent, "shape", tr("Size & position"));
  const dimensions = element("div", undefined, "w2-fields"); shape.append(dimensions);
  number(dimensions, tr("Visual width"), item, "width", isController(item) ? 80 : 20, 800);
  number(dimensions, tr("Visual height"), item, "height", isController(item) ? 40 : 12, 600);
  number(dimensions, tr("X position"), item.position, "x", 0, 1000);
  number(dimensions, tr("Y position"), item.position, "y", 0, 700);
  if (!isController(item)) number(dimensions, tr("Visual rotation"), item, "rotation", -180, 180);
  shape.append(element("p", tr("Drag the corner handle to resize, or use arrow keys. Visual size and rotation only change this diagram: they never move LEDs or matrix pixels."), "hint"));
  field(parent, tr("Enabled"), item.enabled, v => item.enabled = v, { type: "checkbox" });
  if (isController(item)) {
    field(parent, tr("Idle timeout (minutes, 0 disables)"), item.idle_timeout_minutes || 0,
      value => item.idle_timeout_minutes = value || null, { type: "number", min: 0, max: 1440 });
    field(parent, tr("Controller address"), item.url, v => item.url = v.trim(), { type: "url", placeholder: "http://192.168.1.50" });
    const status = element("p", tr("Checking this controller…"), "w2-state"); status.id = "w2-controller-status"; parent.append(status);
    const actions = element("div", undefined, "actions"); parent.append(actions);
    button(actions, tr("Check connection"), () => work(async () => {
      const info = await api("/api/wled2/probe", "POST", { url: item.url });
      probes.set(item.url, info); renderInspector(); notice(tr("Connected to {0}. {1} segments available.", {"0": (item.name), "1": (info.segments.length)}));
    }));
    for (const on of [true, false]) button(actions, on ? tr("Lights on") : tr("Lights off"), () => work(async () => {
      await save(); const result = await api(`/api/wled2/controllers/${item.id}/power`, "POST", { on });
      if (!result.applied) throw new Error(result.devices.find(d => d.error)?.error || tr("Could not change controller power."));
    }));
    const segments = segmentChoices(item);
    const area = section(parent, tr("Segments"), { label: tr("Connect a wire from here"), onClick: () => focusConnector(item.id) });
    if (Object.keys(segments).length) {
      const list = element("ul", undefined, "hint");
      Object.values(segments).forEach(text => list.append(element("li", text))); area.append(list);
    } else {
      area.append(element("p", tr("Check this controller to list its segments."), "hint"));
    }
    parent.append(element("p", tr("A controller can drive a strip and a matrix panel at the same time, so every light keeps its own game rules."), "hint"));
  } else {
    field(parent, tr("Light shape"), item.kind, value => {
      item.kind = value;
      item.width = value === "strip" ? 240 : 160;
      item.height = value === "strip" ? 24 : 160;
      renderInspector();
    }, { choices: lightShapeNames() });
    const area = section(parent, tr("Connection"), { label: tr("Connect a wire from here"), onClick: () => focusConnector(item.id) });
    const choices = { "": tr("Not connected"), ...Object.fromEntries(layout.controllers.map(c => [c.id, c.name])) };
    field(area, tr("Controller"), item.connection?.controller_id || "", value => {
      attachChain(item, layout.controllers.find(c => c.id === value)); renderInspector();
    }, { choices });
    if (item.connection) {
      const upstream = layout.lights.find(l => l.id === item.connection.after_light_id);
      if (upstream) {
        area.append(element("p", tr("Wired after {0}", {"0": (upstream.name)}), "hint"));
        button(area, tr("Wire directly to controller"), () => { item.connection.after_light_id = null; changed(); render(); });
      }
      const controller = controllerFor(item), choices = segmentChoices(controller);
      const geometry = segmentGeometry(controller, item.connection.segment);
      if (Object.keys(choices).length) {
        if (!Object.hasOwn(choices, item.connection.segment)) choices[item.connection.segment] = tr("Segment {0} · unavailable", {"0": (item.connection.segment)});
        field(area, tr("Output / segment"), item.connection.segment, v => { detachChildren(item.id); item.connection.after_light_id = null; item.connection.segment = Number(v); renderInspector(); }, { choices });
      } else {
        field(area, tr("WLED segment ID"), item.connection.segment, v => {
          detachChildren(item.id); item.connection.after_light_id = null; item.connection.segment = v;
        }, { type: "number", min: 0, max: 255, step: 1 });
        area.append(element("p", tr("The service checks each controller and lists its segments. A segment selects the LEDs; the wire on this canvas shows that assignment."), "hint"));
      }
      // A whole segment already defines how many LEDs the light owns, so the range fields
      // only appear for pixel ranges and chained lights.
      if (item.kind === "matrix") {
        if (!geometry?.matrix) number(area, tr("First LED on the segment"), item.connection, "start", 0, 65535);
      } else {
        field(area, tr("Use the whole segment"), item.connection.whole_segment, v => { item.connection.whole_segment = v; renderInspector(); }, { type: "checkbox" });
        if (item.connection.whole_segment) {
          area.append(element("p", geometry ? tr("Whole segment: {0} LEDs from {1}, as WLED reports them.", {"0": (geometry.length), "1": (geometry.name || "segment " + geometry.id)}) : tr("Whole segment, using the LED count WLED reports for it."), "hint"));
        } else {
          number(area, tr("First LED"), item.connection, "start", 0, 65535);
          number(area, tr("LED count"), item.connection, "count", 1, 1024);
          area.append(element("p", tr("Pixel ranges address LEDs inside one segment. Give every light that shares a chained segment its own range."), "hint"));
        }
      }
    }
    if (item.kind === "matrix") {
      const area = section(parent, tr("Score panel"));
      field(area, tr("Display"), item.matrix.source, v => { item.matrix.source = v; renderInspector(); }, { choices: sources });
      if (item.matrix.source === "player_remaining") number(area, tr("Player slot (match order)"), item.matrix, "player_slot", 1, 10);
      const canvas = segmentGeometry(controllerFor(item), item.connection?.segment)?.matrix;
      appearance(area, item.matrix.appearance, controllerFor(item), false);
      // Panel geometry is set once, so it lives in its own fold below the live controls. The
      // corner markers belong to it: they are what those settings are checked against.
      const geometry = details(area, "geometry", tr("Panel geometry"), "w2-fold");
      const grid = element("div", undefined, "w2-fields"); geometry.append(grid);
      number(grid, tr("Panel width"), item.matrix, "width", 5, canvas ? Math.max(5, canvas.width) : 64);
      number(grid, tr("Panel height"), item.matrix, "height", 5, canvas ? Math.max(5, canvas.height) : 64);
      if (canvas) {
        // Panel X and Y place this rectangle inside WLED's own 2D canvas.
        number(grid, tr("Panel X"), item.matrix, "x", 0, Math.max(0, canvas.width - 1));
        number(grid, tr("Panel Y"), item.matrix, "y", 0, Math.max(0, canvas.height - 1));
      }
      field(geometry, tr("Digit rotation"), item.matrix.rotation, v => item.matrix.rotation = Number(v), { choices: { 0: "0°", 90: "90°", 180: "180°", 270: "270°" } });
      if (!canvas) field(geometry, tr("Serpentine rows"), item.matrix.serpentine, v => item.matrix.serpentine = v, { type: "checkbox" });
      geometry.append(element("p", canvas
        ? tr("WLED 2D segment {0}×{1}: Panel X and Y place this panel inside it, and WLED keeps the segment's own physical wiring.", {"0": (canvas.width), "1": (canvas.height)})
        : tr("A linear segment renders this panel row by row. Match the rotation and the row wiring to your physical panel."), "hint"));
      // Corner colours stay lit while this is on, so the panel can be compared with the
      // diagram while the geometry above is adjusted.
      const cornerLabel = element("label");
      const cornerBox = element("input");
      cornerBox.type = "checkbox"; cornerBox.checked = cornersShown(item);
      cornerBox.disabled = busy || !item.connection?.controller_id;
      cornerBox.addEventListener("change", () => toggleCorners(item, cornerBox.checked));
      cornerLabel.append(cornerBox, element("span", tr("Show corner colours")));
      geometry.append(cornerLabel, matrixDiagram(item, !!canvas));
    }
    renderRules(parent, item);
    const players = details(parent, "players", tr("Player colours"));
    item.rules.players ||= {};
    for (let slot = 1; slot <= 10; slot++) {
      const area = details(players, `player-${slot}`, tr("Player {0}", { 0: slot }), "w2-rule");
      const style = item.rules.players[slot];
      field(area, tr("Enabled"), !!style, enabled => {
        if (enabled) item.rules.players[slot] = { color: "#ffffff", brightness: 128, effect: 0, name_filter: "" };
        else delete item.rules.players[slot];
        renderInspector();
      }, { type: "checkbox" });
      if (style) {
        field(area, tr("Player name filter"), style.name_filter, value => style.name_filter = value, { maxLength: 80 });
        appearance(area, style, controllerFor(item), item.kind !== "matrix" && item.connection?.whole_segment !== false);
      }
    }
  }
  const actions = section(parent, tr("Remove object"));
  button(actions, tr("Remove from layout"), () => removeItem(item.id), "danger");
  updateConnectionStatus();
}
function updateConnectionStatus() {
  const node = $("w2-controller-status"), item = current();
  if (!node || !item) return;
  const status = controllerStatus(item);
  const probe = probes.get(item.url);
  if (status?.error) node.textContent = tr(status.error);
  else if (status?.connected || probe) node.textContent = tr("Connected · WLED {0}", {"0": (status?.info?.version || probe?.version || "")});
  // The service checks every saved controller directly, so a saved address has a result soon.
  else if (status) node.textContent = tr("Checking {0}…", {"0": (item.url)});
  else node.textContent = tr("Not checked yet. Save the layout so the service checks this address, or check it now.");
}
function render() {
  if (!layout) return;
  $("w2-enabled").checked = layout.enabled;
  renderCanvas(); renderInspector();
  const list = $("w2-objects"); list.replaceChildren();
  for (const item of [{ id: "board", name: tr("Board") }, ...layout.controllers, ...layout.lights]) {
    const node = button(list, item.name, () => select(item.id)); node.dataset.id = item.id; node.setAttribute("aria-pressed", String(item.id === selected));
  }
  controls();
}
function select(id) {
  if (busy) return;
  if (wiring) {
    if (layout.controllers.some(c => c.id === id)) { wireSource = id; guide(tr("Now select the light to connect.")); }
    else if (wireSource && layout.lights.some(l => l.id === id)) {
      const light = layout.lights.find(l => l.id === id), controller = layout.controllers.find(c => c.id === wireSource);
      if (controller) {
        const segments = infoFor(controller)?.segments;
        if (segments && !segments.some(s => light.kind === "matrix" ? s.matrix || s.length >= 80 : !s.matrix)) {
          error(tr("This controller has no compatible segment for that light.")); return;
        }
      }
      if (!attachChain(light, controller || layout.lights.find(l => l.id === wireSource))) return;
      cancelWire(); error(); changed(); guide(tr("Wire attached. Drag from a light's round connector to extend the chain."));
    } else if (layout.lights.some(l => l.id === id)) { wireSource = id; guide(tr("Select the next light in the chain.")); }
    else guide(tr("Select a controller or light, then the next light."));
  }
  selected = id; render();
}
function overlapsLight(light) {
  if (!light.enabled || !light.connection?.controller_id) return false;
  const a = light.connection;
  return layout.lights.some(other => {
    const b = other.connection;
    if (other.id === light.id || !other.enabled || !b || a.controller_id !== b.controller_id || a.segment !== b.segment) return false;
    if ((light.kind !== "matrix" && a.whole_segment) || (other.kind !== "matrix" && b.whole_segment)) return true;
    if (light.kind === "matrix" && other.kind === "matrix" && a.start === 0 && b.start === 0) {
      const x = light.matrix, y = other.matrix;
      return x.x < y.x + y.width && x.x + x.width > y.x && x.y < y.y + y.height && x.y + x.height > y.y;
    }
    const count = item => item.kind === "matrix" ? item.matrix.width * item.matrix.height : item.connection.count;
    return a.start < b.start + count(other) && a.start + count(light) > b.start;
  });
}
function add(kind, name, url = "") {
  if (!layout || busy) return;
  if (kind === "controller") {
    if (layout.controllers.length >= 8) { error(tr("Keep at most eight controllers.")); return; }
    const item = { id: uid("controller"), name: name || tr("New controller"), url, enabled: true, width: 160, height: 60, position: { x: 150 + layout.controllers.length * 180 % 700, y: 580 } };
    layout.controllers.push(item); selected = item.id;
  } else {
    if (layout.lights.length >= 128) { error(tr("Keep at most 128 lights.")); return; }
    const item = { id: uid("light"), name: name || ({strip:tr("LED strip"),ring:tr("Board ring"),matrix:tr("Score panel")}[kind]), kind, position: { x: kind === "ring" ? layout.board.x : 500 + layout.lights.length * 30 % 250, y: kind === "ring" ? layout.board.y : 110 }, width: kind === "ring" ? 310 : kind === "matrix" ? 150 : 260, height: kind === "ring" ? 310 : kind === "matrix" ? 150 : 24, rotation: 0, enabled: true, connection: null, rules: newRules(kind), matrix: clone(defaults.matrix) };
    layout.lights.push(item); selected = item.id;
  }
  changed(); render();
}
function testLight(event) {
  const item = current();
  if (!item?.connection?.controller_id) { error(tr("Connect a light to a controller first.")); return; }
  work(async () => {
    await save();
    await api("/api/wled2/preview", "POST", { target_id: item.id, phase: $("w2-test-phase").value, event: event || $("w2-event").value || null, value: 180, duration: 3 });
    notice(`${item.name} test complete. Previous settings restored.`);
  });
}

// The service keeps the corners lit until the toggle goes off, so the panel can be checked
// at leisure. The state lives in the service and comes back with the device status.
const cornersShown = (item) => (controllerStatus(controllerFor(item))?.corners || []).includes(item.id);

function toggleCorners(item, on) {
  work(async () => {
    await save();
    await api(`/api/wled2/controllers/${item.connection.controller_id}/corners`, "POST", { target_id: item.id, on });
    notice(on ? `${item.name} keeps its corner colours until you switch them off.` : `${item.name} shows its score again.`);
  }).finally(() => { if (current() === item) renderInspector(); });
}

canvas.addEventListener("pointerdown", event => {
  if (!layout || busy || event.button !== 0) return;
  if (event.target.closest("[data-name-editor]")) return;
  if (!wiring && event.target.closest("[data-name]")) return;
  const remove = event.target.closest("[data-remove]");
  if (remove) { event.preventDefault(); removeItem(remove.dataset.remove); return; }
  const port = event.target.closest("[data-port]");
  if (wireDrag !== null) return;
  if (port) { event.preventDefault(); beginWire(port.dataset.port, event); return; }
  const target = event.target.closest("[data-object], [data-wire]");
  if (!target) return;
  const id = target.dataset.object || target.dataset.wire;
  if (wiring || target.dataset.wire) { select(id); return; }
  if (id !== "board" && !event.target.closest("[data-resize]") && lastTap?.id === id && event.timeStamp - lastTap.time < 350) {
    lastTap = null; select(id); renameSelected(); return;
  }
  selected = id;
  const position = id === "board" ? layout.board : current().position;
  const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(canvas.getScreenCTM().inverse());
  drag = { id, dx: point.x - position.x, dy: point.y - position.y, moved: false, resize: !!event.target.closest("[data-resize]") };
  canvas.setPointerCapture(event.pointerId); render();
});
canvas.addEventListener("pointermove", event => {
  if (wireDrag !== null && event.pointerId === wireDrag && !busy) {
    wirePointer = new DOMPoint(event.clientX, event.clientY).matrixTransform(canvas.getScreenCTM().inverse());
    drawPendingWire(); return;
  }
  if (!drag) return;
  const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(canvas.getScreenCTM().inverse());
  const position = drag.id === "board" ? layout.board : current().position;
  if (drag.resize) {
    const item = current(), angle = -(item.rotation || 0) * Math.PI / 180;
    const dx = point.x - position.x, dy = point.y - position.y;
    item.width = Math.round(Math.max(isController(item) ? 80 : 20, Math.min(800, 2 * (dx * Math.cos(angle) - dy * Math.sin(angle)))));
    item.height = Math.round(Math.max(isController(item) ? 40 : 12, Math.min(600, 2 * (dx * Math.sin(angle) + dy * Math.cos(angle)))));
    drag.moved = true; renderCanvas(); return;
  }
  position.x = Math.round(Math.max(0, Math.min(1000, point.x - drag.dx)) / 5) * 5;
  position.y = Math.round(Math.max(0, Math.min(700, point.y - drag.dy)) / 5) * 5;
  drag.moved = true; renderCanvas();
});
function finishDrag(event) {
  if (!drag) return;
  lastTap = !drag.moved && event.type === "pointerup" ? { id: drag.id, time: event.timeStamp } : null;
  if (drag.moved) changed(); drag = null; renderInspector();
}
canvas.addEventListener("pointerup", finishDrag); canvas.addEventListener("pointercancel", finishDrag);
canvas.addEventListener("pointerup", finishWire);
canvas.addEventListener("pointercancel", finishWire);
canvas.addEventListener("lostpointercapture", event => { if (wireDrag === event.pointerId) cancelWire(); });
window.addEventListener("blur", () => { if (wireDrag !== null) cancelWire(); });
canvas.addEventListener("dblclick", event => {
  const name = event.target.closest("[data-name]");
  if (name) { event.preventDefault(); renameInline(name.dataset.name); return; }
  const id = event.target.closest("[data-object]")?.dataset.object;
  if (id && id !== "board" && !wiring) { select(id); renameSelected(); }
});
canvas.addEventListener("keydown", event => {
  if (busy || !layout) return;
  if (event.key === "Escape") { cancelWire(); guide(tr("Wiring cancelled.")); return; }
  if (["Enter", " "].includes(event.key)) {
    const remove = event.target.closest("[data-remove]"), port = event.target.closest("[data-port]");
    if (remove || port) {
      event.preventDefault();
      if (remove) removeItem(remove.dataset.remove);
      else if (wiring) select(port.dataset.port);
      else beginWire(port.dataset.port);
      return;
    }
  }
  if (event.key === "F2") {
    event.preventDefault();
    const focused = event.target.closest("[data-object]")?.dataset.object;
    if (focused && focused !== selected) select(focused);
    renameSelected(); return;
  }
  const id = event.target.closest("[data-object]")?.dataset.object || selected;
  if (["Enter", " "].includes(event.key)) { event.preventDefault(); select(id); return; }
  const delta = { ArrowLeft: [-1,0], ArrowRight:[1,0], ArrowUp:[0,-1], ArrowDown:[0,1] }[event.key];
  if (!delta) return;
  event.preventDefault(); selected = id;
  if (event.target.closest("[data-resize]")) {
    const item = current(), step = event.shiftKey ? 20 : 5;
    item.width = Math.max(isController(item) ? 80 : 20, Math.min(800, item.width + delta[0] * step));
    item.height = Math.max(isController(item) ? 40 : 12, Math.min(600, item.height + delta[1] * step));
    changed(); render(); canvas.querySelector(`[data-resize="${id}"]`)?.focus(); return;
  }
  const position = id === "board" ? layout.board : current().position;
  position.x = Math.max(0, Math.min(1000, position.x + delta[0] * (event.shiftKey ? 20 : 5)));
  position.y = Math.max(0, Math.min(700, position.y + delta[1] * (event.shiftKey ? 20 : 5)));
  changed(); render(); canvas.querySelector(`[data-object="${id}"]`)?.focus();
});
document.querySelectorAll("[data-w2-add]").forEach(node => node.addEventListener("click", async () => {
  const kind = node.dataset.w2Add === "light" ? await chooseLightShape() : node.dataset.w2Add;
  if (kind) add(kind);
}));
$("w2-inspector-form").addEventListener("submit", event => { event.preventDefault(); work(save); });
$("w2-save").addEventListener("click", () => work(save));
$("w2-reload").addEventListener("click", () => { if (!dirty || confirm(tr("Discard unsaved layout changes?"))) work(load); });
$("w2-enabled").addEventListener("change", () => { layout.enabled = $("w2-enabled").checked; changed(); });
try { $("w2-autosave").checked = localStorage.getItem("oche.wled2.autosave") !== "0"; } catch { /* Default on. */ }
$("w2-autosave").addEventListener("change", () => {
  try { localStorage.setItem("oche.wled2.autosave", $("w2-autosave").checked ? "1" : "0"); } catch { /* This visit only. */ }
  controls();
  scheduleAutosave(200);
});
$("w2-inspector-form").addEventListener("focusout", () => scheduleAutosave(300));
$("w2-connect").addEventListener("click", () => {
  focusConnector();
});
for (const [value, label] of Object.entries(events)) {
  const option = element("option", label); option.value = value; $("w2-event").append(option);
}
$("w2-fire").addEventListener("click", () => { if (layout && !busy) startSimulation(false); });
$("w2-simulate").addEventListener("click", () => { if (layout && !busy) startSimulation(true); });
$("w2-show-live").addEventListener("change", () => {
  if (simulation) stopSimulation();
});
$("w2-stop").addEventListener("click", stopSimulation);
$("w2-test").addEventListener("click", () => testLight());
$("w2-rename").addEventListener("click", renameSelected);
$("w2-discover").addEventListener("click", () => work(async () => {
  guide(tr("Searching for controllers on your network…"));
  const result = await api("/api/wled2/discover", "POST");
  const list = $("w2-found"); list.replaceChildren(); list.hidden = !result.devices.length;
  for (const found of result.devices) button(list, `${found.name} · ${found.url}`, () => {
    if (layout.controllers.some(c => c.url.replace(/\/$/, "") === found.url.replace(/\/$/, ""))) { error(tr("That controller is already in your layout.")); return; }
    add("controller", found.name, found.url);
  });
  guide(result.devices.length ? tr("Choose a discovered controller to add it.") : tr("No controllers found. Add a controller manually using its IP address."));
}));
window.addEventListener("beforeunload", event => { if (dirty || editingName) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("hashchange", refresh);
poll(refresh);
const editor = {
  async prepare() {
    if (busy || editingName || drag) throw new Error(tr("Finish the current lighting edit first."));
    await loading;
    clearTimeout(autosaveTimer);
    if (dirty) await save();
  },
  lock() { $("w2-inspector-form").inert = true; },
  async reload(applied) {
    if (applied) await load();
    controls();
  },
};
registerProfileEditor("WLED visual", editor);
registerLightingEditor("visual", {
  prepare: () => editor.prepare(),
  async reload() { await loading; await load(); },
});
