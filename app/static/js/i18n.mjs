export function tr(message, values = {}) {
  if (globalThis.ocheI18n) return globalThis.ocheI18n.t(message, values);
  if (typeof message !== "string") return message;
  return message.replace(/\{(\w+)\}/g, (match, key) => Object.hasOwn(values, key) ? String(values[key]) : match);
}
