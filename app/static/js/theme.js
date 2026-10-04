// Apply before the styles load, then keep Oche's retained pages in sync.
(() => {
  const key = 'oche.theme';
  const system = matchMedia('(prefers-color-scheme: light)');
  const valid = value => value === 'dark' || value === 'light';
  const read = () => {
    try {
      const value = localStorage.getItem(key);
      return valid(value) ? value : null;
    } catch (_) { return null; }
  };
  let preference = read();
  let owner = window;
  try {
    if (parent !== window && parent.ocheTheme) {
      owner = parent;
      preference = parent.ocheTheme.preference();
    }
  } catch (_) { /* External panels have their own appearance. */ }

  function refresh() {
    const button = document.getElementById('theme-toggle');
    if (!button) return;
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    button.title = button.ariaLabel = `Switch to ${next} theme`;
  }

  function apply(value) {
    preference = valid(value) ? value : null;
    const theme = preference || (system.matches ? 'light' : 'dark');
    const changed = document.documentElement.dataset.theme !== theme;
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.content = theme;
    refresh();
    if (changed) window.dispatchEvent(new CustomEvent('oche:themechange', { detail: { theme } }));
    // This also works when browser storage is disabled. Never reload a frame.
    for (const frame of document.querySelectorAll('iframe')) {
      try { frame.contentWindow.ocheTheme?.apply(preference); } catch (_) { /* Other origin. */ }
    }
  }

  function set(theme) {
    if (!valid(theme)) return;
    if (owner !== window) {
      owner.ocheTheme.set(theme);
      return;
    }
    try { localStorage.setItem(key, theme); } catch (_) { /* Keep the choice for this visit. */ }
    apply(theme);
  }

  window.ocheTheme = { set, apply, refresh, preference: () => preference };
  apply(preference);
  document.addEventListener('DOMContentLoaded', refresh);
  document.addEventListener('click', event => {
    if (event.target.closest('#theme-toggle')) {
      set(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    }
  });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) apply(read());
  });
  system.addEventListener('change', () => {
    if (preference === null) apply(null);
  });
})();
