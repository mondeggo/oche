import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import vm from "node:vm";
import test from "node:test";
import { sourceControls } from "../app/static/core/sources.mjs";
import { callerTestState, eventSource } from "../app/static/core/client.mjs";
import { canonicalUrl, homeRoutes, isHome, pageForUrl, persistent, viewKey } from "../app/static/js/routes.mjs";
import { metricSeries } from "../app/static/js/metrics.mjs";

const core = new URL("../app/static/core/", import.meta.url);

test("legacy links resolve to direct routes and Home views share one retained document", () => {
  const base = "http://localhost:8180/";
  for (const [page, path] of Object.entries(homeRoutes)) {
    const url = canonicalUrl(`${base}#${page}`, base);
    assert.equal(url.pathname, path);
    assert.equal(url.hash, "");
    assert.equal(pageForUrl(url), page);
    assert.equal(viewKey(url), "home");
    assert.equal(persistent(path), true);
  }
  const camera = canonicalUrl("/supervisor?service=cameras", base);
  assert.equal(camera.pathname, "/integrations/cameras");
  assert.equal(isHome(camera.pathname), false);
  assert.equal(persistent(camera.pathname), true);
  assert.notEqual(viewKey(camera), "home");
  assert.notEqual(viewKey(new URL("/supervisor?service=system", base)), viewKey(new URL("/supervisor?service=updates", base)));
});

test("home navigation opens Play instead of the removed Overview", () => {
  const base = "http://localhost:8180/integrations";
  assert.equal(canonicalUrl("/", base).pathname, "/local");
  assert.equal(canonicalUrl("/#overview", base).pathname, "/local");
  assert.equal(canonicalUrl("/#overview", base).hash, "");
  assert.equal(isHome("/"), false);
  assert.equal(Object.hasOwn(homeRoutes, "overview"), false);
});

function navigationContext(path = "/integrations") {
  const handlers = {}, frames = [];
  const original = { hidden: false };
  let context, routeEvents = 0;
  const history = { pushState: (state, title, href) => { context.location = new URL(href, context.location.href); } };
  history.replaceState = history.pushState;
  const document = { title: "Oche", getElementById: id => id === "oche-page" ? original : null,
    addEventListener() {}, body: { appendChild: frame => frames.push(frame) },
    createElement: () => ({ hidden: true, listeners: {}, contentDocument: null,
      addEventListener(type, listener) { this.listeners[type] = listener; } }),
  };
  context = vm.createContext({ URL, resolveUrl: canonicalUrl, isHome, persistent, viewKey,
    location: new URL(path, "http://localhost:8180"), history, document,
    Event: class { constructor(type) { this.type = type; } },
    fetch: () => Promise.reject(new Error("Offline sidebar refresh")),
    addEventListener: (type, listener) => { handlers[type] = listener; },
    dispatchEvent: event => { if (event.type === "oche:route") routeEvents++; },
  });
  context.window = context.parent = document.defaultView = context;
  const code = readFileSync(new URL("../app/static/js/navigation.js", import.meta.url), "utf8").replace(/^import .+;\r?\n/gm, "");
  vm.runInContext(code, context);
  return { context, frames, handlers, original, routeEvents: () => routeEvents };
}

test("retained navigation updates direct URLs and back navigation preserves the camera view", () => {
  const { context, frames, handlers, routeEvents } = navigationContext();
  context.ocheNavigate("/integrations/caller");
  assert.equal(context.location.pathname, "/integrations/caller");
  assert.equal(frames.length, 0);
  assert.ok(routeEvents() > 0);
  context.ocheNavigate("/integrations/cameras");
  assert.equal(frames.length, 1);
  const camera = frames[0];
  camera.draft = "preserved";
  context.ocheNavigate("/events");
  assert.equal(camera.hidden, true);
  context.location = new URL("http://localhost:8180/integrations/cameras");
  handlers.popstate();
  assert.equal(frames.length, 1);
  assert.equal(camera.hidden, false);
  assert.equal(camera.draft, "preserved");
});

test("a workspace that finishes loading late opens the latest requested integration", () => {
  const { context, frames } = navigationContext("/config");
  context.ocheNavigate("/integrations");
  context.ocheNavigate("/integrations/caller");
  assert.equal(frames.length, 1);
  const home = { location: new URL("http://localhost:8180/integrations"), Event: context.Event,
    dispatchEvent() {}, history: { replaceState(state, title, href) { home.location = new URL(href, home.location); } } };
  frames[0].contentDocument = { defaultView: home, title: "Caller" };
  frames[0].listeners.load();
  assert.equal(home.location.pathname, "/integrations/caller");
  assert.equal(context.location.pathname, "/integrations/caller");
});

test("event sources distinguish raw local states and normalized local games from cloud traffic", () => {
  assert.equal(eventSource({ source: "autodarts-local", data: {} }, true), "local");
  assert.equal(eventSource({ channel: "local", topic: "board.state" }, true), "local");
  assert.equal(eventSource({ channel: "board-events" }, true), "cloud");
  assert.equal(eventSource({ source: "core", data: { play_source: "local" } }), "local");
  assert.equal(eventSource({ source: "core", data: { play_source: "cloud" } }), "cloud");
});

test("metric charts use actual sample timestamps and ignore unavailable or expired values", () => {
  const samples = [{ timestamp: 0, cpu: 90 }, { timestamp: 50, cpu: null }, { timestamp: 85, cpu: 30 }, { timestamp: 100, cpu: 120 }];
  const [series] = metricSeries(samples, ["cpu"], 60, 100, 100);
  assert.equal(series.points.length, 2);
  assert.equal(series.points[0].x, 75);
  assert.equal(series.points[1].x, 100);
  assert.equal(series.points[1].y, 2);
  const [io] = metricSeries([{ timestamp: 100, read: 8 }], ["read"], 60, 100);
  assert.equal(io.points[0].y, 2);
});
function element(dataset = {}) {
  const attributes = new Map();
  const state = { textContent: "" };
  return { dataset, disabled: false, hidden: false, value: "", elements: [], listeners: {}, style: {},
    classList: { toggle() {} },
    setAttribute: (key, value) => attributes.set(key, value),
    getAttribute: key => attributes.get(key),
    removeAttribute: key => attributes.delete(key),
    querySelector: () => state,
    querySelectorAll: () => [],
    addEventListener(type, callback) { this.listeners[type] = callback; },
    append() {}, replaceChildren() {}, add() {}, reportValidity: () => true,
  };
}
function scriptContext(file, api = async () => ({}), overrides = {}) {
  const nodes = new Map();
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  const polls = [], editors = {};
  const context = vm.createContext({
    $, api, poll: callback => polls.push(callback), tr: value => value, sourceControls,
    page: "integrations", profileChanging: false, notice() {}, setIntegrationEnabled() {}, callerTestState,
    registerProfileEditor: (name, editor) => { editors[name] = editor; },
    URL, setTimeout, clearTimeout,
    Option: class { constructor(text, value) { this.textContent = text; this.value = value; } },
    simulationGame: () => [],
    window: { addEventListener() {} },
    document: { querySelectorAll: () => [], createElement: () => element(), activeElement: null,
      documentElement: { lang: "en" } },
    localStorage: { getItem: () => null },
    ...overrides,
  });
  // Execute the actual browser module with its imported dependencies supplied above.
  const code = readFileSync(new URL(file, core), "utf8").replace(/^import[\s\S]*?;\r?\n/gm, "");
  context.document.getElementById ||= $;
  vm.runInContext(code, context);
  return { context, $, polls, editors };
}

test("metrics settings save numeric choices, hide disabled charts and preserve rejected edits", async () => {
  let config = { enabled: true, retention_minutes: 60, sample_seconds: 30 }, reject = false;
  const changes = [];
  const { context, $ } = scriptContext("../js/system.js", async (path, method, body) => {
    if (path === "/system/status") return { cpu_percent: 10, mem_percent: 20, disk_percent: 30, uptime_s: 3600 };
    assert.equal(path, "/system/history");
    if (method === "PUT") {
      if (reject) throw new Error("Invalid retention");
      config = JSON.parse(JSON.stringify(body));
      changes.push(config);
    }
    return { config: { ...config }, samples: [], error: null };
  }, { metricSeries, setInterval() {}, document: { hidden: false, documentElement: { lang: "en" },
    addEventListener() {}, createTextNode: value => String(value), createElement: () => element(),
    createElementNS: () => element() } });
  await new Promise(setImmediate);
  assert.equal($("history-minutes").value, 60);
  $("history-enabled").checked = false;
  $("history-minutes").value = "90";
  $("history-seconds").value = "60";
  $("history-form").listeners.input();
  await assert.rejects(context.window.ocheBeforeAppearanceChange(), /Save your metrics history/);
  $("history-form").listeners.submit({ preventDefault() {} });
  await context.window.ocheBeforeAppearanceChange();
  assert.deepEqual(changes, [{ enabled: false, retention_minutes: 90, sample_seconds: 60 }]);
  assert.equal($("spark-cpu").hidden, true);
  reject = true;
  $("history-minutes").value = "1";
  $("history-form").listeners.input();
  $("history-form").listeners.submit({ preventDefault() {} });
  await assert.rejects(context.window.ocheBeforeAppearanceChange(), /Save your metrics history/);
  assert.equal($("history-error").textContent, "Invalid retention");
  assert.equal(config.retention_minutes, 90);
});

test("every Home JavaScript module parses, including Visual WLED and integrations", () => {
  const integrations = new URL("../app/core/integrations/", import.meta.url);
  const folders = [core, ...readdirSync(integrations, { withFileTypes: true })
    .filter(entry => entry.isDirectory()).map(entry => new URL(`${entry.name}/static/`, integrations))
    .filter(folder => existsSync(folder))];
  for (const folder of folders) {
    for (const file of readdirSync(folder).filter(file => /\.(?:m?js)$/.test(file))) {
      const result = spawnSync(process.execPath, ["--check", "--input-type=module"], {
        input: readFileSync(new URL(file, folder), "utf8"), encoding: "utf8",
      });
      assert.equal(result.status, 0, `${file}: ${result.stderr}`);
    }
  }
});

test("source switches show On/Off in every copy and retain independent choices", () => {
  const buttons = ["run_on_cloud", "run_on_local", "run_on_cloud", "run_on_local"]
    .map(playSource => element({ playSource }));
  const previous = globalThis.document;
  globalThis.document = { querySelectorAll: () => buttons };
  try {
    let config = { run_on_cloud: true, run_on_local: false };
    const controls = sourceControls("wled2", (key, value) => {
      config[key] = value;
      controls.render(config);
    });
    controls.render(config);
    assert.deepEqual(buttons.map(button => button.getAttribute("aria-checked")), ["true", "false", "true", "false"]);
    assert.deepEqual(buttons.map(button => button.querySelector().textContent), ["On", "Off", "On", "Off"]);
    buttons[1].listeners.click();
    assert.equal(config.run_on_cloud, true);
    assert.equal(config.run_on_local, true);
    assert.equal(buttons[3].querySelector().textContent, "On");
    controls.render(config, true);
    buttons[0].listeners.click();
    assert.equal(config.run_on_cloud, true);
    controls.render(null);
    assert.ok(buttons.every(button => button.disabled && button.querySelector().textContent === "Unavailable"));
  } finally { globalThis.document = previous; }
});

test("Visual WLED initializes and displays saved On/Off and unavailable states", () => {
  const previous = globalThis.document;
  globalThis.document = { querySelectorAll: () => [] };
  try {
    const { context, $ } = scriptContext("wled2.js");
    vm.runInContext('layout = { controllers: [], lights: [] }; live = { enabled: false }; controls();', context);
    assert.equal($("w2-toggle-label").textContent, "Off");
    assert.equal($("w2-toggle").disabled, false);
    vm.runInContext('live.enabled = true; controls();', context);
    assert.equal($("w2-toggle-label").textContent, "On");
    assert.equal($("w2-toggle").getAttribute("aria-checked"), "true");
    vm.runInContext('live = null; controls();', context);
    assert.equal($("w2-toggle-label").textContent, "Unavailable");
    assert.equal($("w2-toggle").disabled, true);
  } finally { globalThis.document = previous; }
});

for (const name of ["wled", "caller"]) {
  test(`${name} loads source choices directly on Integrations and refreshes saved changes`, async () => {
    const buttons = ["run_on_cloud", "run_on_local"].map(playSource => element({ playSource }));
    const document = { querySelectorAll: () => buttons, documentElement: { lang: "en" } };
    const previous = globalThis.document;
    globalThis.document = document;
    try {
      let revision = 1, catalogueAllowed = false;
      const requests = [];
      const configuration = { enabled: false, run_on_cloud: false, run_on_local: true,
        devices: [], volume: 0.5, output: "host", voice: "" };
      const status = { ...configuration, active_profile: "default", game: { phase: "waiting" },
        download: { state: "idle" }, installed: false, recent_calls: [], missing_sounds: [] };
      const { context, polls, $ } = scriptContext(`${name}.js`, async path => {
        requests.push(path);
        if (path === "/api/caller/voices") {
          assert.ok(catalogueAllowed, "The card must not load the voice catalogue");
          return [];
        }
        return { data: path.endsWith("/status") ? status : { ...configuration }, etag: String(revision) };
      }, { document });
      await polls[0]();
      assert.equal($(`${name}-integration-error`).hidden, true, $(`${name}-integration-error`).textContent);
      assert.ok(requests.includes(`/api/${name}`));
      assert.deepEqual(buttons.map(button => button.querySelector().textContent), ["Off", "On"]);
      assert.ok(buttons.every(button => !button.disabled));
      // Changes made by another client/profile appear without visiting Settings.
      revision++;
      configuration.run_on_cloud = true;
      configuration.run_on_local = false;
      await polls[0]();
      assert.deepEqual(buttons.map(button => button.querySelector().textContent), ["On", "Off"]);
      if (name === "caller") {
        catalogueAllowed = true;
        context.page = "caller";
        await polls[0]();
        assert.equal($("caller-integration-error").hidden, true, $("caller-integration-error").textContent);
        assert.ok(requests.includes("/api/caller/voices"), "Opening Caller still loads its voice choices");
      }
    } finally { globalThis.document = previous; }
  });
}

test("camera activation persists sidebar visibility and a rejected save preserves its state", async () => {
  let saved = true, reject = false;
  const changes = [];
  const { context, $ } = scriptContext("../../core/integrations/autodarts/static/cameras-card.js", async (path, method, body) => {
    assert.equal(path, "/config/data");
    if (method === "POST") {
      if (reject) throw new Error("Save failed");
      changes.push(JSON.parse(JSON.stringify(body)));
      saved = body.show_page_cameras;
    }
    return { show_page_cameras: saved };
  });
  await context.refresh();
  $("cameras-toggle").listeners.click();
  await context.window.ocheBeforeAppearanceChange();
  assert.deepEqual(changes, [{ show_page_cameras: false }]);
  assert.equal($("cameras-toggle-label").textContent, "Off");
  assert.equal($("nav-link-cameras").hidden, true);
  assert.equal($("cameras-settings").hidden, true);
  reject = true;
  $("cameras-toggle").listeners.click();
  await context.window.ocheBeforeAppearanceChange();
  assert.equal($("cameras-toggle").getAttribute("aria-checked"), "false");
  assert.equal($("nav-link-cameras").hidden, true);
  assert.equal($("cameras-integration-error").textContent, "Save failed");
  // Settings changes also appear when the retained Home page polls again.
  saved = true;
  await context.refresh();
  assert.equal($("nav-link-cameras").hidden, false);
});

test("a delayed camera visibility poll cannot overwrite a newer activation change", async () => {
  let release, delayed = false;
  const { context, $ } = scriptContext("../../core/integrations/autodarts/static/cameras-card.js", async (path, method) => {
    if (method === "POST") return { show_page_cameras: false };
    if (delayed) return new Promise(resolve => { release = resolve; });
    return { show_page_cameras: true };
  });
  await context.refresh();
  delayed = true;
  const poll = context.refresh();
  $("cameras-toggle").listeners.click();
  await context.window.ocheBeforeAppearanceChange();
  release({ show_page_cameras: true });
  await poll;
  assert.equal($("cameras-toggle-label").textContent, "Off");
  assert.equal($("nav-link-cameras").hidden, true);
});
