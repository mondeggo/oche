import { tr } from "../js/i18n.mjs";
/* Shared transport and small UI helpers. Game and device decisions stay in the service. */
export const $ = (id) => document.getElementById(id);

export async function api(path, method = "GET", body, settings = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), settings.timeout ?? 30000);
  const options = { method, signal: controller.signal, headers: {} };
  if (settings.etag) options.headers["If-Match"] = settings.etag;
  if (method !== "GET") {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body ?? {});
  }
  try {
    const response = await fetch(path, options);
    let data;
    try {
      data = await response.json();
    } catch (cause) {
      if (controller.signal.aborted) throw cause;
      throw new Error(
        tr("The service returned an invalid response (HTTP {0}).", {"0": (response.status)}),
      );
    }
    if (!response.ok) {
      const detail = data?.detail;
      const message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail
                .map((item) => `${item.loc.slice(1).join(" / ")}: ${item.msg}`)
                .join("; ")
            : tr("Request rejected (HTTP {0}).", {"0": (response.status)});
      throw Object.assign(new Error(tr(message)), { status: response.status });
    }
    return settings.metadata
      ? { data, etag: response.headers.get("ETag") }
      : data;
  } catch (cause) {
    if (controller.signal.aborted) {
      throw new Error(
        tr("The service took too long to respond. Check its connection and try again."),
      );
    }
    throw cause;
  } finally {
    clearTimeout(timer);
  }
}

export function poll(callback, { background = false } = {}) {
  const tick = () => {
    if (background || !document.hidden) callback();
  };
  const timer = setInterval(tick, 2000);
  document.addEventListener("visibilitychange", tick);
  tick();
  return () => {
    clearInterval(timer);
    document.removeEventListener("visibilitychange", tick);
  };
}

let nextFrame = 0;

// A WLED 2D canvas that is twice as wide (or tall) as one panel is the classic split
// scoreboard. A panel that fills the whole canvas is split so a chained panel gets the free
// half, and both ends of the wire keep the same visual size. Returns "width", "height",
// "plain", or false when the panel does not fit into what is left of the canvas.
export function fitPanel(light, upstream, canvas) {
  const panel = upstream.matrix;
  const fills =
    panel.x === 0 && panel.y === 0 && panel.width === canvas.width && panel.height === canvas.height;
  let split = null;
  if (fills && canvas.width >= light.matrix.width + 11) split = "width";
  else if (fills && canvas.height >= light.matrix.height + 5) split = "height";
  if (split === "width") {
    panel.width = canvas.width - light.matrix.width;
    upstream.width = Math.max(20, upstream.width / 2);
    upstream.position.x = Math.max(0, upstream.position.x - upstream.width / 2);
  } else if (split === "height") {
    panel.height = canvas.height - light.matrix.height;
    upstream.height = Math.max(12, upstream.height / 2);
    upstream.position.y = Math.max(0, upstream.position.y - upstream.height / 2);
  }
  let x = panel.x + panel.width;
  let y = panel.y;
  if (x + light.matrix.width > canvas.width) {
    x = 0;
    y += panel.height;
  }
  if (y + light.matrix.height > canvas.height) return false;
  light.matrix.x = x;
  light.matrix.y = y;
  // Draw the chained panel beside the one it follows, at the same visual size.
  light.width = upstream.width;
  light.height = upstream.height;
  if (y === panel.y) {
    light.position.x = Math.min(1000, upstream.position.x + upstream.width);
    light.position.y = upstream.position.y;
  } else {
    light.position.x = upstream.position.x;
    light.position.y = Math.min(700, upstream.position.y + upstream.height);
  }
  return split || "plain";
}

export function eventSource(event, raw = false) {
  return (raw ? event?.source === "autodarts-local" || event?.channel === "local"
    : event?.data?.play_source === "local") ? "local" : "cloud";
}

export function eventEntries(events, raw, previous = []) {
  const signatures = events.map((event) => JSON.stringify(event));
  // The API returns a chronological sliding window. Match its retained suffix,
  // including duplicate frames, before assigning identities to new arrivals.
  let overlap = raw ? Math.min(previous.length, events.length) : 0;
  while (
    overlap &&
    !signatures
      .slice(0, overlap)
      .every(
        (signature, index) =>
          signature === previous[previous.length - overlap + index].signature,
      )
  ) {
    overlap -= 1;
  }
  return events.map((event, index) => {
    const signature = signatures[index];
    const key = raw
      ? index < overlap
        ? previous[previous.length - overlap + index].key
        : `raw-${++nextFrame}`
      : `event-${event.id}`;
    return { event, signature, key };
  });
}

export function callerTestState(
  status,
  { voiceChanged = false, dirty = false, working = false } = {},
) {
  const preparing = ["downloading", "installing"].includes(
    status?.download?.state,
  );
  if (voiceChanged)
    return {
      disabled: true,
      label: tr("Play test"),
      message: tr("Apply the selected voice above before testing it."),
    };
  if (preparing)
    return {
      disabled: true,
      label:
        status.download.state === "installing"
          ? tr("Installing voice…")
          : tr("Downloading voice…"),
      message:
        tr("Waiting for the selected voice package. Play test will be available when installation finishes."),
    };
  if (!status?.voice)
    return {
      disabled: true,
      label: tr("Play test"),
      message: tr("Choose and download a voice above."),
    };
  if (!status.installed)
    return {
      disabled: true,
      label: tr("Voice unavailable"),
      message: tr("Install or repair the selected voice package before testing."),
    };
  if (!status.enabled)
    return {
      disabled: true,
      label: tr("Play test"),
      message: tr("Enable Caller in Integrations to test this voice."),
    };
  if (dirty)
    return {
      disabled: true,
      label: tr("Saving settings…"),
      message: tr("Waiting for your settings to save."),
    };
  return {
    disabled: working,
    label: working ? tr("Please wait…") : tr("Play test"),
    message: tr("Uses the installed voice and your saved output settings."),
  };
}

const simulationDigits = {"DIGITS": {"0": [7, 5, 5, 5, 7], "1": [2, 6, 2, 2, 7], "2": [7, 1, 7, 4, 7], "3": [7, 1, 7, 1, 7], "4": [5, 5, 7, 1, 1], "5": [7, 4, 7, 1, 7], "6": [7, 4, 7, 5, 7], "7": [7, 1, 1, 1, 1], "8": [7, 5, 7, 5, 7], "9": [7, 5, 7, 1, 7], "-": [0, 0, 7, 0, 0]}, "LARGE_DIGITS": {"0": [6, 9, 9, 9, 9, 9, 9, 6], "1": [2, 6, 2, 2, 2, 2, 2, 7], "2": [6, 9, 1, 2, 4, 8, 8, 15], "3": [14, 1, 1, 6, 1, 1, 1, 14], "4": [9, 9, 9, 15, 1, 1, 1, 1], "5": [15, 8, 8, 14, 1, 1, 9, 6], "6": [6, 8, 8, 14, 9, 9, 9, 6], "7": [15, 1, 1, 2, 2, 4, 4, 4], "8": [6, 9, 9, 6, 9, 9, 9, 6], "9": [6, 9, 9, 7, 1, 1, 1, 6], "-": [0, 0, 0, 15, 0, 0, 0, 0]}};

// Return logical panel pixels, matching the service's score font and digit rotation.
export function simulationFace(matrix, value) {
  const { width, height, rotation } = matrix;
  const [w, h] = [90, 270].includes(rotation) ? [height, width] : [width, height];
  let text = String(value);
  if (text.length * 4 - 1 > w) text = "---";
  const large = h >= 8 && w >= Math.max(3, text.length) * 5 - 1;
  const font = simulationDigits[large ? "LARGE_DIGITS" : "DIGITS"];
  const gw = large ? 4 : 3, gh = large ? 8 : 5;
  const face = Array.from({ length: height }, () => Array(width).fill("."));
  const ox = Math.floor((w - (text.length * (gw + 1) - 1)) / 2);
  const oy = Math.floor((h - gh) / 2);
  [...text].forEach((digit, i) => font[digit].forEach((bits, row) => {
    for (let col = 0; col < gw; col++) {
      if (!(bits & (1 << (gw - 1 - col)))) continue;
      let x = ox + i * (gw + 1) + col, y = oy + row;
      if (rotation === 90) [x, y] = [width - 1 - y, x];
      else if (rotation === 180) [x, y] = [width - 1 - x, height - 1 - y];
      else if (rotation === 270) [x, y] = [y, height - 1 - x];
      if (face[y]?.[x] !== undefined) face[y][x] = "1";
    }
  }));
  return face.map(row => row.join(""));
}


export function simulationGame() {
  const scores = [501, 501], steps = [];
  let player = 0, total = 0, last = 0, visit = 0;
  const emit = (event, phase) => steps.push({ event, phase,
    remaining: scores[player], local_remaining: scores[0], opponent_remaining: scores[1],
    turn_score: total, last_dart: last, player, round: Math.floor(visit / 2) + 1 });
  emit("match_started", "ready");
  const visits = [
    [[60, "triple"], [60, "triple"], [60, "triple"]],
    [[60, "triple"], [20, "single"], [20, "single"]],
    [[60, "triple"], [60, "triple"], [60, "triple"]],
    [[60, "triple"], [60, "triple"], [60, "triple"]],
    [[60, "triple"], [60, "triple"], [25, "outer_bull"]],
    [[60, "triple"], [20, "single"], [20, "single"]],
    [[60, "triple"], [57, "triple"], [24, "double"]],
  ];
  for (const darts of visits) {
    total = last = 0;
    const start = scores[player];
    emit("turn_started", "ready");
    for (const [value, event] of darts) {
      last = value; total += value;
      const next = scores[player] - value;
      if (next < 0 || next === 1 || (next === 0 && event !== "double" && event !== "bull")) {
        scores[player] = start; total = 0;
        emit("bust", "takeout"); break;
      }
      scores[player] = next;
      emit(event, "ready");
    }
    if (total === 180) emit("score_180", "takeout");
    if (scores[player] === 0) {
      emit("leg_win", "takeout"); emit("match_win", "waiting");
      emit("match_ended", "idle"); break;
    }
    emit("takeout_started", "takeout");
    emit("takeout_finished", "waiting");
    player = 1 - player; visit++;
  }
  return steps;
}

// Approximate the spatial rainbow and colour cycle from WLED's effect list.
export function simulationPixel(style, elapsed, index, count) {
  const position = index / Math.max(1, count);
  const hue = ((elapsed * 60 + (style.effect === 8 ? 0 : position * 360)) % 360 + 360) % 360;
  const rainbow = `hsl(${hue} 100% 50%)`;
  let color = style.color, strength = 1;
  if (style.effect === 8 || style.effect === 9) color = rainbow;
  else if (style.effect === 33) {
    const head = (elapsed * .3) % 1;
    if ((position - head + 1) % 1 < 2 / Math.max(2, count)) color = rainbow;
  } else if (style.effect) strength = .25 + .75 * (1 + Math.sin(elapsed * 9 - index * .5)) / 2;
  return { color, opacity: style.brightness / 255 * strength };
}
