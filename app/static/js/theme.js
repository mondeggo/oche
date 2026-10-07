// Apply the cached appearance before CSS loads, then follow OcheCore's saved theme.
(() => {
  const key = 'oche.theme';
  const requestKey = 'oche.theme.request';
  const endpoint = '/ochecore/ui/api/ui';
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
  let savedUI = null;
  let pendingTheme = null;
  let revision = 0;
  let syncing = false;
  try {
    if (parent !== window && parent.ocheTheme) {
      owner = parent;
      preference = parent.ocheTheme.preference();
    }
  } catch (_) { /* Oche may itself be embedded by another origin. */ }

  function refresh() {
    const button = document.getElementById('theme-toggle');
    if (!button) return;
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    button.title = button.ariaLabel = `Switch to ${next} theme`;
  }

  function coreSettings() {
    return owner === window ? savedUI : owner.ocheTheme.coreSettings?.();
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
    // Retained pages and Caller audio keep running while their appearance updates.
    for (const frame of document.querySelectorAll('iframe')) {
      try {
        const child = frame.contentWindow;
        if (child.ocheTheme) {
          child.ocheTheme.apply(preference);
        } else if (new URL(frame.src).pathname.startsWith('/ochecore/ui/')) {
          const settings = coreSettings();
          child.dispatchEvent(new child.CustomEvent('ochecore:ui-settings', {
            detail: { ...settings, theme,
              embedded: settings?.embedded ?? (child.document.documentElement.dataset.embedded === 'true') },
          }));
        }
      } catch (_) { /* External panels keep their own appearance. */ }
    }
  }

  function remember(theme) {
    try { localStorage.setItem(key, theme); } catch (_) { /* Keep it for this visit. */ }
    apply(theme);
  }

  async function sync() {
    if (owner !== window || syncing) return;
    syncing = true;
    const requestedRevision = revision;
    const desired = pendingTheme;
    try {
      const response = await fetch(endpoint, {
        method: desired ? 'PATCH' : 'GET',
        cache: 'no-store',
        headers: desired ? { 'Content-Type': 'application/json' } : undefined,
        body: desired ? JSON.stringify({ theme: desired }) : undefined,
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) return;
      const settings = await response.json();
      if (!valid(settings.theme) || requestedRevision !== revision) return;
      savedUI = settings;
      if (desired) pendingTheme = null;
      remember(settings.theme);
    } catch (_) {
      // Core may be stopped. Keep the local appearance and retry the latest choice.
    } finally {
      syncing = false;
      // A newer click during this request must win over its older response.
      if (pendingTheme && requestedRevision !== revision) sync();
    }
  }

  function queueTheme(theme) {
    revision++;
    pendingTheme = theme;
    remember(theme);
    sync();
  }

  function set(theme) {
    if (!valid(theme)) return;
    if (owner !== window) {
      owner.ocheTheme.set(theme);
      return;
    }
    // Publish user choices separately from the cache refreshed by server polling.
    // Other tabs can replace an offline choice without mistaking old GETs for clicks.
    try {
      localStorage.setItem(requestKey, JSON.stringify({ theme, id: `${Date.now()}-${Math.random()}` }));
    } catch (_) { /* Keep the choice in this page when storage is unavailable. */ }
    queueTheme(theme);
  }

  window.ocheTheme = { set, apply, refresh, coreSettings, preference: () => preference };
  apply(preference);
  document.addEventListener('DOMContentLoaded', refresh);
  document.addEventListener('load', event => {
    if (event.target.tagName === 'IFRAME') apply(preference);
  }, true);
  document.addEventListener('click', event => {
    if (event.target.closest('#theme-toggle')) {
      set(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    }
  });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) {
      apply(owner === window ? pendingTheme || read() : owner.ocheTheme.preference());
    }
    if (event.key === requestKey && owner === window) {
      try {
        // A newer choice may already have arrived while this event was queued.
        if (event.newValue !== localStorage.getItem(requestKey)) return;
        const request = JSON.parse(event.newValue);
        if (valid(request?.theme)) queueTheme(request.theme);
      } catch (_) { /* Ignore malformed or unavailable storage. */ }
    }
  });
  system.addEventListener('change', () => {
    if (preference === null) apply(null);
  });
  if (owner === window) {
    sync();
    setInterval(sync, 5000);
    window.addEventListener('focus', sync);
  }
})();
