import { tr } from "../js/i18n.mjs";
import { $, api, poll } from "./client.mjs";
import { sourceEnabled } from "./sources.mjs";

let config = null;
let renderedPlayers = "";

function banner(text) {
  $("board-banner").textContent = text;
  $("board-banner").hidden = !text;
  $("board-players").hidden = !!text;
  $("board-checkout").hidden = true;
}

function playerCard(player, cfg) {
  const card = document.createElement("div");
  card.className = "board-player";
  card.dataset.active = String(player.active);
  const name = document.createElement("p");
  name.className = "board-player-name";
  name.textContent = player.name || tr("Player {0}", {"0": (player.index + 1)});
  const score = document.createElement("p");
  score.className = "board-player-score";
  score.textContent = player.remaining ?? player.game_score ?? "–";
  const detail = document.createElement("p");
  detail.className = "board-player-detail";
  const parts = [];
  if (player.active && cfg.show_last_dart && player.last_dart != null)
    parts.push(tr("Last dart: {0}", {"0": (player.last_dart)}));
  if (player.active && player.turn_score)
    parts.push(tr("This turn: {0}", {"0": (player.turn_score)}));
  detail.textContent = parts.join(" · ");
  card.append(name, score, detail);
  return card;
}

function render(game) {
  const players = (game.players || []).filter(
    (player) => config.show_bots || !player.is_bot,
  );
  const withDetail = players.map((player) => ({
    ...player,
    last_dart: player.active ? game.last_dart : null,
    turn_score: player.active ? game.turn_score : null,
  }));
  const signature = JSON.stringify([withDetail, config]);
  if (signature === renderedPlayers) return;
  renderedPlayers = signature;
  $("board-players").replaceChildren(
    ...withDetail.map((player) => playerCard(player, config)),
  );
  $("board-checkout").hidden = !(config.show_checkout && game.checkout_available);
  $("board-checkout").textContent = tr("Checkout available");
}

function applyTheme(uiTheme) {
  if (config.theme !== "auto") return;
  document.documentElement.dataset.theme = uiTheme === "light" ? "light" : "dark";
}

async function refresh() {
  try {
    const [scoreboard, game, ui] = await Promise.all([
      api("/api/scoreboard"),
      api("/api/game"),
      api("/api/ui"),
    ]);
    config = scoreboard;
    applyTheme(ui.theme);
    $("board-title").textContent = config.title;
    $("board-title").hidden = !config.title;
    if (!config.enabled) {
      banner(tr("Scoreboard is turned off. Enable it in Oche → Integrations."));
      return;
    }
    if (!sourceEnabled(config, game.source)) {
      banner(tr("Scoreboard is disabled for {0}.", {"0": (game.source === "local" ? "local play" : tr("Autodarts Play"))}));
      return;
    }
    if (!game.available) {
      banner(tr("Board unavailable."));
      return;
    }
    if (game.phase === "idle") {
      banner(tr("No active match."));
      return;
    }
    if (!game.players.length) {
      banner(game.reason || tr("Waiting for match state…"));
      return;
    }
    banner("");
    $("board-match").textContent =
      config.show_sets_legs && game.variant
        ? `${game.variant} · Set ${game.set + 1} · Leg ${game.leg + 1}`
        : game.variant || "";
    render(game);
  } catch {
    banner(tr("Cannot reach Oche. Retrying automatically…"));
  }
}

poll(refresh);
