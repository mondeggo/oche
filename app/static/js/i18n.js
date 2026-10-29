/* The current catalogue is bundled into each local page; no external service is used. */
(() => {
  const root = document.documentElement;
  let messages = {};
  try { messages = JSON.parse(document.getElementById('oche-translations')?.textContent || '{}'); }
  catch (_) { /* English remains usable if a catalogue cannot be read. */ }
  let patterns;
  function t(message, values = {}) {
    if (typeof message !== 'string') return message;
    let translated = messages[message];
    if (!translated && message.includes(' ')) {
      patterns ||= Object.entries(messages).filter(([key]) => /\{\w+\}/.test(key)).map(([key, value]) => {
        const names = [];
        const expression = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
          .replace(/\\\{(\w+)\\\}/g, (_, name) => { names.push(name); return '(.*?)'; });
        return { expression: new RegExp('^' + expression + '$'), names, value };
      });
      for (const pattern of patterns) {
        const match = message.match(pattern.expression);
        if (!match) continue;
        translated = pattern.value;
        values = { ...Object.fromEntries(pattern.names.map((name, i) => [name, match[i + 1]])), ...values };
        break;
      }
    }
    return (translated || message).replace(/\{(\w+)\}/g,
      (match, key) => Object.hasOwn(values, key) ? String(values[key]) : match);
  }
  async function prepare(windowToCheck = window) {
    await windowToCheck.ocheBeforeAppearanceChange?.();
    for (const frame of windowToCheck.document.querySelectorAll('iframe')) {
      let child;
      try { if (frame.contentWindow.document) child = frame.contentWindow; }
      catch (_) { continue; }
      if (child) await prepare(child);
    }
  }
  globalThis.ocheI18n = { t, prepare, language: root.lang || 'en' };
  globalThis.tr = t;
  document.addEventListener('change', async event => {
    if (event.target.id !== 'language-select') return;
    const select = event.target;
    select.disabled = true;
    try {
      await window.ocheTheme.setLanguage(select.value);
    } catch (error) {
      select.value = root.lang;
      select.title = t(error.message);
    } finally { select.disabled = false; }
  });
})();
