import { tr } from "/static/js/i18n.mjs";
import { $, api, poll } from "/static/core/client.mjs";

// This switch shares Settings' visibility preference; detection stays independent.
const toggle = $("cameras-toggle");
let working = false, loading = false, pendingSave = null, revision = 0;
function render(config) {
  const enabled = config.show_page_cameras !== false;
  toggle.setAttribute("aria-checked", String(enabled));
  $("cameras-toggle-label").textContent = enabled ? tr("On") : tr("Off");
  $("nav-link-cameras").hidden = !enabled;
  $("cameras-settings").hidden = !enabled;
}
function error(message = "") {
  $("cameras-integration-error").textContent = message;
  $("cameras-integration-error").hidden = !message;
}
async function refresh() {
  if (working || loading) return;
  loading = true;
  const started = revision;
  try {
    const config = await api("/config/data");
    if (working || started !== revision) return;
    render(config);
    toggle.disabled = false;
    error();
  } catch (cause) {
    if (working || started !== revision) return;
    toggle.disabled = true;
    $("cameras-toggle-label").textContent = tr("Unavailable");
    error(cause.message);
  } finally { loading = false; }
}
toggle.addEventListener("click", () => {
  if (working || toggle.disabled) return;
  revision++;
  working = toggle.disabled = true;
  const enabled = toggle.getAttribute("aria-checked") !== "true";
  pendingSave = (async () => {
    try {
      render(await api("/config/data", "POST", { show_page_cameras: enabled }));
      error();
    } catch (cause) { error(cause.message); }
    finally { working = toggle.disabled = false; }
  })();
});
poll(refresh);
const prepareAppearance = window.ocheBeforeAppearanceChange;
window.ocheBeforeAppearanceChange = async () => {
  await prepareAppearance?.();
  await pendingSave;
};
