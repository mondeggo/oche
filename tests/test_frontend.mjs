import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { api, eventEntries, callerTestState, fitPanel } from "../app/static/core/client.mjs";
import { sourceEnabled } from "../app/static/core/sources.mjs";
import { engineHealth, localEventsHealth, localHealth, manualHealth, syncHealthRows } from "../app/static/js/health.mjs";
// The Autodarts integration contributes its event health to one engine indicator.
import { cloudHealth } from "../app/core/integrations/autodarts/static/health.mjs";
import { dartRegion, pitchPosition } from "../app/static/core/football.mjs";
import { boardMarkup, boardPoint, checkout, funnyName, hitKey, playerStats, seats } from "../app/static/core/game.mjs";

function appearanceContext(messages = {}, fetcher = async () => ({ ok: true,
  json: async () => ({ theme: "dark", language: "en", profile_id: "default" }) })) {
  const storage = new Map();
  const context = vm.createContext({
    document: {
      documentElement: { lang: "en", dataset: { theme: "dark", profileId: "default" } },
      getElementById: id => id === "oche-translations" ? { textContent: JSON.stringify(messages) } : null,
      querySelector: () => null, querySelectorAll: () => [], addEventListener: () => {},
    },
    fetch: fetcher, AbortSignal, setInterval: () => {}, addEventListener: () => {}, dispatchEvent: () => {},
    matchMedia: () => ({ matches: false, addEventListener: () => {} }),
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
    reloads: 0,
  });
  context.window = context.parent = context;
  context.location = { reload: () => { context.reloads++; } };
  vm.runInContext(readFileSync(new URL("../app/static/js/i18n.js", import.meta.url), "utf8"), context);
  return context;
}

test("browser translations interpolate safely, translate dynamic messages and retain English fallback", () => {
  const french = JSON.parse(readFileSync(new URL("../app/static/locales/fr.json", import.meta.url), "utf8"));
  const context = appearanceContext(french);
  assert.equal(context.tr("Settings"), "Paramètres");
  assert.equal(context.tr("{0} wins!", { 0: "<Zoë & A>" }), "<Zoë & A> gagne !");
  assert.equal(context.tr("Ana to throw"), "À Ana de lancer");
  assert.equal(context.tr("New message not in catalogue"), "New message not in catalogue");
  assert.equal(context.tr("Score {missing}"), "Score {missing}");
});

test("language selection saves against the active profile before reloading", async () => {
  let settings = { theme: "dark", language: "en", profile_id: "default" };
  const changes = [];
  const context = appearanceContext({}, async (url, options) => {
    if (options.method === "PATCH") {
      const update = JSON.parse(options.body);
      changes.push(update);
      settings = { ...settings, ...update };
    }
    return { ok: true, json: async () => ({ ...settings }) };
  });
  let prepared = false;
  context.ocheBeforeAppearanceChange = async () => { prepared = true; };
  vm.runInContext(readFileSync(new URL("../app/static/js/theme.js", import.meta.url), "utf8"), context);
  await context.ocheTheme.refreshSettings();
  await context.ocheTheme.setLanguage("fr");
  assert.deepEqual(changes, [{ language: "fr", profile_id: "default" }]);
  assert.equal(prepared, true);
  assert.equal(context.reloads, 1);
});

test("a delayed appearance response cannot overwrite a newly applied profile", async () => {
  let release;
  const context = appearanceContext({}, () => new Promise(resolve => { release = resolve; }));
  vm.runInContext(readFileSync(new URL("../app/static/js/theme.js", import.meta.url), "utf8"), context);
  context.ocheTheme.applyProfile({ theme: "light", language: "en" }, "second");
  release({ ok: true, json: async () => ({ theme: "dark", language: "en", profile_id: "default" }) });
  await context.ocheTheme.refreshSettings();
  assert.equal(context.document.documentElement.dataset.theme, "light");
  assert.equal(context.document.documentElement.dataset.profileId, "second");
});

test("rejected language changes do not reload or claim that the change was saved", async () => {
  const context = appearanceContext({}, async (url, options) => options.method === "PATCH"
    ? { ok: false, status: 409, json: async () => ({ detail: "The active profile changed." }) }
    : { ok: true, json: async () => ({ theme: "dark", language: "en", profile_id: "default" }) });
  vm.runInContext(readFileSync(new URL("../app/static/js/theme.js", import.meta.url), "utf8"), context);
  await context.ocheTheme.refreshSettings();
  await assert.rejects(context.ocheTheme.setLanguage("de"), /active profile changed/);
  assert.equal(context.reloads, 0);
});

test("missing and stopped selected engines are red with a recovery action", () => {
  const missing = engineHealth({ active: true, status: "nofile", unavailable_reason: "Native Windows development does not include Autodarts." });
  assert.equal(missing[0], "bad");
  assert.match(missing[2], /Native Windows/);
  const stopped = engineHealth({ active: true, status: "stopped" });
  assert.equal(stopped[0], "bad");
  assert.match(stopped[2], /Start this engine/);
  assert.equal(engineHealth(null)[0], "bad");
});

test("cloud setup and reconnection are amber, while connection failures are red", () => {
  for (const [auth, cloud, expected] of [
    ["unconfigured", "unconfigured", "warn"], ["disconnected", "waiting_for_login", "warn"],
    ["authenticated", "unconfigured", "warn"], ["authenticated", "reconnecting", "warn"],
    ["authenticated", "stopped", "bad"], ["authenticated", "error", "bad"],
    ["authenticated", "degraded", "bad"],
  ]) {
    const health = cloudHealth({ auth: { state: auth }, cloud: { state: cloud } });
    assert.equal(health[0], expected, `${auth}/${cloud}`);
    assert.ok(health[2].length > 20);
  }
});

test("cloud error details and rejected subscriptions remain visible", () => {
  const status = { auth: { state: "authenticated", error: "Session expired." },
    cloud: { state: "degraded", error: "Board subscription rejected.",
      subscription_errors: [{ error: "unauthorized client" }] } };
  const health = cloudHealth(status);
  assert.equal(health[0], "bad");
  assert.match(health[2], /Session expired/);
  assert.match(health[2], /unauthorized client/);
  assert.match(health[2], /account access/);
});

test("cloud connection with an offline board is red and explains the distinction", () => {
  const health = cloudHealth({ auth: { state: "authenticated" },
    cloud: { state: "connected", board_online: false } });
  assert.equal(health[0], "bad");
  assert.match(health[2], /connection is open/);
  assert.match(health[2], /board is offline/);
});

test("intentional local-play pause and sign-in approval are explained as warnings", () => {
  const paused = cloudHealth({ auth: { state: "disconnected" }, cloud: { state: "paused_for_local_game" } });
  assert.equal(paused[0], "warn");
  assert.match(paused[2], /intentionally paused/);
  assert.match(paused[2], /End local play/);
  const approval = cloudHealth({ auth: { state: "awaiting_authorization" }, cloud: { state: "waiting_for_login" } });
  assert.equal(approval[0], "warn");
  assert.match(approval[2], /sign-in code/);
});

test("healthy services stay green and local detection failure is also red", () => {
  assert.equal(cloudHealth({ auth: { state: "authenticated" }, cloud: { state: "connected" } })[0], "good");
  assert.equal(localHealth({ detecting: true, state: "detecting" })[0], "good");
  assert.equal(localHealth({ detecting: false, state: "stopped", reason: "Detection is stopped." })[0], "bad");
  assert.equal(localHealth({ detecting: false, state: "calibrating" })[0], "warn");
});

test("source selection distinguishes cloud games from local board players", () => {
  assert.equal(sourceEnabled({}, "local"), true);
  assert.equal(sourceEnabled({ run_on_local: false }, "local"), false);
  assert.equal(sourceEnabled({ run_on_local: false }, "cloud"), true);
  assert.equal(sourceEnabled({ run_on_cloud: false }, "cloud"), false);
  assert.equal(sourceEnabled({ run_on_cloud: false }, "local"), true);
});

test("engine indicators combine required checks and leave inactive engines grey", () => {
  const engine = { name: "autodarts", active: true, installed: true, status: "running", scores: true };
  const detector = { detecting: true, state: "detecting" };
  const connected = cloudHealth({ auth: { state: "authenticated" }, cloud: { state: "connected" } });
  assert.equal(engineHealth(engine, detector, connected)[0], "good");
  assert.equal(engineHealth(engine, detector, ["bad", "Disconnected", "Events failed"])[0], "bad");
  assert.match(engineHealth(engine, detector, connected)[2], /Service: Running.*Detection: Detecting.*Events: Connected/);
  assert.equal(engineHealth(engine, { state: "calibrating", detecting: false }, connected)[0], "warn");
  assert.equal(engineHealth({ ...engine, status: "stopped" }, detector, connected)[0], "bad");
  assert.equal(engineHealth({ ...engine, active: false, status: "stopped" }, null, null)[0], "neutral");
  assert.equal(manualHealth()[0], "good");
  const unsupported = engineHealth({ ...engine, name: "ochecore", scores: false,
    note: "OcheCore can't score games yet." }, detector, connected);
  assert.equal(unsupported[0], "warn");
  assert.match(unsupported[2], /can't score games yet/);
});

test("local engine events work without cloud and intentional pauses do not turn health red", () => {
  const engine = { active: true, installed: true, status: "running", scores: true };
  const detector = { detecting: true, state: "detecting" };
  const local = { active: true, input: "autodarts", detector: { connected: true } };
  assert.equal(engineHealth(engine, detector, localEventsHealth(local))[0], "good");
  assert.match(localEventsHealth(local)[2], /without a cloud connection/);
  assert.equal(localEventsHealth({ ...local, detector: { connected: false } })[0], "warn");
  assert.equal(localEventsHealth({ ...local, detector: { connected: false, error: "Lost board" } })[0], "bad");
  assert.equal(engineHealth(engine, detector, localEventsHealth({ ...local, input: "manual" }))[0], "good");
});

test("live status adds enabled engine rows and removes Manual as the selection changes", () => {
  class Node {
    constructor() { this.children = []; this.dataset = {}; this.ownerDocument = { createElement: () => new Node() }; }
    setAttribute() {}
    append(...nodes) {
      for (const node of nodes) { node.remove(); node.parent = this; this.children.push(node); }
    }
    insertBefore(node, before) {
      node.remove();
      node.parent = this;
      this.children.splice(before ? this.children.indexOf(before) : this.children.length, 0, node);
    }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
    querySelectorAll() { return this.children.filter(node => node.dataset.health); }
    querySelector() { return this.children.find(node => node.className === "nav-text"); }
  }
  const container = new Node();
  const manual = { id: "manual", label: "Manual", href: "/local", module: null };
  const autodarts = { id: "autodarts", label: "Autodarts", href: "/integrations/autodarts/board", module: "/autodarts.mjs" };
  assert.deepEqual(syncHealthRows(container, [manual]).map(row => row.dataset.health), ["manual"]);
  const rows = syncHealthRows(container, [autodarts]);
  assert.deepEqual(container.children.map(row => row.dataset.health), ["autodarts"]);
  assert.equal(rows[0].href, autodarts.href);
  assert.equal(rows[0].dataset.healthModule, autodarts.module);
  assert.equal(syncHealthRows(container, [manual, autodarts])[1], rows[0]);
  syncHealthRows(container, [manual]);
  assert.deepEqual(container.children.map(row => row.dataset.health), ["manual"]);
});

const panel = (id, matrix) => ({
  id,
  name: id,
  position: { x: 500, y: 100 },
  width: 150,
  height: 150,
  matrix,
});

test("a second 16x16 panel takes the free half of a 32x16 canvas", () => {
  const canvas = { width: 32, height: 16 };
  const first = panel("first", { width: 16, height: 16, x: 0, y: 0 });
  const second = panel("second", { width: 16, height: 16, x: 0, y: 0 });
  assert.equal(fitPanel(second, first, canvas), "plain");
  assert.deepEqual([second.matrix.x, second.matrix.y], [16, 0]);
  // The wired panel is left exactly as it was: only a panel filling the canvas is split.
  assert.deepEqual([first.matrix.width, first.matrix.height], [16, 16]);
  assert.deepEqual([first.matrix.x, first.matrix.y], [0, 0]);
  // The diagram shows the chained panel beside its upstream one.
  assert.deepEqual([second.position.x, second.position.y], [650, 100]);
  assert.deepEqual([second.width, second.height], [150, 150]);
});

test("a panel filling the whole canvas is split for the chained one", () => {
  const canvas = { width: 32, height: 16 };
  const wide = panel("wide", { width: 32, height: 16, x: 0, y: 0 });
  const half = panel("half", { width: 16, height: 16, x: 0, y: 0 });
  assert.equal(fitPanel(half, wide, canvas), "width");
  assert.deepEqual([wide.matrix.width, wide.matrix.height], [16, 16]);
  assert.deepEqual([half.matrix.x, half.matrix.y], [16, 0]);
  assert.ok(wide.position.x + wide.width <= half.position.x);

  const tall = { width: 16, height: 32 };
  const top = panel("top", { width: 16, height: 16, x: 0, y: 0 });
  const bottom = panel("bottom", { width: 16, height: 16, x: 0, y: 0 });
  assert.equal(fitPanel(bottom, top, tall), "plain");
  assert.deepEqual([bottom.matrix.x, bottom.matrix.y], [0, 16]);
  const stacked = panel("stacked", { width: 16, height: 32, x: 0, y: 0 });
  const under = panel("under", { width: 16, height: 16, x: 0, y: 0 });
  assert.equal(fitPanel(under, stacked, tall), "height");
  assert.deepEqual([under.matrix.x, under.matrix.y], [0, 16]);
});

test("a chain that cannot fit is refused instead of shrinking a panel away", () => {
  const full = panel("full", { width: 16, height: 16, x: 0, y: 0 });
  const extra = panel("extra", { width: 16, height: 16, x: 0, y: 0 });
  assert.equal(fitPanel(extra, full, { width: 16, height: 16 }), false);
  assert.deepEqual([full.matrix.width, full.matrix.height], [16, 16]);
});

function untilAborted(signal) {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

test("API timeout aborts a request that never receives headers", { timeout: 2000 }, async (t) => {
  let signal;
  t.mock.method(globalThis, "fetch", (path, options) => {
    signal = options.signal;
    return untilAborted(signal);
  });
  await assert.rejects(api("/api/status", "GET", undefined, { timeout: 10 }), {
    message: "The service took too long to respond. Check its connection and try again.",
  });
  assert.equal(signal.aborted, true);
});

test("API timeout also covers a stalled response body", { timeout: 2000 }, async (t) => {
  let signal;
  let readingBody = false;
  t.mock.method(globalThis, "fetch", async (path, options) => {
    signal = options.signal;
    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json() {
        readingBody = true;
        return untilAborted(signal);
      },
    };
  });
  await assert.rejects(api("/api/events/raw", "GET", undefined, { timeout: 10 }), {
    message: "The service took too long to respond. Check its connection and try again.",
  });
  assert.equal(readingBody, true);
  assert.equal(signal.aborted, true);
});

test("API exposes readable validation paths and preserves HTTP status", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({
    detail: [
      { loc: ["body", "devices", 0, "url"], msg: "A local HTTP address is required" },
      { loc: ["body", "devices", 0, "targets", 1, "brightness"], msg: "Must be at most 255" },
    ],
  }, { status: 422 }));
  await assert.rejects(api("/api/wled", "PATCH", { devices: [] }), {
    status: 422,
    message: "devices / 0 / url: A local HTTP address is required; devices / 0 / targets / 1 / brightness: Must be at most 255",
  });
});

test("API sends If-Match and returns response data with its new ETag", async (t) => {
  const body = { devices: [{ id: "ring", name: "Dartboard" }] };
  let request;
  t.mock.method(globalThis, "fetch", async (path, options) => {
    request = { path, ...options };
    return Response.json({ saved: true }, { headers: { ETag: '"revision-2"' } });
  });
  const result = await api("/api/wled", "PATCH", body, {
    etag: '"revision-1"', metadata: true,
  });
  assert.equal(request.path, "/api/wled");
  assert.equal(request.method, "PATCH");
  assert.equal(request.headers["If-Match"], '"revision-1"');
  assert.equal(request.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(request.body), body);
  assert.deepEqual(result, { data: { saved: true }, etag: '"revision-2"' });
  assert.equal(request.signal.aborted, false);
});

test("GET metadata includes ETag without adding a JSON body", async (t) => {
  let options;
  t.mock.method(globalThis, "fetch", async (path, value) => {
    options = value;
    return Response.json({ enabled: true }, { headers: { ETag: '"current"' } });
  });
  const result = await api("/api/wled", "GET", undefined, { metadata: true });
  assert.deepEqual(result, { data: { enabled: true }, etag: '"current"' });
  assert.equal(options.body, undefined);
  assert.equal(options.headers["Content-Type"], undefined);
  assert.equal(options.headers["If-Match"], undefined);
});

test("API returns plain JSON by default and null metadata for absent ETag", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ enabled: false }));
  assert.deepEqual(await api("/api/caller"), { enabled: false });
  assert.deepEqual(await api("/api/caller", "GET", undefined, { metadata: true }), {
    data: { enabled: false }, etag: null,
  });
});

test("stale ETag produces a 412 error without hiding the conflict message", async (t) => {
  t.mock.method(globalThis, "fetch", async (path, options) => {
    assert.equal(options.headers["If-Match"], '"stale"');
    return Response.json({ detail: "Settings changed in another client. Reload first." }, {
      status: 412,
    });
  });
  await assert.rejects(api("/api/wled", "PATCH", { devices: [] }, {
    etag: '"stale"', metadata: true,
  }), {
    status: 412,
    message: "Settings changed in another client. Reload first.",
  });
});

test("invalid JSON and network errors produce distinct failures", async (t) => {
  const fetch = t.mock.method(globalThis, "fetch", async () => new Response("unavailable", {
    status: 503,
  }));
  await assert.rejects(api("/api/status"), {
    message: "The service returned an invalid response (HTTP 503).",
  });
  const offline = new TypeError("Network unavailable");
  fetch.mock.mockImplementation(async () => { throw offline; });
  await assert.rejects(api("/api/status"), (error) => error === offline);
});

test("normalized event IDs survive rolling history and content changes", () => {
  const first = eventEntries([
    { id: "one", event: "throw", data: { points: 20 } },
    { id: "two", event: "turn_end", data: { score: 60 } },
  ], false);
  const updated = { id: "two", event: "turn_end", data: { score: 80 } };
  const second = eventEntries([updated, { id: "three", event: "player_changed" }], false, first);
  assert.equal(second[0].key, first[1].key);
  assert.equal(second[0].key, "event-two");
  assert.equal(second[1].key, "event-three");
  assert.equal(second[0].event, updated);
  assert.notEqual(second[0].signature, first[1].signature);
});

test("unchanged identical raw frames keep distinct occurrence keys", () => {
  const frames = [{ type: "ping" }, { type: "ping" }, { type: "ping" }];
  const first = eventEntries(frames, true);
  assert.equal(new Set(first.map((entry) => entry.key)).size, frames.length);
  const second = eventEntries(structuredClone(frames), true, first);
  assert.deepEqual(second.map((entry) => entry.key), first.map((entry) => entry.key));
  assert.deepEqual(second.map((entry) => entry.event), frames);
});

test("an appended duplicate raw frame receives its own key", () => {
  const frames = [{ type: "ping" }, { channel: "board", data: { count: 1 } }];
  const first = eventEntries(frames, true);
  const second = eventEntries([...frames, { type: "ping" }], true, first);
  assert.deepEqual(second.slice(0, 2).map((entry) => entry.key), first.map((entry) => entry.key));
  assert.equal(new Set(second.map((entry) => entry.key)).size, 3);
});

test("rolling raw history preserves the surviving duplicate, not the discarded occurrence", () => {
  const duplicate = { type: "ping" };
  const anchor = { channel: "board", data: { count: 1 } };
  const first = eventEntries([duplicate, anchor, duplicate], true);
  const second = eventEntries([anchor, duplicate, { type: "next" }], true, first);
  assert.equal(second[0].key, first[1].key);
  assert.equal(second[1].key, first[2].key);
  assert.ok(!first.some((entry) => entry.key === second[2].key));
  assert.equal(new Set(second.map((entry) => entry.key)).size, second.length);
});

test("the rolling 100-frame window stays bounded and retains overlapping row keys", () => {
  let frames = Array.from({ length: 100 }, (_, index) => ({ data: { index } }));
  let previous = eventEntries(frames, true);
  for (let index = 100; index < 105; index++) {
    frames = [...frames.slice(1), { data: { index } }];
    const current = eventEntries(frames, true, previous);
    assert.equal(current.length, 100);
    assert.deepEqual(
      current.slice(0, -1).map((entry) => entry.key),
      previous.slice(1).map((entry) => entry.key),
    );
    assert.ok(!previous.some((entry) => entry.key === current.at(-1).key));
    previous = current;
  }
});

test("clearing raw history does not reuse rows from an earlier recording", () => {
  const frames = [{ type: "ping" }];
  const first = eventEntries(frames, true);
  const cleared = eventEntries([], true, first);
  assert.deepEqual(cleared, []);
  const restarted = eventEntries(frames, true, cleared);
  assert.notEqual(restarted[0].key, first[0].key);
});

test("Caller test waits for the selected voice installation, even if an older pack exists", () => {
  const state = callerTestState({
    enabled: true,
    voice: "new",
    installed: true,
    download: { state: "downloading" },
  });
  assert.equal(state.disabled, true);
  assert.equal(state.label, "Downloading voice…");
  assert.match(state.message, /selected voice package/);
});
test("Caller test becomes available only for the installed and applied voice", () => {
  const status = {
    enabled: true,
    voice: "remi",
    installed: true,
    download: { state: "idle" },
  };
  assert.equal(callerTestState(status).disabled, false);
  assert.equal(callerTestState(status, { voiceChanged: true }).disabled, true);
  assert.match(
    callerTestState(status, { voiceChanged: true }).message,
    /Apply the selected voice/,
  );
  assert.equal(callerTestState({ ...status, installed: false }).disabled, true);
  assert.equal(callerTestState({ ...status, enabled: false }).disabled, true);
  assert.equal(callerTestState(status, { dirty: true }).disabled, true);
  assert.equal(callerTestState(status, { working: true }).disabled, true);
});
test("Caller test explains missing and installing voice states", () => {
  assert.match(callerTestState(null).message, /Choose and download/);
  const status = { voice: "remi", download: { state: "installing" } };
  assert.equal(callerTestState(status).label, "Installing voice…");
});


test("simulation draws matrix scores without a connected controller", async () => {
  const { simulationFace } = await import("../app/static/core/client.mjs");
  const matrix = { width: 16, height: 16, rotation: 0 };
  const start = simulationFace(matrix, 501);
  const thrown = simulationFace(matrix, 321);
  assert.equal(start.length, 16);
  assert.ok(start.every(row => row.length === 16));
  assert.ok(start.some(row => row.includes("1")));
  assert.notDeepEqual(start, thrown);
  assert.notDeepEqual(simulationFace(matrix, 0), thrown);
  for (const rotation of [90, 180, 270]) {
    const face = simulationFace({ ...matrix, rotation }, 321);
    assert.equal(face.join("").split("1").length, thrown.join("").split("1").length);
  }
});


test("simulated players start at 501, alternate visits and restore a bust", async () => {
  const { simulationGame } = await import("../app/static/core/client.mjs");
  const steps = simulationGame();
  assert.equal(steps[0].local_remaining, 501);
  assert.equal(steps[0].opponent_remaining, 501);
  const turns = steps.filter(step => step.event === "turn_started");
  assert.deepEqual(turns.map(step => step.player), [0, 1, 0, 1, 0, 1, 0]);
  assert.equal(turns[2].opponent_remaining, 401);
  assert.equal(turns[2].local_remaining, 321);
  assert.equal(steps.find(step => step.event === "bust").local_remaining, 141);
  assert.equal(steps.at(-1).local_remaining, 0);
  assert.equal(steps.at(-1).opponent_remaining, 121);
  for (const step of steps) assert.equal(step.remaining, step.player ? step.opponent_remaining : step.local_remaining);
});

test("rainbow spans LEDs and moves over time without a brightness pulse", async () => {
  const { simulationPixel } = await import("../app/static/core/client.mjs");
  const style = { effect: 9, color: "#ffffff", brightness: 128 };
  const first = simulationPixel(style, 0, 0, 48);
  assert.notEqual(first.color, simulationPixel(style, 0, 24, 48).color);
  assert.notEqual(first.color, simulationPixel(style, 1, 0, 48).color);
  assert.equal(first.opacity, 128 / 255);
  assert.equal(simulationPixel({ ...style, effect: 8 }, 0, 0, 48).color,
    simulationPixel({ ...style, effect: 8 }, 0, 24, 48).color);
  assert.equal(simulationPixel({ ...style, effect: 0 }, 1, 24, 48).color, "#ffffff");
});


test("opponent throws in every completed simulated round", async () => {
  const { simulationGame } = await import("../app/static/core/client.mjs");
  const steps = simulationGame();
  for (const round of [1, 2, 3]) {
    const darts = steps.filter(step => step.round === round && step.player === 1
      && ["single", "double", "triple", "bull", "outer_bull", "miss"].includes(step.event));
    assert.equal(darts.length, 3);
    assert.ok(darts.at(-1).opponent_remaining < darts[0].opponent_remaining);
  }
});

test("checkout suggestions end on preferred doubles and respect the darts left", () => {
  assert.deepEqual(checkout(170), ["T20", "T20", "Bull"]);
  assert.deepEqual(checkout(100), ["T20", "D20"]);
  assert.deepEqual(checkout(101), ["T17", "Bull"]);
  assert.deepEqual(checkout(40, 1), ["D20"]);
  assert.equal(checkout(100, 1), null);
  assert.equal(checkout(169), null);
  assert.equal(checkout(1), null);
  assert.equal(checkout(171), null);
  assert.deepEqual(checkout(60, 1, false), ["T20"]);
  assert.deepEqual(checkout(180, 3, false), ["T20", "T20", "T20"]);
  for (let score = 2; score <= 170; score++) {
    const route = checkout(score);
    if (!route) continue;
    assert.ok(route.at(-1).startsWith("D") || route.at(-1) === "Bull", `${score} ends on a double`);
  }
});

test("player stats average scored darts, count busts as zero and show the last visit", () => {
  const dart = () => ({ segment: { number: 20, multiplier: 3 } });
  const frame = {
    players: [{ id: "a" }, { id: "b" }],
    turns: [
      { playerId: "b", throws: [dart()], points: 60 },
      { playerId: "a", throws: [dart(), dart(), dart()], points: 0, busted: true, finishedAt: "t" },
      { playerId: "a", throws: [dart(), dart(), dart()], points: 180, finishedAt: "t" },
    ],
  };
  const [alice, bob] = playerStats(frame);
  assert.equal(alice.average, 90);
  assert.equal(alice.busted, true);
  assert.equal(bob.average, null);
  assert.equal(bob.last, undefined);
});

test("default player names are playful, unique and mark bots", () => {
  assert.equal(funnyName([], false, () => 0), "Lucky Otter");
  assert.equal(funnyName(["Lucky Otter"], false, () => 0), "Lucky Badger");
  assert.equal(funnyName(["[Bot] Lucky Otter"], true, () => 0), "[Bot] Lucky Badger");
  assert.match(funnyName(), /^\w+ \w+$/);
});

test("the board marks segments by key and places coordinates with y pointing up", () => {
  const markup = boardMarkup();
  assert.equal(markup.match(/data-hit="S20"/g).length, 2);
  for (const key of ["T20", "D20", "S25", "D25", "T5", "D3"]) assert.ok(markup.includes(`data-hit="${key}"`), key);
  assert.equal(hitKey({ number: 20, multiplier: 3 }), "T20");
  assert.equal(hitKey({ number: 25, multiplier: 2 }), "D25");
  assert.equal(hitKey({ number: 0, multiplier: 1 }), null);
  assert.deepEqual(boardPoint({ x: 0, y: 0.6 }), [0, -60]);
  assert.deepEqual(boardPoint({ x: 3, y: 0 }), [120, 0]);
});

test("players are seated clockwise around the board", () => {
  assert.deepEqual(seats(1), { left: [0], right: [] });
  assert.deepEqual(seats(2), { left: [0], right: [1] });
  assert.deepEqual(seats(3), { left: [0], right: [1, 2] });
  assert.deepEqual(seats(4), { left: [0, 3], right: [1, 2] });
  assert.deepEqual(seats(5), { left: [0, 4], right: [1, 2, 3] });
  assert.deepEqual(seats(8), { left: [0, 7, 6, 5], right: [1, 2, 3, 4] });
});

test("missing compatible modules stay installable across refreshes and submit installation", async () => {
  const nodes = [];
  function node(tag = '') {
    const entry = { tag, dataset: {}, children: [], listeners: {}, disabled: false,
      append(child) { this.children.push(child); },
      replaceChildren() { this.children = []; },
      setAttribute() {}, contains() { return false; },
      addEventListener(event, listener) { this.listeners[event] = listener; },
      querySelector(selector) {
        const action = selector.match(/data-action="(.*?)"/)?.[1];
        const matches = child => action ? child.dataset.action === action : child.tag === selector;
        const find = parent => {
          for (const child of parent.children) {
            if (matches(child)) return child;
            const found = find(child);
            if (found) return found;
          }
          return null;
        };
        return find(this);
      },
    };
    nodes.push(entry);
    return entry;
  }
  const ids = Object.fromEntries(['update-status', 'update-modules', 'check-updates', 'update-all',
    'update-installation-notice'].map(id => [id, node()]));
  const item = { name: 'autodarts', present: false, installable: true, has_update: false,
    available: '2.1.1', runtime_compatible: true, installed: [] };
  const data = { enabled: true, modules: [item], job: { status: 'complete', action: 'check' } };
  const posts = [];
  let refresh;
  const context = vm.createContext({
    document: { getElementById: id => ids[id], createElement: node, activeElement: null,
      dispatchEvent() {}, querySelectorAll: () => nodes.filter(entry => entry.tag === 'button') },
    window: {}, tr: text => text, CustomEvent: class {},
    setInterval: callback => { refresh = callback; },
    fetch: async (path, options) => {
      if (options.method === 'POST') posts.push(path);
      return { ok: true, json: async () => options.method === 'POST' ? { job_id: 'install' } : data };
    },
  });
  vm.runInContext(readFileSync(new URL('../app/static/js/updates.js', import.meta.url), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  const install = () => ids['update-modules'].querySelector('[data-action="update"]');
  assert.equal(install().textContent, 'Install');
  assert.equal(install().disabled, false);
  await refresh(); // The row signature is unchanged; button restoration must retain Install.
  assert.equal(install().disabled, false);
  assert.equal(ids['update-all'].disabled, true); // Bulk updates do not install missing modules.
  await install().listeners.click();
  assert.deepEqual(posts, ['/updates/autodarts/update']);
  assert.equal(install().disabled, false);
  data.job = { status: 'running', module: 'autodarts', installing: true };
  await refresh();
  const badge = () => ids['update-modules'].children[0].children[0].children.find(child => child.tag === 'span');
  assert.equal(badge().textContent, 'Installing…');
  item.present = true; // Activation has created the release; this is still a first install.
  await refresh();
  assert.equal(badge().textContent, 'Installing…');
  data.job.installing = false;
  data.job.status = 'complete';
  await refresh();
  data.job.status = 'running';
  await refresh();
  assert.equal(badge().textContent, 'Updating…');
  data.job = { status: 'complete', action: 'check' };
  item.present = false;
  await refresh();
  for (const [target, key, value] of [[item, 'runtime_compatible', false],
    [data, 'enabled', false], [data.job, 'status', 'running'], [item, 'installable', false]]) {
    const original = target[key];
    target[key] = value;
    await refresh();
    assert.equal(install().disabled, true, `${key} must block installation`);
    target[key] = original;
    await refresh();
    assert.equal(install().disabled, false);
  }
});


test("football pitch and board distinguish inner/outer singles and align D11/D6 goals", () => {
  const single = coords => ({ segment: { number: 17, multiplier: 1 }, coords });
  assert.equal(dartRegion(single({ x: 0, y: 65 / 170 })), "I17");
  assert.equal(dartRegion(single({ x: 0, y: 135 / 170 })), "O17");
  assert.equal(dartRegion({ segment: { number: 25, multiplier: 1 } }), "OB");
  assert.equal(dartRegion({ segment: { number: 25, multiplier: 2 } }), "B");
  assert.deepEqual(pitchPosition("B"), [500, 320]);
  assert.deepEqual(pitchPosition("D11"), [27, 320]);
  assert.deepEqual(pitchPosition("D6"), [973, 320]);
  assert.ok(Math.abs(pitchPosition("D11")[1] - 320) < 0.001);
  const markup = boardMarkup();
  for (const region of ["I17", "O17", "T17", "D17", "B", "OB"]) {
    assert.equal(markup.split(`data-region="${region}"`).length - 1, 1);
  }
});
