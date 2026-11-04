import { tr } from "../js/i18n.mjs";

const editors = new Map();
let switching = false;
export function registerLightingEditor(view, editor) { editors.set(view, editor); }

// A view change finishes the outgoing draft before reading the shared configuration.
for (const button of document.querySelectorAll("[data-wled-view]")) {
  button.addEventListener("click", async () => {
    if (switching) return;
    const destination = button.dataset.wledView;
    const current = button.closest("[data-view]").dataset.view === "wled2" ? "visual" : "list";
    if (current === destination) return;
    switching = true;
    const outgoing = button.closest("[data-view]");
    outgoing.inert = true;
    const buttons = document.querySelectorAll("[data-wled-view]");
    buttons.forEach(item => { item.disabled = true; });
    const message = button.closest("[data-view]").querySelector("[data-wled-view-error]");
    message.hidden = true;
    try {
      await editors.get(current)?.prepare();
      await editors.get(destination)?.prepare();
      await editors.get(destination)?.reload();
      try { localStorage.setItem("oche.wled.view", destination); } catch { /* This visit only. */ }
      window.ocheNavigate(`/integrations/wled?view=${destination}`);
    } catch (cause) {
      message.textContent = cause.message;
      message.hidden = false;
    } finally {
      switching = false;
      outgoing.inert = false;
      buttons.forEach(item => { item.disabled = false; });
    }
  });
}

export function chooseLightShape() {
  const dialog = document.getElementById("wled-light-dialog");
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise(resolve => dialog.addEventListener("close", () => {
    resolve(["strip", "ring", "matrix"].includes(dialog.returnValue) ? dialog.returnValue : null);
  }, { once: true }));
}

export const lightShapeNames = () => ({ strip: tr("Strip"), ring: tr("Ring"), matrix: tr("Matrix") });

// Prefer the filtered output, but a requested matrix can use a suitable segment
// on another output instead of being assigned to a short ring segment.
export function lightSegments(shape, preferred, available = preferred) {
  const fits = segment => shape === "matrix" ? segment.matrix || segment.length >= 80 : !segment.matrix;
  const filtered = preferred.filter(fits);
  if (filtered.length) return filtered;
  const compatible = available.filter(fits);
  return compatible.length ? compatible : preferred;
}
