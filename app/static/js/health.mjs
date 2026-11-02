import { tr } from "./i18n.mjs";
/* Status descriptions shared by the sidebar and its checks. */
export function localHealth(detector) {
  const tone = detector?.detecting ? "good"
    : ["starting", "calibrating", "waiting"].includes(detector?.state) ? "warn" : "bad";
  return [tone, detector?.detecting ? tr("Detecting") : tr("Not detecting"),
    tr(detector?.reason) || tr("Cannot read local detector readiness. Check the vision engine's page in Integrations.")];
}
