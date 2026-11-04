import { tr } from "/static/js/i18n.mjs";
import { $, api } from "/static/core/client.mjs";
import { action, notice } from "/static/core/app.js";

// The Autodarts account on Home: sign-in, board choice and the cloud connection.
// Home sends its status in an "oche:status" event; this module renders its own parts.
const fields = ["client_id", "board_id"];
const labels = {
  unconfigured: tr("Not configured"),
  disconnected: tr("Disconnected"),
  awaiting_authorization: tr("Awaiting approval"),
  authenticated: tr("Connected"),
  waiting_for_login: tr("Waiting for login"),
  connecting: tr("Connecting…"),
  connected: tr("Connected"),
  degraded: tr("Needs attention"),
  reconnecting: tr("Reconnecting…"),
  error: tr("Connection error"),
  stopped: tr("Stopped"),
};
let available = false;
let working = false;
let dirty = false;
let config = { client_id: "", board_id: "", locked_fields: [] };
let status = null;
let boardOptions = [];
let renderedBoards = "";
let boardLoading = false;
let nextBoardRefresh = 0;

const show = (id, text) => { if ($(id)) $(id).textContent = text; };

function badge(id, text, tone = "") {
  if (!$(id)) return;
  $(id).textContent = text;
  $(id).dataset.tone = tone;
}

function updateButtons() {
  const auth = status?.auth?.state ?? "unconfigured";
  fields.forEach((key) => {
    $(key).disabled = working || config.locked_fields.includes(key);
  });
  $("save").disabled = working || !available || !dirty;
  $("unsaved").hidden = !dirty;
  $("login").disabled =
    working || !available || dirty || !config.client_id || ["authenticated", "awaiting_authorization"].includes(auth);
  $("logout").disabled = working || !available || ["unconfigured", "disconnected"].includes(auth);
  $("refresh-boards").disabled = working || boardLoading || !available || auth !== "authenticated";
}

function applyConfig(value) {
  if (config.client_id !== value.client_id) {
    boardOptions = [];
    nextBoardRefresh = 0;
  }
  config = value;
  renderBoardOptions(dirty && !config.locked_fields.includes("board_id") ? $("board_id").value : config.board_id);
  fields.forEach((key) => {
    if (!dirty || config.locked_fields.includes(key)) $(key).value = config[key];
  });
  $("managed").textContent = config.locked_fields.length
    ? tr("Locked settings are managed by config/config.yaml or environment variables.")
    : tr("Use an OAuth client with device authorization enabled.");
}

function renderBoardOptions(selected) {
  const signature = JSON.stringify([boardOptions, selected]);
  if (signature === renderedBoards) return;
  renderedBoards = signature;
  const select = $("board_id");
  select.replaceChildren(new Option(tr("No board selected"), ""));
  boardOptions.forEach((board) => {
    const state = board.online === true ? tr("Online") : board.online === false ? tr("Offline") : tr("Status unknown");
    select.add(new Option(`${board.name} — ${state}`, board.id));
  });
  if (selected && !boardOptions.some((board) => board.id === selected)) {
    select.add(new Option(tr("Configured board ({0})", {"0": (selected)}), selected));
  }
  select.value = selected;
}

async function loadBoards() {
  if (boardLoading || status?.auth?.state !== "authenticated") return;
  boardLoading = true;
  nextBoardRefresh = Date.now() + 30000;
  $("boards-status").textContent = tr("Loading boards…");
  updateButtons();
  try {
    const result = await api("/api/autodarts/boards");
    boardOptions = result.boards;
    renderBoardOptions(dirty ? $("board_id").value : (result.selected_board_id ?? ""));
    $("boards-status").textContent = config.locked_fields.includes("board_id")
      ? tr("This board is managed by the service configuration.")
      : boardOptions.length
        ? tr("Choose your board, then save settings.")
        : tr("No boards found. Register a board on Autodarts, then refresh.");
    render();
  } catch (error) {
    $("boards-status").textContent = tr(error.message);
  } finally {
    boardLoading = false;
    updateButtons();
  }
}

function render() {
  const { auth, cloud } = status;
  const authLabel = labels[auth.state] ?? auth.state;
  badge("account-state", authLabel, auth.state === "authenticated" ? "good" : "");
  show("account-hint", !config.client_id
    ? tr("Set an OAuth Client ID in Advanced settings below to connect.")
    : auth.state === "authenticated"
      ? tr("Your account is connected. Select the board you want to use.")
      : auth.state === "awaiting_authorization"
        ? tr("Approve the connection using the code below.")
        : tr("Sign in on Autodarts to connect your board."));
  show("detail-board", config.board_id || tr("No board selected"));
  show("detail-match", cloud.match_id || tr("No active match"));
  const errors = [auth.error, cloud.error,
    ...(cloud.subscription_errors ?? []).map((item) => `${item.channel}: ${item.error}.`)].filter(Boolean);
  show("connection-error", errors.join(" ") || tr("No connection errors reported."));
  $("connection-warning").hidden = !errors.length;
  show("warning-text", auth.error || cloud.error || tr("Some Autodarts subscriptions need attention."));
  $("device").hidden = !auth.device;
  if (auth.device) {
    const device = auth.device;
    $("user-code").textContent = device.user_code;
    $("verification").removeAttribute("href");
    try {
      const url = new URL(device.verification_uri_complete || device.verification_uri);
      if (url.protocol === "https:") $("verification").href = url.href;
    } catch {
      /* Keep invalid approval links inactive. */
    }
    const seconds = Math.max(0, Math.ceil(device.expires_at - Date.now() / 1000));
    $("expires").textContent = tr("Code expires in {0} seconds.", {"0": (seconds)});
  }
}

function unavailable() {
  available = false;
  badge("account-state", tr("Unavailable"), "warning");
  show("warning-text", tr("Cannot reach Oche. Retrying automatically."));
  $("connection-warning").hidden = false;
  updateButtons();
}

document.addEventListener("oche:status", async (event) => {
  if (!event.detail?.auth) return unavailable();
  status = event.detail;
  try {
    applyConfig(await api("/api/autodarts/config"));
    available = true;
  } catch {
    return unavailable();
  }
  render();
  if (status.auth.state === "authenticated") {
    if (Date.now() >= nextBoardRefresh) await loadBoards();
  } else {
    boardOptions = [];
    nextBoardRefresh = 0;
    renderBoardOptions($("board_id").value);
    $("boards-status").textContent = tr("Connect your account to load boards.");
  }
  updateButtons();
});

// Actions run through Home, which refreshes its status (and so this card) afterwards.
async function run(work) {
  working = true;
  updateButtons();
  try { await action(work); }
  finally { working = false; updateButtons(); }
}

fields.forEach((key) =>
  $(key).addEventListener(key === "board_id" ? "change" : "input", () => {
    dirty = fields.some((field) => $(field).value.trim() !== config[field]);
    updateButtons();
  }),
);
$("configuration").addEventListener("submit", (event) => {
  event.preventDefault();
  run(async () => {
    await api("/api/autodarts/config", "PUT", Object.fromEntries(fields.map((key) => [key, $(key).value.trim()])));
    dirty = false;
    notice(tr("Settings saved."));
  });
});
$("login").addEventListener("click", () => run(async () => {
  await api("/api/autodarts/login", "POST");
  notice(tr("Approve the code on Autodarts."));
}));
$("logout").addEventListener("click", () => run(async () => {
  await api("/api/autodarts/logout", "POST");
  notice(tr("Account disconnected."));
}));
$("refresh-boards").addEventListener("click", () => run(loadBoards));
