import { chooseLightShape, lightSegments, lightShapeNames, registerLightingEditor } from "./lighting-view.mjs";
import { tr } from "../js/i18n.mjs";
import { $, api, poll } from "./client.mjs";
import { sourceControls } from "./sources.mjs";
import {
  page,
  notice,
  setIntegrationEnabled,
  registerProfileEditor,
  profileChanging,
} from "./app.js";

/* WLED forms edit service configuration; device traffic stays in the headless core. */
(() => {
  // Must match app.core.integrations.wled.simulator.DEMO_URL - an in-process fake
  // controller, never a real network address.
  const DEMO_URL = "http://oche-demo.local";
  let configuration = null;
  const sources = sourceControls("wled", (key, value) => work(async () => {
    const current = await api("/api/wled", "GET", undefined, { metadata: true });
    const saved = await api("/api/wled", "PATCH", { [key]: value },
      { etag: current.etag, metadata: true });
    configuration = { ...current.data, [key]: value };
    configurationETag = saved.etag;
    render();
  }));
  let configurationETag = null;
  let statusETag = null;
  let saveConflict = false;
  let selected = "";
  let changed = false;
  let working = false;
  let refreshing = null;
  let latestStatus = null;
  let statusError = "";
  let discovered = [];
  let saveTimer = null;
  let saveError = "";
  let saving = null;
  let editRevision = 0;
  const capabilities = new Map();
  const selectedOutputs = new Map();
  const selectedTabs = new Map();
  let outputSignature = "";
  const phases = {
    ready: tr("Ready to throw"),
    takeout: tr("Remove darts"),
    waiting: tr("Wait"),
    idle: tr("Idle"),
  };
  const phaseHints = {
    ready: tr("Your turn. Step up to the oche."),
    takeout: tr("Collect your darts from the board."),
    waiting: tr("Hold your throw until the board is ready."),
    idle: tr("Between games, keep the mood you like."),
  };
  const colourModes = {
    color: tr("Colour"),
    white: tr("White"),
    color_white: tr("Colour + white"),
    on_off: tr("On / off"),
    unknown: tr("Type unavailable"),
  };
  const effectGroups = [
    {
      id: "victories",
      title: tr("Match & victories"),
      effects: {
        leg_win: tr("Leg won"),
        match_win: tr("Match won"),
      },
    },
    {
      id: "hits",
      title: tr("Hits & throws"),
      effects: {
        throw: tr("Any dart"),
        single: tr("Single"),
        double: tr("Double"),
        triple: tr("Triple"),
        outer_bull: tr("Outer bull (25)"),
        bull: tr("Bullseye (50)"),
        miss: tr("Miss"),
        score_180: "180",
        bust: tr("Bust"),
      },
    },
    {
      id: "transitions",
      title: tr("Game flow & transitions"),
      effects: {
        match_started: tr("Match started"),
        match_ended: tr("Match ended"),
        turn_started: tr("Turn started"),
        takeout_started: tr("Takeout started"),
        takeout_finished: tr("Takeout finished"),
        manual_reset: tr("Manual reset"),
        calibration_started: tr("Calibration started"),
        calibration_finished: tr("Calibration finished"),
      },
    },
  ];
  const form = $("wled-form");
  const field = (root, name) => root.querySelector(`[data-field="${name}"]`);
  const matrixField = (root, name) =>
    root.querySelector(`[data-matrix-field="${name}"]`);
  const device = () =>
    configuration?.devices.find((item) => item.id === selected);
  const identity = (prefix) => `${prefix}-${Date.now().toString(36)}`;
  const appearance = (color = "#ffffff") => ({
    color,
    brightness: 128,
    effect: 0,
  });
  const newTarget = () => ({
    id: identity("target"),
    name: tr("Board lighting"),
    enabled: true,
    segment: 0,
    mode: "segment",
    shape: "strip",
    start: 0,
    count: 1,
    phases: {
      ready: appearance("#00ff00"),
      takeout: { ...appearance("#e17100"), brightness: 191 },
      waiting: appearance("#ff0000"),
      idle: { ...appearance("#6f00dd"), brightness: 64, effect: 2 },
    },
    effects: {
      throw: { ...appearance("#ffffff"), effect: 12, duration: 0.6 },
      single: { ...appearance("#00aaff"), effect: 3, duration: 0.6 },
      double: { ...appearance("#ff8000"), effect: 1, duration: 0.8 },
      triple: { ...appearance("#a000ff"), effect: 33, duration: 1 },
      outer_bull: { ...appearance("#00ffaa"), effect: 6, duration: 1 },
      bull: { ...appearance("#ff0080"), effect: 9, duration: 1.5 },
      miss: { ...appearance("#808080"), effect: 12, duration: 0.5 },
      score_180: { ...appearance("#ffb000"), effect: 33, duration: 2 },
      bust: { ...appearance("#ff0000"), duration: 2 },
      leg_win: { ...appearance("#00aaff"), duration: 2 },
      match_win: { ...appearance("#a000ff"), effect: 9, duration: 10 },
    },
    players: {},
    matrix: {
      width: 16,
      height: 8,
      x: 0,
      y: 0,
      player_slot: 1,
      serpentine: true,
      rotation: 0,
      source: "remaining",
      appearance: appearance("#ff0000"),
    },
  });

  function buttons() {
    sources.render(configuration, working || profileChanging || changed || !!saving);
    $("wled-toggle").disabled =
      working || profileChanging || !latestStatus || !!statusError;
    $("wled-toggle-label").textContent = working
      ? tr("Saving…")
      : statusError
        ? tr("Unavailable")
        : !latestStatus
          ? tr("Loading…")
          : latestStatus.enabled
            ? tr("On")
            : tr("Off");
    if (working) {
      [...form.elements].forEach((control) => {
        control.disabled = true;
      });
      return;
    }
    $("wled-save-state").textContent =
      saveError ||
      (saving ? tr("Saving…") : changed ? tr("Waiting to save…") : tr("All changes saved"));
    $("wled-save-state").classList.toggle("error", !!saveError);
    $("wled-save-state").dataset.state = saveError
      ? "error"
      : saving
        ? "saving"
        : changed
          ? "pending"
          : "saved";
    $("wled-retry").hidden = !saveError || saveConflict;
    $("wled-reload").textContent = saveConflict
      ? tr("Reload saved settings")
      : tr("Discard changes");
    $("wled-reload").hidden = !changed;
    $("wled-device").disabled = working || changed;
    $("wled-reload").disabled = working;
    $("wled-probe").disabled = working || !device();
    const saved = latestStatus?.devices.find(
      (item) =>
        item.id === selected &&
        origin(item.url) === origin($("wled-url").value.trim()),
    );
    $("wled-power-on").disabled = $("wled-power-off").disabled = !saved;
    const address = $("wled-url").value.trim();
    const link = $("wled-open");
    try {
      const url = new URL(address);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password)
        throw new Error(tr("Invalid WLED address"));
      // The demo controller's address only resolves inside Oche's own outgoing
      // requests - send a browser here instead, to the page that actually renders it.
      link.href =
        url.origin === origin(DEMO_URL) ? `${location.origin}/demo-wled/` : url.origin;
      link.hidden = false;
    } catch {
      link.removeAttribute("href");
      link.hidden = true;
    }
    form.querySelectorAll("[data-rule]").forEach(updateAppearance);
    $("wled-add-device").disabled = working || !configuration;
    $("wled-discover").disabled = !configuration;
    $("wled-add-device").disabled ||= configuration?.devices.length >= 8;
    const demoAdded = configuration?.devices.some(
      (item) => origin(item.url) === origin(DEMO_URL),
    );
    $("wled-add-demo").disabled = $("wled-add-device").disabled || demoAdded;
    $("wled-add-demo").textContent = demoAdded
      ? tr("Demo controller added")
      : tr("Try a demo controller");
    form.querySelectorAll("[data-add-found]").forEach((button) => {
      const added = alreadyConfigured(
        discovered[Number(button.dataset.addFound)],
      );
      button.disabled = added || configuration.devices.length >= 8;
      button.textContent = added ? tr("Added") : tr("Add");
    });
    $("wled-name").disabled = $("wled-url").disabled = $(
      "wled-idle-timeout",
    ).disabled = !device();
    renderOutputs();
  }

  function markChanged() {
    changed = true;
    editRevision += 1;
    if (!saveConflict) saveError = "";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDraft, 500);
    buttons();
  }

  function saveDraft() {
    if (!changed || working || saving || saveConflict) return;
    if (!form.checkValidity()) {
      saveError = tr("Complete the highlighted settings to save automatically.");
      buttons();
      return;
    }
    collect();
    const devices = structuredClone(configuration.devices);
    const revision = editRevision;
    saving = (async () => {
      try {
        const saved = await api(
          "/api/wled",
          "PATCH",
          { devices },
          {
            etag: configurationETag,
            metadata: true,
          },
        );
        configurationETag = saved.etag;
        changed = revision !== editRevision;
        saveError = "";
        for (const option of $("wled-device").options) {
          const item = configuration.devices.find(
            (device) => device.id === option.value,
          );
          if (item) option.textContent = item.name;
        }
      } catch (error) {
        saveConflict = error.status === 412;
        if (saveConflict) {
          saveError =
            tr("Settings changed in another client. Your draft is kept here. Reload saved settings before editing again.");
        } else if (revision === editRevision)
          saveError = tr("Not saved: {0}", {"0": (error.message)});
      } finally {
        saving = null;
        buttons();
        if (changed && !saveError && !working)
          saveTimer = setTimeout(saveDraft, 500);
      }
    })();
    buttons();
    return saving;
  }

  function origin(url) {
    try {
      return new URL(url).origin;
    } catch {
      return url;
    }
  }

  function deviceInfo() {
    const address = origin($("wled-url").value.trim());
    return (
      capabilities.get(address) ||
      latestStatus?.devices.find((item) => origin(item.url) === address)?.info
    );
  }

  function alreadyConfigured(found) {
    const address = new URL(found.url);
    const origins = [address.origin];
    if (found.hostname) {
      address.hostname = found.hostname;
      origins.push(address.origin);
    }
    return configuration.devices.some((item) =>
      origins.includes(origin(item.url)),
    );
  }

  function addDevice(name, url) {
    if (!configuration || configuration.devices.length >= 8) return;
    collect();
    const added = {
      id: identity("device"),
      name,
      url,
      enabled: true,
      idle_timeout_minutes: null,
      targets: [],
    };
    configuration.devices.push(added);
    selected = added.id;
    // Rebuild the zones before refreshing controls for the new device.
    render();
    markChanged();
    $("wled-connection").open = true;
    $("wled-url").focus();
  }

  function renderDiscovery() {
    const list = $("wled-discovered");
    list.replaceChildren();
    list.hidden = !discovered.length;
    discovered.forEach((found, index) => {
      const row = document.createElement("div");
      row.className = "wled-discovered-device";
      const description = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = found.name;
      const details = document.createElement("p");
      details.className = "hint";
      details.textContent = `${found.url} · WLED ${found.version}`;
      description.append(title, details);
      const add = document.createElement("button");
      add.type = "button";
      add.dataset.addFound = index;
      add.textContent = tr("Add");
      add.addEventListener("click", () => {
        collect();
        if (alreadyConfigured(found)) {
          buttons();
          return;
        }
        addDevice(found.name, found.url);
        notice(
          tr("Device added. Choose its lighting zones; changes save automatically."),
        );
      });
      row.append(description, add);
      list.append(row);
    });
    buttons();
  }

  function label(text, input) {
    const element = document.createElement("label");
    element.append(text, input);
    return element;
  }

  function input(type, value, name, min, max) {
    const element = document.createElement("input");
    element.type = type;
    element.dataset.value = name;
    if (min !== undefined) {
      element.min = min;
      element.max = max;
      element.required = true;
    }
    if (type === "checkbox") element.checked = value;
    else element.value = value;
    return element;
  }

  function appearanceRow(key, title, value, isEffect, mode, isPlayer = false) {
    const row = document.createElement("div");
    row.className = "wled-appearance";
    row.dataset.rule = key;
    row.classList.toggle("wled-phase-card", !isEffect);
    const heading = document.createElement("div");
    heading.className = "wled-rule-heading";
    const name = document.createElement("strong");
    name.textContent = title;
    if (isEffect) {
      const toggle = label(title, input("checkbox", Boolean(value) && value.enabled !== false, "enabled"));
      toggle.className = "check-label";
      heading.append(toggle);
      const state = document.createElement("span");
      state.dataset.ruleState = "";
      state.className = "hint";
      heading.append(state);
    } else {
      heading.append(name);
      const description = document.createElement("p");
      description.textContent = phaseHints[key];
      heading.append(description);
    }
    row.append(heading);
    const controls = document.createElement("div");
    controls.className = "wled-rule-controls";
    row.append(controls);
    const settings = value || { ...appearance(), duration: 2 };
    row.style.setProperty("--phase-colour", settings.color);
    if (isPlayer) {
      row.dataset.playerSlot = key;
      const filter = input("text", settings.name_filter || "", "name_filter");
      filter.maxLength = 80;
      filter.placeholder = tr("Player {0} by position", {"0": (key)});
      controls.append(label(tr("Name contains (optional)"), filter));
    }
    const select = document.createElement("select");
    select.dataset.value = "effect";
    const info = deviceInfo();
    updateEffectOptions(select, settings.effect);
    const effectLabel = label(tr("Effect"), select);
    effectLabel.dataset.nativeEffect = "";
    effectLabel.hidden = mode !== "segment";
    controls.append(effectLabel);
    if (mode === "segment" && !info?.effects?.length) {
      const hint = document.createElement("span");
      hint.className = "hint";
      hint.dataset.effectsHint = "";
      hint.textContent = tr("Check connection to load effects.");
      controls.append(hint);
    }
    const colour = label(tr("Colour"), input("color", settings.color, "color"));
    colour.dataset.colourControl = "";
    colour.querySelector("input").addEventListener("input", (event) => {
      row.style.setProperty("--phase-colour", event.target.value);
    });
    controls.append(colour);
    const brightness = label(
      tr("Brightness"),
      input("range", settings.brightness, "brightness", 0, 255),
    );
    brightness.className = "wled-brightness";
    const level = document.createElement("output");
    brightness.append(level);
    controls.append(brightness);
    const updateLevel = () => {
      const value = Number(brightness.querySelector("input").value);
      level.textContent = value ? `${Math.round((value / 255) * 100)}%` : tr("Off");
      brightness
        .querySelector("input")
        .setAttribute("aria-valuetext", level.textContent);
    };
    brightness.querySelector("input").addEventListener("input", updateLevel);
    updateLevel();
    if (isEffect && !isPlayer) {
      const duration = input("number", settings.duration, "duration", 0.1, 30);
      duration.step = "0.1";
      controls.append(label(tr("Seconds"), duration));
    }
    const preview = document.createElement("button");
    preview.type = "button";
    preview.textContent = "▶ Preview";
    preview.dataset.preview = key;
    preview.setAttribute("aria-label", tr("Preview {0}", {"0": (title.toLowerCase())}));
    preview.addEventListener("click", () =>
      previewTarget(row.closest("[data-target]"), key, isEffect, isPlayer),
    );
    controls.append(preview);
    select.addEventListener("change", () => updateAppearance(row));
    row
      .querySelector('[data-value="enabled"]')
      ?.addEventListener("change", () => updateAppearance(row));
    return row;
  }

  function updateEffectOptions(select, selected = select.value) {
    const effects = deviceInfo()?.effects;
    const signature = JSON.stringify(effects || []);
    if (select.dataset.catalogue === signature) return;
    select.dataset.catalogue = signature;
    select.replaceChildren();
    (effects?.length ? effects : [tr("Solid")]).forEach((name, index) => {
      if (!["-", "RSVD"].includes(name)) select.add(new Option(name, index));
    });
    if (
      ![...select.options].some(
        (option) => Number(option.value) === Number(selected),
      )
    )
      select.add(new Option(tr("Effect {0}", {"0": (selected)}), selected));
    select.value = selected;
  }

  function updateAppearance(row) {
    const enabled = row.querySelector('[data-value="enabled"]');
    const active = !enabled || enabled.checked;
    const root = row.closest("[data-target]");
    const native = field(root, "mode").value === "segment";
    const effect = Number(row.querySelector('[data-value="effect"]').value);
    const slots = deviceInfo()?.effect_colors?.[effect];
    const colorMode = deviceInfo()?.segments?.find(
      (segment) => segment.id === Number(field(root, "segment").value),
    )?.color_mode;
    row.dataset.colorMode = colorMode || "unknown";
    row.querySelector("[data-colour-control]").hidden =
      ["white", "on_off"].includes(colorMode) ||
      Boolean(native && slots && !slots[0]);
    row.classList.toggle("is-enabled", active);
    row.querySelector(".wled-rule-controls").hidden = !active;
    if (enabled)
      row.querySelector("[data-rule-state]").textContent = active
        ? tr("On")
        : tr("Off");
    row.querySelectorAll("input, select, button").forEach((control) => {
      control.disabled = working || (!active && control !== enabled);
      if (
        control.matches('[data-value="effect"]') &&
        !deviceInfo()?.effects?.length
      )
        control.disabled = true;
    });
  }

  function previewTarget(
    root,
    rule = "ready",
    isEffect = false,
    isPlayer = false,
  ) {
    if (!form.reportValidity()) return;
    collect();
    const current = structuredClone(device());
    const target = current.targets.find(
      (item) => item.id === root.dataset.target,
    );
    const draft = {
      device: current,
      target_id: target.id,
      phase: isEffect ? "ready" : rule,
      event: isEffect && !isPlayer ? rule : null,
      player: isPlayer ? Number(rule) : null,
      value:
        target.mode === "matrix"
          ? Number(root.querySelector("[data-test-value]").value)
          : null,
      duration: 3,
    };
    work(async () => {
      notice(tr("Previewing for 3 seconds…"));
      await api("/api/wled/preview", "POST", draft);
      notice(tr("Preview complete. Previous lighting restored."));
    });
  }

  function targetMode(root) {
    const mode = field(root, "mode").value;
    if (mode === "matrix") field(root, "shape").value = "matrix";
    else if (field(root, "shape").value === "matrix") field(root, "shape").value = "strip";
    root.querySelector("[data-pixels]").hidden = mode === "segment";
    root.querySelector("[data-count]").hidden = mode !== "pixels";
    for (const tab of root.querySelectorAll("[data-editor-tab]")) {
      const key = tab.dataset.editorTab;
      tab.hidden =
        key === "display"
          ? mode !== "matrix"
          : key !== "events" && mode === "matrix";
    }
    const selectedTab = selectedTabs.get(root.dataset.target) || "phases";
    selectTab(
      root,
      root.querySelector(`[data-editor-tab="${selectedTab}"]`).hidden
        ? mode === "matrix"
          ? "display"
          : "phases"
        : selectedTab,
    );
    root.querySelector("[data-test-score]").hidden = mode !== "matrix";
    root.querySelectorAll("[data-native-effect]").forEach((node) => {
      node.hidden = mode !== "segment";
    });
    root.querySelectorAll("[data-rule]").forEach(updateAppearance);
  }

  function selectTab(root, key) {
    selectedTabs.set(root.dataset.target, key);
    for (const tab of root.querySelectorAll("[data-editor-tab]")) {
      const active = tab.dataset.editorTab === key;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    }
    for (const panel of root.querySelectorAll("[data-editor-panel]"))
      panel.hidden = panel.dataset.editorPanel !== key;
  }

  const outputLabel = (output) =>
    output.matrix
      ? output.name
      : output.pins.length
        ? `GPIO ${output.pins.join(" + ")}`
        : tr("Output {0}", {"0": (output.id + 1)});

  function deviceOutputs(info) {
    return [
      ...(info?.outputs || []),
      ...(info?.segments || [])
        .filter((segment) => segment.matrix)
        .map((segment) => ({
          id: `matrix-${segment.id}`,
          name: segment.name,
          segment: segment.id,
          matrix: segment.matrix,
          color_mode: segment.color_mode,
          pins: [],
          length: segment.length,
          type_name: `${segment.matrix.width} × ${segment.matrix.height} matrix`,
        })),
    ];
  }

  function outputsForTarget(target, info) {
    const segment = info?.segments.find((item) => item.id === target.segment);
    if (!segment) return [];
    if (segment.matrix)
      return deviceOutputs(info).filter(
        (output) => output.matrix && output.segment === segment.id,
      );
    const start =
      segment.start + (target.mode === "segment" ? 0 : target.start);
    const stop =
      target.mode === "segment"
        ? segment.stop
        : start +
          (target.mode === "matrix"
            ? target.matrix.width * target.matrix.height
            : target.count);
    return (info.outputs || []).filter(
      (output) => Math.max(start, output.start) < Math.min(stop, output.stop),
    );
  }

  function outputSegments(output, info) {
    return (info?.segments || []).filter(
      (segment) =>
        !output ||
        (output.matrix
          ? segment.id === output.segment
          : !segment.matrix &&
            segment.start >= output.start &&
            segment.stop <= output.stop),
    );
  }

  function segmentOptions(root, target, info) {
    const select = field(root, "segment");
    // Capability refreshes must preserve an edited or temporarily unavailable segment.
    const value = Number(select.value || target.segment);
    const options = (info?.segments || []).map((segment) => {
      const outputs = outputsForTarget(
        { ...target, segment: segment.id, mode: "segment" },
        info,
      );
      return new Option(
        `${segment.name} · ID ${segment.id}${segment.matrix ? ` · ${segment.matrix.width} × ${segment.matrix.height} matrix` : outputs.length ? ` · ${outputs.map(outputLabel).join(" / ")}${outputs.length > 1 ? " (shared)" : ""}` : ""}`,
        segment.id,
      );
    });
    if (!options.some((item) => Number(item.value) === value))
      options.push(
        new Option(tr("Segment {0}{1}", {"0": (value), "1": (info ? " (unavailable)" : "")}), value),
      );
    select.replaceChildren(...options);
    select.value = value;
  }

  function renderOutputs() {
    const current = device();
    if (!current) return;
    const info = deviceInfo();
    const outputs = deviceOutputs(info);
    let choice = selectedOutputs.get(current.id);
    if (
      choice !== "all" &&
      !outputs.some((output) => String(output.id) === choice)
    ) {
      const first =
        current.targets[0] && outputsForTarget(current.targets[0], info);
      choice =
        first?.length === 1
          ? String(first[0].id)
          : "all";
      selectedOutputs.set(current.id, choice);
    }
    const selectedOutput = outputs.find(
      (output) => String(output.id) === choice,
    );
    $("wled-outputs-section").hidden = !outputs.length;
    const signature = JSON.stringify([current.id, info?.segments, outputs]);
    if (signature !== outputSignature) {
      outputSignature = signature;
      const list = $("wled-outputs");
      list.replaceChildren();
      for (const output of [null, ...outputs]) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.output = output ? String(output.id) : "all";
        const name = document.createElement("strong");
        name.textContent = output ? outputLabel(output) : tr("All zones");
        const icon = document.createElement("span");
        icon.className = "wled-output-icon";
        icon.dataset.mode = output?.color_mode || "unknown";
        icon.setAttribute("aria-hidden", "true");
        const kind = document.createElement("span");
        kind.className = "wled-output-kind";
        kind.textContent = output
          ? colourModes[output.color_mode] || tr("Type unavailable")
          : tr("Overview");
        const detail = document.createElement("span");
        detail.className = "wled-output-detail";
        detail.textContent = output
          ? `${output.type_name} · ${output.length} ${output.length === 1 ? "LED" : "LEDs"}`
          : tr("Every lighting zone");
        button.append(icon, name, kind, detail);
        button.addEventListener("click", () => {
          selectedOutputs.set(current.id, button.dataset.output);
          renderOutputs();
        });
        list.append(button);
      }
      for (const root of $("wled-targets").querySelectorAll("[data-target]")) {
        const target = current.targets.find(
          (item) => item.id === root.dataset.target,
        );
        segmentOptions(root, target, info);
      }
    }
    $("wled-outputs")
      .querySelectorAll("[data-output]")
      .forEach((button) => {
        button.setAttribute(
          "aria-pressed",
          String(button.dataset.output === choice),
        );
        button.disabled = working;
      });
    let visible = 0;
    for (const root of $("wled-targets").querySelectorAll("[data-target]")) {
      const target = current.targets.find(
        (item) => item.id === root.dataset.target,
      );
      const matches = outputsForTarget(target, info);
      root.hidden =
        !!selectedOutput &&
        !matches.some((output) => String(output.id) === choice);
      if (!root.hidden) visible++;
      const outputNote = root.querySelector("[data-output-note]");
      outputNote.classList.toggle("error", matches.length > 1);
      outputNote.textContent =
        matches.length > 1
          ? tr("Controls {0}. This zone affects all these outputs even when you filter by one. Create separate segments in WLED to control them independently.", {"0": (matches.map(outputLabel).join(" and "))})
          : matches.length
            ? tr("Controls {0} · {1}", {"0": (outputLabel(matches[0])), "1": (colourModes[matches[0].color_mode] || matches[0].type_name)})
            : tr("Output information unavailable. Check the connection.");
      root.querySelectorAll("[data-rule]").forEach(updateAppearance);
      const segment = info?.segments?.find(
        (item) => item.id === Number(field(root, "segment").value),
      );
      matrixField(root, "serpentine").closest("label").hidden =
        !!segment?.matrix;
      matrixField(root, "player_slot").closest("label").hidden =
        matrixField(root, "source").value !== "player_remaining";
      const split = root.querySelector("[data-split-matrix]");
      const panelWidth = Number(matrixField(root, "width").value);
      split.hidden = !segment?.matrix;
      split.disabled = working || current.targets.length >= 16 ||
        panelWidth < 22 || panelWidth % 2 !== 0;
      const geometryHint = root.querySelector("[data-matrix-layout]");
      geometryHint.hidden = !segment?.matrix;
      geometryHint.textContent = segment?.matrix
        ? tr("WLED segment: {0} × {1}. Place each panel using X, Y, width and height, with First LED 0. For two 16 × 16 panels on a 32 × 16 segment, use X 0 and 16, Y 0. Wiring is managed in WLED.", {"0": (segment.matrix.width), "1": (segment.matrix.height)})
        : "";
      const warning = root.querySelector("[data-geometry-warning]");
      warning.hidden =
        field(root, "mode").value === "segment" ||
        !segment?.pixel_control_error;
      warning.textContent = segment?.pixel_control_error || "";
      matrixField(root, "color").closest("label").hidden = [
        "white",
        "on_off",
      ].includes(segment?.color_mode);
    }
    const segments = outputSegments(selectedOutput, info);
    const saved = latestStatus?.devices.find(
      (item) => item.id === selected && origin(item.url) === origin(current.url),
    );
    const separate = !!selectedOutput && !selectedOutput.matrix && segments.length > 0 &&
      !(info?.segments || []).some((segment) =>
        segment.start < selectedOutput.stop && segment.stop > selectedOutput.start &&
        (segment.matrix || segment.start < selectedOutput.start || segment.stop > selectedOutput.stop),
      );
    $("wled-output-power").hidden = !selectedOutput || !!selectedOutput.matrix;
    const off = saved?.on === false || segments.every(
      (segment) => saved?.off_segments?.includes(segment.id) ||
        (saved?.info?.segments?.find((item) => item.id === segment.id) || segment).on === false,
    );
    $("wled-output-power-label").textContent = selectedOutput
      ? `${outputLabel(selectedOutput)} · ${saved?.on === false ? "Off (device off)" : separate ? off ? "Off" : "On" : "Separate segments required in WLED"}`
      : "";
    const sharedSegments = selectedOutput && !selectedOutput.matrix
      ? (info?.segments || []).filter((segment) =>
        segment.start < selectedOutput.stop && segment.stop > selectedOutput.start &&
        (segment.matrix || segment.start < selectedOutput.start || segment.stop > selectedOutput.stop),
      ) : [];
    $("wled-output-power-hint").textContent = !separate
      ? sharedSegments.length
        ? `${sharedSegments.map((segment) => `Segment ${segment.id}`).join(" and ")} also cover another output. Power controls are unavailable because turning them on would affect other GPIOs. In WLED, give ${outputLabel(selectedOutput)} a segment with start ${selectedOutput.start} and stop ${selectedOutput.stop} (exclusive), and keep other segments outside this range. Then check the connection.`
        : tr("Independent power control needs separate, non-overlapping segments for this output in WLED. Update the segments, then check the connection.")
      : changed
        ? tr("Waiting for your changes to save before changing output power.")
        : !saved
          ? tr("Save this controller and check its connection to control output power.")
          : saved.on === false
            ? tr("The device is off. Turn this output on to wake the device with only this output enabled; other outputs will stay off.")
            : "";
    for (const on of [true, false]) {
      const button = $(`wled-output-${on ? "on" : "off"}`);
      button.textContent = on ? tr("On") : tr("Off");
      button.disabled = working || changed || !saved || !separate;
      button.setAttribute("aria-pressed", String(
        on ? separate && !off : saved?.on === false || separate && off,
      ));
    }
    $("wled-add-target").disabled =
      working ||
      current.targets.length >= 128 ||
      (!!selectedOutput && !segments.length);
    $("wled-add-target").textContent = selectedOutput
      ? tr("Add light on {0}", {"0": (outputLabel(selectedOutput))})
      : tr("Add light");
    const hiddenCount = selectedOutput ? current.targets.length - visible : 0;
    const hiddenHint = $("wled-hidden-zones-hint");
    hiddenHint.hidden = !hiddenCount;
    hiddenHint.textContent = hiddenCount
      ? `${hiddenCount} more ${hiddenCount === 1 ? "zone" : "zones"} on other outputs — choose All zones to see ${hiddenCount === 1 ? "it" : "them"}.`
      : "";
    $("wled-output-empty").hidden = visible > 0;
    $("wled-output-empty-heading").textContent =
      selectedOutput && !segments.length
        ? info?.matrix ? tr("Select a matrix") : tr("No dedicated segment on this output")
        : current.targets.length ? tr("No lighting zones on this output") : tr("No lighting rules yet");
    $("wled-target-heading").textContent = selectedOutput
      ? tr("Zones affecting {0}", {"0": (outputLabel(selectedOutput))})
      : tr("Lighting rules");
    $("wled-output-empty-text").textContent =
      selectedOutput && !segments.length
        ? info?.matrix
          ? tr("For a 2D layout, choose a matrix card above. Physical GPIO outputs do not define its logical rows and columns.")
          : tr("To add a zone here, create a dedicated segment for this output in WLED, then check the connection again.")
        : current.targets.length
          ? tr("Choose All zones above to view your saved lighting rules, or add a zone on this output.")
          : tr("Add a target{0} to set up phase lighting and event effects.", {"0": (selectedOutput ? ` on ${outputLabel(selectedOutput)}` : "")});
  }

  function render() {
    const select = $("wled-device");
    select.replaceChildren(
      ...configuration.devices.map((item) => new Option(item.name, item.id)),
    );
    if (!device()) selected = configuration.devices[0]?.id || "";
    if (!selected) select.add(new Option(tr("No devices configured"), ""));
    select.value = selected;
    const current = device();
    $("wled-device-editor").hidden = !current;
    $("wled-no-device").hidden = !!current;
    $("wled-name").disabled = $("wled-url").disabled = $(
      "wled-idle-timeout",
    ).disabled = !current;
    $("wled-targets").replaceChildren();
    if (!current) {
      buttons();
      return;
    }
    $("wled-name").value = current.name;
    $("wled-url").value = current.url;
    $("wled-device-enabled").checked = current.enabled;
    $("wled-idle-timeout").value = current.idle_timeout_minutes || 0;
    current.targets.forEach((target) => {
      const root = $(
        "wled-target-template",
      ).content.firstElementChild.cloneNode(true);
      root.dataset.target = target.id;
      root.querySelector("[data-title]").textContent = target.name;
      ["name", "mode", "start", "count"].forEach((key) => {
        field(root, key).value = target[key];
      });
      field(root, "shape").value = target.mode === "matrix" ? "matrix" : target.shape || "strip";
      field(root, "shape").addEventListener("change", () => {
        const shape = field(root, "shape").value;
        if (shape === "matrix") field(root, "mode").value = "matrix";
        else if (field(root, "mode").value === "matrix") field(root, "mode").value = "segment";
        targetMode(root);
      });
      segmentOptions(root, target, deviceInfo());
      field(root, "segment").addEventListener("change", () => {
        collect();
        const outputs = outputsForTarget(target, deviceInfo());
        selectedOutputs.set(
          current.id,
          outputs.length === 1 ? String(outputs[0].id) : "all",
        );
        renderOutputs();
      });
      field(root, "enabled").checked = target.enabled;
      ["source", "rotation", "width", "height", "x", "y", "player_slot"].forEach((key) => {
        matrixField(root, key).value = target.matrix[key];
      });
      root.querySelector("[data-split-matrix]").addEventListener("click", () => {
        collect();
        if (!form.reportValidity()) return;
        const width = target.matrix.width / 2;
        if (!Number.isInteger(width) || width < 11 || current.targets.length >= 16) return;
        target.matrix.width = width;
        target.matrix.source = "local_remaining";
        target.name = tr("My score");
        const opponent = structuredClone(target);
        opponent.id = identity("target");
        opponent.name = tr("Opponent score");
        opponent.matrix.x += width;
        opponent.matrix.source = "opponent_remaining";
        // On the visual canvas the second panel sits beside the first, wired after it.
        if (target.placement) {
          const place = target.placement;
          opponent.placement = {
            ...place,
            id: null,
            position: { x: Math.min(1000, place.position.x + place.width + 30), y: place.position.y },
            after_light_id: place.id || target.id,
            wired: true,
          };
        }
        current.targets.push(opponent);
        render();
        markChanged();
      });
      ["color", "brightness"].forEach((key) => {
        matrixField(root, key).value = target.matrix.appearance[key];
      });
      matrixField(root, "serpentine").checked = target.matrix.serpentine;
      for (const [key, title] of Object.entries(phases))
        root
          .querySelector("[data-phase-rows]")
          .append(
            appearanceRow(key, title, target.phases[key], false, target.mode),
          );
      for (const group of effectGroups) {
        const section = document.createElement("details");
        section.className = "wled-event-group";
        section.dataset.eventGroup = group.id;
        const heading = document.createElement("summary");
        heading.textContent = group.title;
        section.open = group.id === "victories";
        section.append(heading);
        for (const [key, title] of Object.entries(group.effects))
          section.append(
            appearanceRow(key, title, target.effects[key], true, target.mode),
          );
        root.querySelector("[data-effect-rows]").append(section);
      }
      for (let slot = 1; slot <= 10; slot++)
        root
          .querySelector("[data-player-rows]")
          .append(
            appearanceRow(
              String(slot),
              tr("Player {0}", {"0": (slot)}),
              target.players?.[slot],
              true,
              target.mode,
              true,
            ),
          );
      root.querySelectorAll("[data-editor-tab]").forEach((button) => {
        const key = button.dataset.editorTab;
        button.id = `${target.id}-tab-${key}`;
        const panel = root.querySelector(`[data-editor-panel="${key}"]`);
        panel.id = `${target.id}-panel-${key}`;
        button.setAttribute("aria-controls", panel.id);
        panel.setAttribute("aria-labelledby", button.id);
        button.addEventListener("click", () => selectTab(root, key));
        button.addEventListener("keydown", (event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
            return;
          event.preventDefault();
          const tabs = [...root.querySelectorAll("[data-editor-tab]")].filter(
            (tab) => !tab.hidden,
          );
          const index =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? tabs.length - 1
                : (tabs.indexOf(button) +
                    (event.key === "ArrowRight" ? 1 : -1) +
                    tabs.length) %
                  tabs.length;
          selectTab(root, tabs[index].dataset.editorTab);
          tabs[index].focus();
        });
      });
      field(root, "name").addEventListener("input", () => {
        root.querySelector("[data-title]").textContent =
          field(root, "name").value || tr("Lighting zone");
      });
      field(root, "mode").addEventListener("change", () => targetMode(root));
      root
        .querySelector("[data-remove]")
        .setAttribute("aria-label", tr("Remove zone {0}", {"0": (target.name)}));
      root.querySelector("[data-remove]").addEventListener("click", () => {
        if (
          !confirm(
            tr("Remove lighting zone \"{0}\" from all profiles?", {"0": (field(root, "name").value)}),
          )
        )
          return;
        collect();
        current.targets = current.targets.filter(
          (item) => item.id !== target.id,
        );
        // Remove stale zone controls before refreshing buttons or collecting a save.
        render();
        markChanged();
      });
      root
        .querySelector("[data-test]")
        .addEventListener("click", () => previewTarget(root));
      targetMode(root);
      $("wled-targets").append(root);
    });
    renderStatus();
    buttons();
  }

  function collectRules(root, selector, mode) {
    const result = {};
    root
      .querySelector(selector)
      .querySelectorAll("[data-rule]")
      .forEach((row) => {
        const enabled = row.querySelector('[data-value="enabled"]');
        if (row.dataset.playerSlot && enabled && !enabled.checked) return;
        const value = (name) =>
          row.querySelector(`[data-value="${name}"]`).value;
        const key = row.dataset.playerSlot || row.dataset.rule;
        result[key] = {
          color: value("color"),
          brightness: Number(value("brightness")),
          effect: mode === "segment" ? Number(value("effect")) : 0,
        };
        if (row.dataset.playerSlot)
          result[key].name_filter = value("name_filter").trim();
        else if (enabled) {
          result[key].duration = Number(value("duration"));
          result[key].enabled = enabled.checked;
        }
      });
    return result;
  }

  function collect() {
    const current = device();
    if (!current) return;
    current.name = $("wled-name").value.trim();
    current.url = $("wled-url").value.trim();
    current.enabled = $("wled-device-enabled").checked;
    current.idle_timeout_minutes =
      Number($("wled-idle-timeout").value) || null;
    $("wled-targets")
      .querySelectorAll("[data-target]")
      .forEach((root) => {
        const target = current.targets.find(
          (item) => item.id === root.dataset.target,
        );
        ["name", "mode", "shape"].forEach((key) => {
          target[key] = field(root, key).value;
        });
        ["segment", "start", "count"].forEach((key) => {
          target[key] = Number(field(root, key).value);
        });
        target.enabled = field(root, "enabled").checked;
        target.phases = collectRules(root, "[data-phase-rows]", target.mode);
        target.effects = collectRules(root, "[data-effect-rows]", target.mode);
        target.players = collectRules(root, "[data-player-rows]", target.mode);
        target.matrix = {
          width: Number(matrixField(root, "width").value),
          height: Number(matrixField(root, "height").value),
          x: Number(matrixField(root, "x").value),
          y: Number(matrixField(root, "y").value),
          player_slot: Number(matrixField(root, "player_slot").value),
          rotation: Number(matrixField(root, "rotation").value),
          source: matrixField(root, "source").value,
          serpentine: matrixField(root, "serpentine").checked,
          appearance: {
            color: matrixField(root, "color").value,
            brightness: Number(matrixField(root, "brightness").value),
            effect: 0,
          },
        };
      });
  }

  function renderStatus() {
    if (!latestStatus) return;
    $("wled-phase").textContent =
      phases[latestStatus.game.phase] || latestStatus.game.phase;
    $("wled-phase").dataset.phase = latestStatus.game.phase;
    $("wled-reason").textContent = tr(latestStatus.game.reason);
    renderOutputs();
    form
      .querySelectorAll('[data-value="effect"]')
      .forEach((select) => updateEffectOptions(select));
    form.querySelectorAll("[data-effects-hint]").forEach((hint) => {
      hint.hidden = !!deviceInfo()?.effects?.length;
    });
    $("wled-targets")
      .querySelectorAll(".wled-phase-card")
      .forEach((card) => {
        const active = card.dataset.rule === latestStatus.game.phase;
        if (active) card.setAttribute("aria-current", "step");
        else card.removeAttribute("aria-current");
      });
    const list = $("wled-device-status");
    list.replaceChildren();
    const item = latestStatus.devices.find(
      (item) =>
        item.id === selected &&
        origin(item.url) === origin($("wled-url").value.trim()),
    );
    if (item) {
      const state = document.createElement("span");
      state.className = "wled-connection-state";
      state.dataset.connected = String(item.connected);
      state.textContent = item.connected ? tr("Connected") : tr("Not connected");
      const detail = document.createElement("span");
      const power =
        item.on === false
          ? tr("Lights off")
          : item.on === true
            ? tr("Lights on")
            : tr("Power unknown");
      detail.textContent =
        item.error ||
        `${power} · ${latestStatus.enabled && item.enabled ? "Automatic lighting" : "Automation paused"}`;
      list.append(state, detail);
    } else if (device())
      list.textContent = tr("Check this address to verify the connection.");
    for (const on of [true, false])
      $(`wled-power-${on ? "on" : "off"}`).setAttribute(
        "aria-pressed",
        String(!!item && item.on === on),
      );
    const info = deviceInfo();
    $("wled-capabilities").textContent = info
      ? `WLED ${info.version} · ${info.segments.length} segments · ${info.effects.length} effects`
      : tr("Check the connection to load outputs, segments and effects.");
  }

  async function load(discard = false) {
    const revision = editRevision;
    const result = await api("/api/wled", "GET", undefined, { metadata: true });
    if (!discard && (changed || revision !== editRevision)) return;
    configuration = result.data;
    $("wled-unconnected-note").hidden = !configuration.unconnected_lights?.length;
    configurationETag = result.etag;
    changed = false;
    saveConflict = false;
    saveError = "";
    render();
  }

  function refreshWled(force = false) {
    if (working || (profileChanging && force !== true)) return;
    if (refreshing) return refreshing;
    refreshing = (async () => {
      try {
        const response = await api("/api/wled/status", "GET", undefined, {
          metadata: true,
        });
        latestStatus = response.data;
        statusETag = response.etag;
        statusError = "";
        syncIntegration();
        if (!configuration) await load();
        if (
          configuration &&
          !changed &&
          !saving &&
          (statusETag !== configurationETag ||
            latestStatus.active_profile !== configuration.active_profile)
        )
          await load();
        renderStatus();
      } catch (error) {
        statusError = error.message;
      } finally {
        for (const id of ["wled-error", "wled-integration-error"]) {
          $(id).textContent = statusError || latestStatus?.error || "";
          $(id).hidden = !$(id).textContent;
        }
        refreshing = null;
        buttons();
      }
    })();
    return refreshing;
  }

  function syncIntegration() {
    const enabled = latestStatus.enabled;
    if (configuration) configuration.enabled = enabled;
    $("wled-toggle").setAttribute("aria-checked", String(enabled));
    $("wled-settings").hidden = !enabled;
    setIntegrationEnabled("wled", enabled);
  }

  async function work(task) {
    if (working || profileChanging) return;
    working = true;
    clearTimeout(saveTimer);
    buttons();
    try {
      await refreshing;
      await saving;
      await task();
    } catch (error) {
      notice(error.message, true);
    } finally {
      [...form.elements].forEach((control) => {
        control.disabled = false;
      });
      working = false;
      buttons();
      if (changed && !saveError) saveTimer = setTimeout(saveDraft, 500);
      await refreshWled();
    }
  }

  form.addEventListener("input", (event) => {
    if (!event.target.matches("#wled-device, [data-test-value]")) markChanged();
    if (event.target.id === "wled-url") renderStatus();
  });
  form.addEventListener("change", (event) => {
    if (!event.target.matches("#wled-device, [data-test-value]")) markChanged();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveDraft();
  });
  $("wled-retry").addEventListener("click", saveDraft);
  $("wled-toggle").addEventListener("click", () => {
    work(async () => {
      if (changed)
        throw new Error(
          tr("Save or discard your WLED changes before toggling the integration."),
        );
      const current = await api("/api/wled", "GET", undefined, {
        metadata: true,
      });
      const enabled = !current.data.enabled;
      const saved = await api(
        "/api/wled",
        "PATCH",
        { enabled },
        {
          etag: current.etag,
          metadata: true,
        },
      );
      if (configuration) {
        configuration = { ...current.data, enabled };
        configurationETag = saved.etag;
        render();
      }
      latestStatus.enabled = enabled;
      syncIntegration();
      notice(
        enabled
          ? tr("WLED enabled. Open its settings to configure lights.")
          : tr("WLED disabled. Settings kept."),
      );
    });
  });
  $("wled-device").addEventListener("change", () => {
    selected = $("wled-device").value;
    render();
  });
  $("wled-add-device").addEventListener("click", () => {
    addDevice(tr("WLED controller"), "");
  });
  $("wled-add-demo").addEventListener("click", () => {
    addDevice(tr("Demo controller"), DEMO_URL);
    notice(
      tr("Demo controller added - it isn't a real device. Use Open WLED, below, to watch it update live as you edit."),
    );
  });
  $("wled-discover").addEventListener("click", () =>
    work(async () => {
      const message = $("wled-discovery-status");
      message.hidden = false;
      message.textContent =
        tr("Searching for WLED controllers… This takes a few seconds.");
      discovered = [];
      renderDiscovery();
      try {
        const result = await api("/api/wled/discover", "POST");
        discovered = result.devices;
        renderDiscovery();
        message.textContent = discovered.length
          ? tr("Found {0} controller{1}. Choose Add to configure one.", {"0": (discovered.length), "1": (discovered.length === 1 ? "" : "s")})
          : "No reachable controllers found. Check they are on Oche's network with mDNS enabled, or add an address manually.";
      } catch (error) {
        message.textContent =
          tr("Discovery failed. You can still add a device by address.");
        throw error;
      }
    }),
  );
  $("wled-remove-device").addEventListener("click", () => {
    if (
      !confirm(tr("Remove controller \"{0}\" and its lighting zones?", {"0": (device().name)}))
    )
      return;
    collect();
    configuration.devices = configuration.devices.filter(
      (item) => item.id !== selected,
    );
    selected = "";
    render();
    markChanged();
  });
  $("wled-add-target").addEventListener("click", async () => {
    const shape = await chooseLightShape();
    if (!shape || !device()) return;
    collect();
    const current = device();
    const info = deviceInfo();
    const output = deviceOutputs(info).find(
      (item) => String(item.id) === selectedOutputs.get(current.id),
    );
    const segments = outputSegments(output, info);
    if (output && !segments.length) return;
    const choices = lightSegments(shape, segments, info?.segments || segments);
    const unused = choices.find(
      (segment) =>
        !current.targets.some(
          (target) => target.enabled && target.segment === segment.id,
        ),
    );
    const segment = unused || choices[0];
    const target = newTarget();
    target.shape = shape;
    target.name = lightShapeNames()[shape];
    if (shape === "matrix") {
      target.mode = "matrix";
      target.phases.idle.effect = 0;
      for (const effect of Object.values(target.effects)) { effect.effect = 0; effect.enabled = false; }
    }
    if (segment) {
      target.segment = segment.id;
      target.name =
        `${output && segments.includes(segment) ? `${outputLabel(output)} · ` : ""}${segment.name}`.slice(
          0,
          80,
        );
      target.enabled = !!unused;
      if (
        shape === "matrix" && segment.matrix &&
        segment.matrix.width >= 11 &&
        segment.matrix.height >= 5 &&
        segment.matrix.width <= 64 &&
        segment.matrix.height <= 64 &&
        segment.length <= 512 &&
        !segment.pixel_control_error
      ) {
        target.mode = "matrix";
        target.matrix.width = segment.matrix.width;
        target.matrix.height = segment.matrix.height;
        target.matrix.serpentine = false;
        for (const effect of Object.values(target.effects)) effect.effect = 0;
        target.phases.idle.effect = 0;
      }
      if (["white", "on_off"].includes(segment.color_mode)) {
        for (const style of Object.values(target.phases))
          style.color = "#ffffff";
        target.phases.idle.brightness = 16;
        target.effects = {};
      }
    } else if (
      current.targets.some(
        (item) => item.enabled && item.segment === target.segment,
      )
    )
      target.enabled = false;
    current.targets.push(target);
    selectedOutputs.set(current.id, "all");
    markChanged();
    render();
  });
  $("wled-reload").addEventListener("click", () => work(() => load(true)));
  $("wled-probe").addEventListener("click", () => {
    if (!$("wled-url").reportValidity()) return;
    collect();
    work(async () => {
      const url = $("wled-url").value.trim();
      const info = await api("/api/wled/probe", "POST", { url });
      capabilities.set(origin(url), info);
      render();
      notice(tr("WLED connection verified."));
    });
  });
  for (const on of [true, false]) {
    $(`wled-output-${on ? "on" : "off"}`).addEventListener("click", () =>
      work(async () => {
        const output = selectedOutputs.get(selected);
        const saved = latestStatus?.devices.find((item) => item.id === selected);
        const waking = on && saved?.on === false;
        const selectedOutput = deviceOutputs(deviceInfo()).find(
          (item) => String(item.id) === output,
        );
        if (waking && !confirm(
          tr("Turn on the device and {0} only? All other outputs will stay off.", {"0": (selectedOutput ? outputLabel(selectedOutput) : "this output")}),
        )) return;
        await api(`/api/wled/${selected}/outputs/${output}/power`, "POST", { on });
        notice(waking ? tr("Device and selected output on. Other outputs remain off.") : on ? tr("Output on.") : tr("Output off. Game events will keep it off."));
      }),
    );
    $(`wled-power-${on ? "on" : "off"}`).addEventListener("click", () =>
      work(async () => {
        const result = await api(`/api/wled/${selected}/power`, "POST", { on });
        if (!result.applied) throw new Error(result.devices[0].error);
        notice(
          on
            ? tr("Lights on. Saved automation resumes if enabled.")
            : tr("Lights off. Saved settings kept."),
        );
      }),
    );
  }
  const editor = {
    async prepare() {
      if (working) throw new Error(tr("Wait for the WLED action to finish."));
      await refreshing;
      clearTimeout(saveTimer);
      await saving;
      await saveDraft();
      if (changed)
        throw new Error(
          saveError || tr("Save or discard your WLED changes first."),
        );
    },
    lock() {
      [...form.elements].forEach((control) => {
        control.disabled = true;
      });
    },
    async reload(applied) {
      [...form.elements].forEach((control) => {
        control.disabled = false;
      });
      if (configuration && applied) await load(true);
      await refreshWled(true);
      buttons();
    },
  };
  registerProfileEditor("WLED", editor);
  registerLightingEditor("list", {
    prepare: () => editor.prepare(),
    async reload() { await refreshing; await load(true); await refreshWled(true); },
  });
  window.addEventListener("beforeunload", (event) => {
    if (changed) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  window.addEventListener("hashchange", refreshWled);
  poll(refreshWled);
})();
