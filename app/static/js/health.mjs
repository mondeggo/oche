import { tr } from "./i18n.mjs";
/* Status descriptions shared by the sidebar and its checks. */
export function autodartsHealth(process) {
  if (!process) return ["bad", tr("Status unavailable"), tr("Cannot read Autodarts status. Check that the Oche host is reachable.")];
  if (process.status === "running") return ["good", tr("Running"), tr("The local Autodarts process is running.")];
  if (process.status === "nofile") return ["bad", tr("Not installed"), tr(process.unavailable_reason) || tr("Autodarts is not installed. Install it from System → Updates, or run ./oche.sh autodarts in the Oche folder.")];
  if (process.status === "stopped") return ["bad", tr("Stopped"), tr("The Autodarts process is stopped. Start it under Integrations → Autodarts; check its logs if it stops again.")];
  return ["bad", tr("Status unknown"), tr("Autodarts has not reported a recognized process state. Check Integrations → Autodarts.")];
}

export function localHealth(detector) {
  const tone = detector?.detecting ? "good"
    : ["starting", "calibrating", "waiting"].includes(detector?.state) ? "warn" : "bad";
  return [tone, detector?.detecting ? tr("Detecting") : tr("Not detecting"),
    tr(detector?.reason) || tr("Cannot read local detector readiness. Check Integrations → Autodarts.")];
}

export function cloudHealth(status) {
  if (!status?.auth || !status?.cloud) return ["bad", tr("Status unavailable"), tr("Cannot read the cloud connection status. Check that the Oche host is reachable.")];
  const { auth, cloud } = status;
  const errors = [...new Set([auth.error, cloud.error,
    ...(cloud.subscription_errors || []).map(item => item.error)].filter(Boolean).map(error => tr(error)))].join(" ");
  const detail = fallback => errors ? `${errors} ${fallback}` : fallback;
  if (cloud.state === "paused_for_local_game") return ["warn", tr("Paused for local play"), tr("Cloud events are intentionally paused while a local game is active. End local play to reconnect.")];
  if (auth.state === "unconfigured") return ["bad", tr("Not configured"), detail(tr("Set up the Autodarts account connection under Integrations."))];
  if (auth.state === "disconnected") return ["bad", tr("Signed out"), detail(tr("Connect your Autodarts account under Integrations."))];
  if (auth.state === "awaiting_authorization") return ["warn", tr("Sign-in approval needed"), detail(tr("Open Integrations and approve the Autodarts sign-in code."))];
  if (cloud.state === "unconfigured") return ["bad", tr("No board selected"), detail(tr("Choose your board under Integrations → Autodarts account."))];
  if (cloud.state === "degraded" || cloud.subscription_errors?.length) return ["bad", tr("Game events rejected"), detail(tr("Check the selected board and account access under Integrations."))];
  if (cloud.state === "connected") {
    if (cloud.board_online === false) return ["bad", tr("Board offline"), detail(tr("The cloud connection is open, but the selected board is offline. Check Autodarts and the board's network connection."))];
    if (errors) return ["bad", tr("Connection needs attention"), errors];
    return ["good", tr("Connected"), tr("The Autodarts cloud connection is open.")];
  }
  if (cloud.state === "reconnecting") return ["bad", tr("Disconnected; retrying"), detail(tr("Oche will reconnect automatically. Check internet access and the Autodarts account connection."))];
  if (cloud.state === "connecting" && !errors) return ["warn", tr("Connecting"), tr("Opening the Autodarts cloud connection.")];
  if (cloud.state === "waiting_for_login") return ["bad", tr("Waiting for sign-in"), detail(tr("Connect or reconnect your Autodarts account under Integrations."))];
  return ["bad", cloud.state === "stopped" ? tr("Stopped") : tr("Connection error"), detail(tr("Check internet access and review the account connection under Integrations."))];
}
