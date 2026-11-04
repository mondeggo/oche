import { tr } from "../js/i18n.mjs";
import { $, api, poll, eventEntries, eventSource } from "./client.mjs";
import { pageForUrl } from "../js/routes.mjs";
const pages = {
  integrations: [
    tr("Integrations"),
    tr("Choose your vision engines, lights and sound."),
  ],
  profiles: [tr("Profiles"), tr("Create, switch between and manage app profiles.")],
  wled: ["WLED", tr("Lighting effects for your game.")],
  wled2: ["Visual WLED", tr("Build your lighting setup around the board.")],
  caller: [tr("Caller"), tr("Choose a voice and where to hear your game.")],
  events: [tr("Events"), tr("See what Oche receives from your board.")],
};
export let page = "integrations";
let stream = "normalized";
let refreshTask = null;
let busy = false;
let available = false;
let status = null;
let renderedEvents = "";
let entries = [];
let eventElements = new Map();
let eventsPaused = false;
let eventRequest = 0;
let debugState = null;
let debugBusy = false;

function navigate(focus = false) {
  if (location.hash === "#main") return;
  const requested = pageForUrl(new URL(location.href));
  // Integrations add views that carry their own title and description.
  const registered = document.querySelector(`[data-view="${CSS.escape(requested)}"][data-title]`);
  page = Object.hasOwn(pages, requested) || registered ? requested : "integrations";
  const [title, description] = pages[page] || [registered.dataset.title, registered.dataset.description];
  document.querySelectorAll("[data-view]").forEach((view) => {
    view.hidden = view.dataset.view !== page;
  });
  document.querySelectorAll("[data-page]").forEach((link) => {
    const sub = (link.dataset.subpages || "").split(" ").includes(page);
    if (link.dataset.page === page || sub) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $("page-title").textContent = title;
  $("page-description").textContent = description;
  document.title = `${title} \u00b7 Oche`;
  document.dispatchEvent(new CustomEvent("oche:navigate", {detail: page}));
  if (focus) $("page-title").focus();
  eventRequest += 1;
  refreshEvents();
}

export function setIntegrationEnabled(name, enabled) {
  document.querySelector(`[data-integration="${name}"]`).hidden = !enabled && name !== "wled2";
}

export function notice(message, error = false) {
  $("notice-text").textContent = tr(message);
  $("notice").className = error ? "notice error" : "notice";
  $("notice").hidden = false;
}

// Integrations render their own parts from this event; null means status is unavailable.
function renderStatus() {
  const { events } = status;
  document.dispatchEvent(new CustomEvent("oche:status", { detail: status }));
  renderDebug(events.debug);
  $("events-summary").textContent = tr("{count} game events · {raw} raw frames", { count: events.normalized, raw: events.received });
  $("local-feed-status").textContent = status.local.feed.error ? tr(status.local.feed.error)
    : status.local.feed.transport ? tr("Local detector feed connected.")
    : tr("Open Events or enable recording to inspect the local detector feed.");
}

function renderDebug(value) {
  debugState = value || null;
  $("debug-toggle").disabled = debugBusy || !debugState;
  $("debug-toggle").setAttribute("aria-checked", String(!!value?.enabled));
  $("debug-toggle-label").textContent = value?.enabled ? tr("Recording") : tr("Off");
  $("debug-status").textContent = value?.file
    ? `${value.enabled ? "Recording" : "Saved"}: ${value.file}${value.recorded == null ? "" : ` · ${value.recorded} raw frames`}${value.enabled ? " · Stop to download." : ""}`
    : tr("No recording yet. Recording starts with the next incoming frame.");
  $("debug-error").hidden = !value?.error;
  $("debug-error").textContent = tr(value?.error) || "";
  $("debug-download").hidden =
    !value?.download_url || value.enabled || value.pending > 0;
}

function renderEvents(events) {
  if (events) entries = eventEntries(events, stream === "raw", entries);
  const filter = $("event-filter").value.trim().toLowerCase();
  const source = $("event-source").value;
  const visible = entries.filter(({ event }) =>
    (source === "all" || eventSource(event, stream === "raw") === source) &&
    (stream === "raw"
      ? [event?.source, event?.channel, event?.topic, event?.type, event?.data?.event, event?.data?.status].filter(Boolean).join(" ")
      : event.event
    )
      .toLowerCase()
      .includes(filter),
  );
  const signature = `${stream}:${source}:${filter}:${JSON.stringify(entries)}`;
  if (signature === renderedEvents) return;
  renderedEvents = signature;
  const list = $("event-list");
  const retained = new Set(entries.map((entry) => entry.key));
  for (const key of eventElements.keys()) {
    if (!retained.has(key)) eventElements.delete(key);
  }
  const nodes = visible.map(({ event, key }) => {
    if (eventElements.has(key)) return eventElements.get(key);
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const pre = document.createElement("pre");
    const origin = document.createElement("span");
    origin.className = "badge";
    origin.textContent = tr(eventSource(event, stream === "raw") === "local" ? "Local" : "Cloud");
    summary.append(origin, " ");
    details.dataset.key = key;
    if (stream === "normalized") {
      const time = document.createElement("span");
      time.className = "event-time";
      time.textContent = new Date(event.received_at).toLocaleTimeString(
        "en-GB",
      );
      summary.append(time, event.event.replaceAll("_", " "));
    } else {
      summary.append(
        [event?.channel, event?.topic, event?.type, event?.data?.event]
          .filter(Boolean)
          .join(" · ") || tr("Autodarts frame"));
    }
    pre.textContent = JSON.stringify(event, null, 2);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "event-copy";
    copy.textContent = tr("Copy JSON");
    copy.addEventListener("click", async () => {
      try {
        if (navigator.clipboard)
          await navigator.clipboard.writeText(pre.textContent);
        else {
          const text = document.createElement("textarea");
          text.value = pre.textContent;
          text.className = "clipboard-buffer";
          document.body.append(text);
          text.select();
          const copied = document.execCommand("copy");
          text.remove();
          copy.focus();
          if (!copied) throw new Error(tr("Clipboard unavailable"));
        }
        notice(tr("Event copied."));
      } catch {
        notice(tr("Could not copy. Select and copy the JSON below."), true);
      }
    });
    details.append(summary, copy, pre);
    eventElements.set(key, details);
    return details;
  });
  list.replaceChildren(...nodes.reverse());
  $("event-count").textContent =
    `${visible.length} of ${entries.length} entries`;
  $("empty").hidden = entries.length > 0;
  $("event-no-match").hidden = !entries.length || !!visible.length;
}

async function refreshEvents() {
  if (page !== "events" || $("oche-page")?.hidden || window.frameElement?.hidden) return;
  const request = ++eventRequest;
  try {
    const events = await api(
      (stream === "raw" ? "/api/events/raw" : "/api/events") + "?monitor_local=true",
    );
    if (request !== eventRequest || eventsPaused) return;
    renderEvents(events);
    $("event-error").hidden = true;
  } catch {
    if (request !== eventRequest || eventsPaused) return;
    $("event-error").textContent =
      tr("Cannot update events. Retrying automatically; displayed entries may be out of date.");
    $("event-error").hidden = false;
  }
}

async function refreshData() {
  try {
    const newStatus = await api("/api/status");
    available = true;
    status = newStatus;
    renderStatus();
  } catch {
    available = false;
    document.dispatchEvent(new CustomEvent("oche:status", { detail: null }));
    $("debug-toggle").disabled = true;
    $("events-summary").textContent = tr("Event status unavailable.");
  }
  await refreshEvents();
}

function refresh() {
  if (busy) return;
  if (!refreshTask)
    refreshTask = refreshData().finally(() => {
      refreshTask = null;
    });
  return refreshTask;
}

export async function action(work) {
  if (busy) return;
  busy = true;
  try {
    await refreshTask;
    await work();
  } catch (error) {
    notice(error.message, true);
  } finally {
    busy = false;
    await refresh();
  }
}

$("dismiss-notice").addEventListener("click", () => {
  $("notice").hidden = true;
});

$("debug-toggle").addEventListener("click", async () => {
  if (busy || debugBusy || !debugState) return;
  const enabled = !debugState.enabled;
  debugBusy = true;
  renderDebug(debugState);
  try {
    await action(async () => {
      renderDebug(await api("/api/events/debug", "PUT", { enabled }));
      notice(
        enabled
          ? tr("Raw-event recording started.")
          : tr("Recording stopped. The debug file is saved."),
      );
    });
  } finally {
    debugBusy = false;
    $("debug-toggle").disabled = !available || !debugState;
  }
});

document.querySelectorAll("[data-stream]").forEach((button) =>
  button.addEventListener("click", () => {
    if (stream === button.dataset.stream) return;
    stream = button.dataset.stream;
    document.querySelectorAll("[data-stream]").forEach((item) => {
      item.setAttribute("aria-pressed", String(item.dataset.stream === stream));
    });
    $("stream-description").textContent =
      stream === "raw"
        ? tr("Incoming cloud and local detector frames, newest first. Known credential fields are redacted.")
        : tr("Normalized game events, newest first. Open an event to see its payload.");
    $("empty").querySelector("h2").textContent =
      stream === "raw" ? tr("No Autodarts frames yet") : tr("No game events yet");
    $("empty").querySelector("p").textContent =
      stream === "raw"
        ? tr("Frames appear when the connected Autodarts stream sends data.")
        : tr("Start a cloud or local game. Use Raw events to inspect the detector without a game.");
    $("event-list").replaceChildren();
    entries = [];
    eventElements.clear();
    renderedEvents = "";
    $("event-filter").value = "";
    eventsPaused = false;
    $("events-pause").textContent = tr("Pause display");
    $("events-pause").setAttribute("aria-pressed", "false");
    $("events-display-state").textContent =
      tr("Latest 100 entries from this service session. Updates automatically.");
    renderEvents([]);
    refreshEvents();
  }),
);
$("event-filter").addEventListener("input", () => renderEvents());
$("event-source").addEventListener("change", () => renderEvents());
$("events-pause").addEventListener("click", () => {
  eventsPaused = !eventsPaused;
  eventRequest += 1;
  $("events-pause").textContent = eventsPaused
    ? tr("Resume display")
    : tr("Pause display");
  $("events-pause").setAttribute("aria-pressed", String(eventsPaused));
  $("events-display-state").textContent = eventsPaused
    ? tr("Display paused. The service and debug recording keep running.")
    : tr("Latest 100 entries from this service session. Updates automatically.");
  if (!eventsPaused) refreshEvents();
});
window.addEventListener("hashchange", () => navigate(true));
window.addEventListener("oche:route", () => navigate(true));
navigate();
poll(refresh);

// Profiles belong to the service; editors finish their current save before switching.
export let profileChanging = false;
const profileEditors = new Map();
window.ocheBeforeAppearanceChange = async () => {
  for (const editor of profileEditors.values()) await editor.prepare();
};
let profiles = null;
let profileRequest = null;
let removal = null;
let importedProfile = null;
export function registerProfileEditor(name, editor) {
  profileEditors.set(name, editor);
}
function profileRow(item) {
  const row = document.createElement("div");
  row.className = "integration-row";
  row.dataset.profileId = item.id;
  const active = item.id === profiles.active_profile;

  const description = document.createElement("div");
  description.className = "integration-description";
  const heading = document.createElement("h2");
  heading.textContent = item.name;
  if (active) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.dataset.tone = "good";
    badge.textContent = tr("Active");
    heading.append(badge);
  }
  description.append(heading);

  const controls = document.createElement("div");
  controls.className = "integration-controls";
  if (!active) {
    const use = document.createElement("button");
    use.type = "button";
    use.dataset.action = "use";
    use.textContent = tr("Use this profile");
    controls.append(use);
  }
  const exportButton = document.createElement("button");
  exportButton.type = "button";
  exportButton.dataset.action = "export";
  exportButton.textContent = tr("Export");
  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "danger";
  removeButton.dataset.action = "remove";
  removeButton.textContent = tr("Remove…");
  if (profiles.profiles.length <= 1) {
    removeButton.title = tr("Keep at least one profile.");
  }
  controls.append(exportButton, removeButton);

  row.append(description, controls);
  return row;
}
function renderProfiles() {
  if (!profiles) return;
  const select = $("app-profile");
  const signature = JSON.stringify(profiles);
  if (select.dataset.signature !== signature) {
    select.replaceChildren(
      ...profiles.profiles.map((item) => new Option(item.name, item.id)),
    );
    select.value = profiles.active_profile;
    select.dataset.signature = signature;
    $("profile-list").replaceChildren(
      ...profiles.profiles.map((item) => profileRow(item)),
    );
  }
  for (const id of [
    "app-profile",
    "profile-create",
    "profile-blank",
    "profile-name",
    "profile-confirm-remove",
    "profile-choose-file",
    "profile-import",
    "profile-import-name",
    "profile-import-cancel",
  ])
    $(id).disabled = profileChanging;
  document.querySelectorAll("#profile-list button[data-action]").forEach((button) => {
    button.disabled =
      profileChanging ||
      (button.dataset.action === "remove" && profiles.profiles.length <= 1);
  });
  $("profile-create").disabled ||= profiles.profiles.length >= 12;
  $("profile-blank").disabled ||= profiles.profiles.length >= 12;
  $("profile-choose-file").disabled ||= profiles.profiles.length >= 12;
  $("profile-import").disabled ||= !importedProfile || profiles.profiles.length >= 12;
}
async function refreshProfiles() {
  if (profileChanging || profileRequest) return profileRequest;
  profileRequest = (async () => {
    try {
      profiles = await api("/api/profiles");
      renderProfiles();
    } catch {
      $("app-profile").disabled = true;
    } finally {
      profileRequest = null;
    }
  })();
  return profileRequest;
}
async function changeProfile(path, method, body, expected) {
  if (profileChanging) return;
  profileChanging = true;
  renderProfiles();
  $("profiles-error").hidden = true;
  // Inert blocks new edits while pending autosaves complete.
  document
    .querySelectorAll(
      '[data-view="wled"], [data-view="caller"], [data-view="integrations"]',
    )
    .forEach((view) => {
      view.inert = true;
    });
  let applied = false;
  try {
    await profileRequest;
    await window.ocheTheme?.prepare();
    await window.ocheTheme?.flush();
    for (const editor of profileEditors.values()) editor.lock();
    const latest = await api("/api/profiles", "GET", undefined, {
      metadata: true,
    });
    if (expected && latest.data.active_profile !== expected)
      throw new Error(
        tr("The active profile changed in another client. Review the selected profile and try again."),
      );
    const result = await api(path, method, body, { etag: latest.etag });
    if (method === "GET") {
      const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2) + "\n"],
        { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${result.name.replace(/[^a-z0-9_-]+/gi, "-") || "oche"}.profile.json`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      notice(tr("Profile file downloaded."));
      return;
    }
    applied = true;
    window.ocheTheme?.applyProfile(result.appearance, result.active_profile);
    if (path === "/api/profiles/import") resetProfileImport();
    $("profile-name").value = "";
    $("profile-confirm").close();
    notice(
      method === "DELETE"
        ? tr("Profile removed. Remaining profile settings applied.")
        : tr("App profile applied to lighting, Caller, theme and language."),
    );
  } catch (error) {
    notice(error.message, true);
    $("profiles-error").textContent = tr(error.message);
    $("profiles-error").hidden = false;
    $("profile-confirm").close();
  } finally {
    for (const editor of profileEditors.values()) {
      try {
        await editor.reload(applied);
      } catch (error) {
        notice(error.message, true);
      }
    }
    profileChanging = false;
    document
      .querySelectorAll(
        '[data-view="wled"], [data-view="caller"], [data-view="integrations"]',
      )
      .forEach((view) => {
        view.inert = false;
      });
    await refreshProfiles();
    // Restore the selection if switching was rejected before applying.
    if (profiles) $("app-profile").value = profiles.active_profile;
  }
}
function resetProfileImport() {
  importedProfile = null;
  $("profile-file").value = "";
  $("profile-import-preview").hidden = true;
}
$("profile-choose-file").addEventListener("click", () => $("profile-file").click());
$("profile-import-cancel").addEventListener("click", () => {
  resetProfileImport();
  renderProfiles();
});
$("profile-file").addEventListener("change", async () => {
  const file = $("profile-file").files[0];
  if (!file) return;
  importedProfile = null;
  $("profile-import-preview").hidden = true;
  $("profiles-error").hidden = true;
  try {
    if (file.size > 2 * 1024 * 1024) throw new Error(tr("Choose a profile file smaller than 2 MB."));
    const value = JSON.parse((await file.text()).replace(/^\uFEFF/, ""));
    if (value?.format !== "oche-profile" || value.version !== 1 ||
        typeof value.name !== "string" || !value.name.trim() || value.name.length > 60 ||
        !Array.isArray(value.zones) || !value.caller)
      throw new Error(tr("Choose a profile JSON file downloaded from Oche (version 1)."));
    importedProfile = value;
    $("profile-import-name").value = value.name;
    $("profile-file-summary").textContent = `${file.name} · ${value.zones.length} lighting zones · Caller settings included`;
    $("profile-import-preview").hidden = false;
    $("profile-import-name").focus();
  } catch (error) {
    $("profiles-error").textContent = error instanceof SyntaxError
      ? tr("This file is not valid JSON. Choose a profile downloaded from Oche.") : error.message;
    $("profiles-error").hidden = false;
  } finally {
    $("profile-file").value = "";
    renderProfiles();
  }
});
$("profile-import").addEventListener("click", () => {
  const name = $("profile-import-name").value.trim();
  if (!name) return $("profile-import-name").focus();
  if (importedProfile)
    changeProfile("/api/profiles/import", "POST", { name, profile: importedProfile },
      profiles.active_profile);
});
$("app-profile").addEventListener("change", () =>
  changeProfile(
    "/api/profile",
    "PUT",
    { id: $("app-profile").value },
    profiles.active_profile,
  ),
);
for (const [id, source] of [
  ["profile-create", "current"],
  ["profile-blank", "blank"],
]) {
  $(id).addEventListener("click", () => {
    const name = $("profile-name").value.trim();
    if (!name) return $("profile-name").focus();
    changeProfile(
      "/api/profiles",
      "POST",
      { name, source },
      profiles.active_profile,
    );
  });
}
$("profile-list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button || profileChanging) return;
  const id = button.closest("[data-profile-id]").dataset.profileId;
  const item = profiles.profiles.find((p) => p.id === id);
  if (!item) return;
  if (button.dataset.action === "use") {
    changeProfile("/api/profile", "PUT", { id }, profiles.active_profile);
  } else if (button.dataset.action === "export") {
    // Exporting any profile is a read; no active-profile staleness check applies.
    changeProfile(`/api/profiles/${encodeURIComponent(id)}/export`, "GET", undefined, null);
  } else if (button.dataset.action === "remove") {
    removal = item;
    $("profile-confirm-title").textContent = tr("Remove \"{0}\"?", {"0": (item.name)});
    const fallback = profiles.profiles.find((p) => p.id !== item.id);
    $("profile-confirm-description").textContent =
      item.id === profiles.active_profile
        ? `"${fallback.name}" will become active. This cannot be undone.`
        : tr("This cannot be undone.");
    $("profile-confirm").showModal();
  }
});
$("profile-cancel").addEventListener("click", () =>
  $("profile-confirm").close(),
);
$("profile-confirm-remove").addEventListener("click", () => {
  if (removal)
    // Removing a profile is valid regardless of which one is currently active.
    changeProfile(
      `/api/profiles/${encodeURIComponent(removal.id)}`,
      "DELETE",
      undefined,
      null,
    );
});
poll(refreshProfiles);
