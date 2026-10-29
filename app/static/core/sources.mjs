import { tr } from "../js/i18n.mjs";
/* Shared source switches; each integration keeps its own save workflow. */
export function sourceEnabled(config, source = "cloud") {
  return config?.[source === "local" ? "run_on_local" : "run_on_cloud"] !== false;
}

export function sourceControls(name, change) {
  const buttons = [...document.querySelectorAll(`[data-play-sources="${name}"] [data-play-source]`)];
  buttons.forEach(button => button.addEventListener("click", () => {
    if (!button.disabled) change(button.dataset.playSource, button.getAttribute("aria-checked") !== "true");
  }));
  return {
    render(config, disabled = false) {
      buttons.forEach(button => {
        button.disabled = !config || disabled;
        const enabled = !!config && config[button.dataset.playSource] !== false;
        button.setAttribute("aria-checked", String(enabled));
        button.querySelector("[data-play-source-state]").textContent = !config ? tr("Unavailable")
          : enabled ? tr("On") : tr("Off");
      });
    },
  };
}
