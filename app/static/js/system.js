import { tr } from "./i18n.mjs";
import { api } from "../core/client.mjs";
import { metricSeries } from "./metrics.mjs";

const $ = id => document.getElementById(id);
const form = $("history-form");
let history = null, dirty = false, saving = null, refreshing = false, nextHistory = 0;
const charts = {
  cpu: { fields: ["cpu_percent"], unit: "%", max: 100, label: "CPU" },
  mem: { fields: ["mem_percent"], unit: "%", max: 100, label: tr("Memory") },
  disk: { fields: ["disk_percent"], unit: "%", max: 100, label: tr("Storage") },
  temp: { fields: ["cpu_temp_c"], unit: "°C", label: tr("Temperature") },
  io: { fields: ["disk_read_mb_s", "disk_write_mb_s"], unit: " MB/s", label: tr("Disk activity") },
  net: { fields: ["net_recv_mb_s", "net_sent_mb_s"], unit: " MB/s", label: tr("Network") },
};
const ns = "http://www.w3.org/2000/svg";
function svgElement(tag, attributes) {
  const element = document.createElementNS(ns, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}
function draw(key) {
  const box = $("spark-" + key), chart = charts[key];
  box.hidden = !history?.config.enabled;
  if (box.hidden) { box.replaceChildren(); return; }
  const series = metricSeries(history.samples, chart.fields, history.config.retention_minutes * 60, history.now ?? Date.now() / 1000, chart.max);
  const svg = svgElement("svg", { viewBox: "0 0 100 40", preserveAspectRatio: "none", "aria-hidden": "true" });
  svg.append(svgElement("line", { class: "spark-base", x1: 0, x2: 100, y1: 39, y2: 39 }));
  for (const [index, item] of series.entries()) {
    if (!item.points.length) continue;
    svg.append(svgElement("polyline", { class: "spark-line" + (index ? " spark-secondary" : ""),
      points: item.points.map(point => `${point.x.toFixed(3)},${point.y.toFixed(3)}`).join(" ") }));
    const last = item.points.at(-1);
    svg.append(svgElement("circle", { class: "spark-dot", cx: last.x, cy: last.y, r: 1.5, "vector-effect": "non-scaling-stroke" }));
  }
  box.replaceChildren(svg);
  box.setAttribute("aria-label", `${chart.label}: ${$("history-summary").title}`);
}
function showHistory(value) {
  history = value;
  if (!saving) $("history-save").disabled = false;
  if (!dirty) {
    $("history-enabled").checked = value.config.enabled;
    $("history-minutes").value = value.config.retention_minutes;
    $("history-seconds").value = value.config.sample_seconds;
  }
  const description = value.error ? tr(value.error)
    : !value.config.enabled ? tr("History is off.")
    : tr("{count} samples · {minutes} minutes · every {seconds} seconds", {
      count: value.samples.length, minutes: value.config.retention_minutes, seconds: value.config.sample_seconds,
    });
  $("history-summary").textContent = value.error ? tr(value.error)
    : value.config.enabled ? tr("{count} samples", { count: value.samples.length }) : tr("Off");
  $("history-summary").title = description;
  $("history-summary").setAttribute("aria-label", description);
  Object.keys(charts).forEach(draw);
}
for (const [key, chart] of Object.entries(charts)) {
  const box = $("spark-" + key);
  box.addEventListener("pointermove", event => {
    if (!history?.samples.length) return;
    const rect = box.getBoundingClientRect();
    const timestamp = (history.now ?? Date.now() / 1000) - (1 - (event.clientX - rect.left) / rect.width) * history.config.retention_minutes * 60;
    const sample = history.samples.reduce((best, item) => Math.abs(item.timestamp - timestamp) < Math.abs(best.timestamp - timestamp) ? item : best);
    let tip = box.querySelector(".spark-tip");
    if (!tip) { tip = document.createElement("div"); tip.className = "spark-tip"; box.append(tip); }
    const values = chart.fields.map(field => Number.isFinite(sample[field]) ? sample[field] + chart.unit : "—");
    tip.textContent = `${values.join(" / ")} · ${new Date(sample.timestamp * 1000).toLocaleTimeString(document.documentElement.lang)}`;
    tip.style.left = Math.min(Math.max(event.clientX - rect.left, 45), rect.width - 45) + "px";
  });
  box.addEventListener("pointerleave", () => box.querySelector(".spark-tip")?.remove());
}
form.addEventListener("input", () => { dirty = true; });
form.addEventListener("submit", event => {
  event.preventDefault();
  if (saving || !form.reportValidity()) return;
  $("history-save").disabled = true;
  form.querySelectorAll("input").forEach(input => { input.disabled = true; });
  saving = (async () => {
    try {
      const value = await api("/system/history", "PUT", { enabled: $("history-enabled").checked,
        retention_minutes: Number($("history-minutes").value), sample_seconds: Number($("history-seconds").value) });
      dirty = false;
      showHistory(value);
      $("history-error").textContent = "";
      nextHistory = 0;
    } catch (error) { $("history-error").textContent = error.message; }
    finally {
      saving = null; $("history-save").disabled = false;
      form.querySelectorAll("input").forEach(input => { input.disabled = false; });
    }
  })();
});
function percent(id, value) {
  $(id).replaceChildren(document.createTextNode(value));
  const unit = document.createElement("span"); unit.className = "metric-unit"; unit.textContent = "%"; $(id).append(unit);
  const bar = $(id + "-bar");
  bar.style.width = value + "%";
  bar.classList.toggle("metric-warn", value >= 75 && value < 90);
  bar.classList.toggle("metric-danger", value >= 90);
}
async function refresh() {
  if (refreshing || document.hidden || $("oche-page")?.hidden || window.frameElement?.hidden) return;
  refreshing = true;
  try {
    const sys = await api("/system/status");
    for (const [id, field] of [["cpu", "cpu"], ["mem", "mem"], ["disk", "disk"]]) percent("m-" + id, sys[field + "_percent"]);
    for (const key of ["mem", "disk"]) $("m-" + key + "-sub").textContent = `${sys[key + "_used_gb"]} / ${sys[key + "_total_gb"]} GB`;
    $("m-temp").textContent = sys.cpu_temp_c == null ? "—" : sys.cpu_temp_c + "°C";
    $("m-temp-sub").textContent = sys.cpu_temp_c == null ? tr("Not reported by this device") : "";
    $("m-uptime").textContent = tr("Up {0}h {1}m", { 0: Math.floor(sys.uptime_s / 3600), 1: Math.floor(sys.uptime_s % 3600 / 60) });
    for (const [id, field] of [["disk-read", "disk_read_mb_s"], ["disk-write", "disk_write_mb_s"], ["net-down", "net_recv_mb_s"], ["net-up", "net_sent_mb_s"]]) $("m-" + id).textContent = sys[field];
    if (!saving && Date.now() >= nextHistory) {
      showHistory(await api("/system/history"));
      nextHistory = Date.now() + Math.min(history.config.sample_seconds, 60) * 1000;
    }
  } catch (error) { $("history-error").textContent = error.message; }
  finally { refreshing = false; }
}
setInterval(refresh, 5000);
document.addEventListener("visibilitychange", refresh);
const prepareAppearance = window.ocheBeforeAppearanceChange;
window.ocheBeforeAppearanceChange = async () => {
  await prepareAppearance?.();
  await saving;
  if (dirty) throw new Error(tr("Save your metrics history settings first."));
};
refresh();
