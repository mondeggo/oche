import { tr } from "../js/i18n.mjs";
import { $, api, poll } from "./client.mjs";
import { sourceControls } from "./sources.mjs";
import { notice, setIntegrationEnabled } from "./app.js";

(() => {
  let config = null,
    dirty = false,
    working = false,
    polling = null;
  let settingsTimer = null,
    settingsSave = null,
    settingsError = "";
  const pendingSettings = {};
  const sources = sourceControls("scoreboard", (key, value) => {
    pendingSettings[key] = value;
    dirty = true;
    settingsError = "";
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(saveSettings, 500);
    formState();
  });
  const flags = [
    "show_last_dart",
    "show_checkout",
    "show_sets_legs",
    "show_bots",
  ];

  function error(message = "") {
    for (const id of ["scoreboard-error", "scoreboard-integration-error"]) {
      $(id).textContent = message;
      $(id).hidden = !message;
    }
  }

  function formState() {
    sources.render(config && { ...config, ...pendingSettings }, working);
    $("scoreboard-form").inert = !config;
    $("scoreboard-autosave").textContent =
      settingsError ||
      (settingsSave
        ? tr("Saving settings…")
        : Object.keys(pendingSettings).length
          ? tr("Waiting to save settings…")
          : tr("Settings save automatically."));
    $("scoreboard-autosave").classList.toggle("error", !!settingsError);
    $("scoreboard-settings-retry").hidden = !settingsError;
  }

  function fill(value) {
    config = value;
    if (dirty) return;
    $("scoreboard-title").value = value.title;
    $("scoreboard-theme").value = value.theme;
    flags.forEach((key) => {
      $(`scoreboard-${key}`).checked = value[key];
    });
    formState();
  }

  async function saveSettings() {
    if (settingsSave || !Object.keys(pendingSettings).length) return;
    const values = { ...pendingSettings };
    Object.keys(pendingSettings).forEach((key) => delete pendingSettings[key]);
    settingsSave = api("/api/scoreboard", "PATCH", values)
      .then((saved) => {
        config = saved;
        settingsError = "";
      })
      .catch((cause) => {
        Object.assign(pendingSettings, values);
        settingsError = cause.message;
      })
      .finally(() => {
        settingsSave = null;
        dirty = !!Object.keys(pendingSettings).length;
        formState();
      });
    return settingsSave;
  }

  async function refreshScoreboard() {
    if (working || polling) return polling;
    polling = (async () => {
      try {
        const current = await api("/api/scoreboard");
        setIntegrationEnabled("scoreboard", current.enabled);
        $("scoreboard-toggle").disabled = false;
        $("scoreboard-toggle").setAttribute(
          "aria-checked",
          String(current.enabled),
        );
        $("scoreboard-toggle-label").textContent = current.enabled
          ? tr("On")
          : tr("Off");
        $("scoreboard-settings").hidden = !current.enabled;
        $("scoreboard-open").href = "/scoreboard";
        $("overview-scoreboard").textContent = current.enabled
          ? tr("Enabled")
          : tr("Disabled");
        error(current.error || "");
        fill(current);
      } catch (cause) {
        error(cause.message || tr("Cannot reach Scoreboard."));
        $("scoreboard-toggle").disabled = true;
        $("overview-scoreboard").textContent = tr("Unavailable");
      }
    })().finally(() => {
      polling = null;
    });
    return polling;
  }

  async function change(work) {
    if (working) return;
    working = true;
    clearTimeout(settingsTimer);
    $("scoreboard-toggle").disabled = true;
    try {
      await polling;
      await settingsSave;
      await work();
    } catch (cause) {
      notice(cause.message, true);
    } finally {
      working = false;
      await refreshScoreboard();
    }
  }

  $("scoreboard-toggle").addEventListener("click", () =>
    change(async () => {
      const saved = await api("/api/scoreboard", "PATCH", {
        enabled: !config.enabled,
      });
      fill(saved);
      setIntegrationEnabled("scoreboard", saved.enabled);
      if (saved.enabled) window.ocheNavigate("/integrations/scoreboard");
    }),
  );

  function editSettings(event) {
    const key = event.target.id.replace("scoreboard-", "");
    if (key === "title") pendingSettings.title = event.target.value;
    else if (key === "theme") pendingSettings.theme = event.target.value;
    else if (flags.includes(key)) pendingSettings[key] = event.target.checked;
    else return;
    settingsError = "";
    dirty = true;
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(saveSettings, 500);
    formState();
  }
  $("scoreboard-form").addEventListener("input", editSettings);
  $("scoreboard-form").addEventListener("change", editSettings);
  $("scoreboard-settings-retry").addEventListener("click", () => saveSettings());

  const prepareAppearance = window.ocheBeforeAppearanceChange;
  window.ocheBeforeAppearanceChange = async () => {
    await prepareAppearance?.();
    clearTimeout(settingsTimer);
    await settingsSave;
    await saveSettings();
    if (settingsError || Object.keys(pendingSettings).length)
      throw new Error(settingsError || tr("Settings could not be saved. Try again."));
  };
  poll(refreshScoreboard);
})();
