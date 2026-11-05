// Apply Oche's saved appearance before CSS loads and share it with local tools.
(() => {
  const key = 'oche.theme';
  const requestKey = 'oche.theme.request';
  const endpoint = '/api/ui';
  const system = matchMedia('(prefers-color-scheme: light)');
  const valid = value => value === 'dark' || value === 'light';
  const read = () => {
    try {
      const value = localStorage.getItem(key);
      return valid(value) ? value : null;
    } catch (_) { return null; }
  };
  let preference = document.documentElement.dataset.theme || read() || 'dark';
  let owner = window;
  let savedUI = null;
  let pendingTheme = null;
  let pendingLanguage = null;
  let flight = Promise.resolve();
  let lastError = null;
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
    button.title = button.ariaLabel = tr(next === 'dark' ? tr("Switch to dark theme") : tr("Switch to light theme"));
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
    // Embedded Oche tools can share the current appearance. External panels keep their own.
    for (const frame of document.querySelectorAll('iframe')) {
      try {
        const child = frame.contentWindow;
        if (child.ocheTheme) {
          child.ocheTheme.apply(preference);
        }
      } catch (_) { /* External panels keep their own appearance. */ }
    }
  }

  function remember(theme) {
    try { localStorage.setItem(key, theme); } catch (_) { /* Keep it for this visit. */ }
    apply(theme);
  }

  function sync() {
    if (owner !== window) return owner.ocheTheme.refreshSettings();
    if (syncing) return flight;
    syncing = true;
    const requestedRevision = revision;
    const desired = { ...(pendingTheme ? { theme: pendingTheme } : {}),
      ...(pendingLanguage ? { language: pendingLanguage } : {}) };
    const patch = Object.keys(desired).length > 0;
    flight = (async () => { try {
      const response = await fetch(endpoint, {
        method: patch ? 'PATCH' : 'GET',
        cache: 'no-store',
        headers: patch ? { 'Content-Type': 'application/json' } : undefined,
        body: patch ? JSON.stringify({ ...desired, profile_id: document.documentElement.dataset.profileId || undefined }) : undefined,
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        lastError = new Error(error.detail || tr("Cannot save appearance settings."));
        if (response.status === 409) pendingTheme = pendingLanguage = null;
        return;
      }
      const settings = await response.json();
      if (!valid(settings.theme) || requestedRevision !== revision) return;
      savedUI = settings;
      lastError = null;
      if (patch) pendingTheme = pendingLanguage = null;
      document.documentElement.dataset.profileId = settings.profile_id || '';
      remember(settings.theme);
      if (settings.language && settings.language !== document.documentElement.lang) location.reload();
    } catch (_) {
      lastError = new Error(tr("Cannot save appearance settings. Check the Oche connection."));
      // Oche may be restarting. Keep the local appearance and retry the latest choice.
    } finally {
      syncing = false;
      // A newer click during this request must win over its older response.
      if ((pendingTheme || pendingLanguage) && requestedRevision !== revision) sync();
    } })();
    return flight;
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
      localStorage.setItem(requestKey, JSON.stringify({ theme, profile_id: document.documentElement.dataset.profileId, id: `${Date.now()}-${Math.random()}` }));
    } catch (_) { /* Keep the choice in this page when storage is unavailable. */ }
    queueTheme(theme);
  }

  async function flush() {
    if (owner !== window) return owner.ocheTheme.flush();
    await flight;
    if (pendingTheme || pendingLanguage) await sync();
    if (lastError && (pendingTheme || pendingLanguage)) throw lastError;
  }
  async function setLanguage(language) {
    if (owner !== window) return owner.ocheTheme.setLanguage(language);
    if (!['en', 'fr', 'de', 'pt-PT'].includes(language)) return;
    await window.ocheI18n.prepare();
    await flush();
    revision++;
    pendingLanguage = language;
    await sync();
    if (lastError) throw lastError;
  }
  function applyProfile(appearance, profileId) {
    if (owner !== window) return owner.ocheTheme.applyProfile(appearance, profileId);
    revision++;
    pendingTheme = pendingLanguage = null;
    lastError = null;
    document.documentElement.dataset.profileId = profileId;
    savedUI = { ...appearance, profile_id: profileId };
    remember(appearance.theme);
    if (appearance.language !== document.documentElement.lang) location.reload();
  }
  window.ocheTheme = { set, apply, refresh, coreSettings, flush, setLanguage, applyProfile,
    prepare: () => owner.ocheI18n.prepare(), refreshSettings: sync, preference: () => preference };
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
        if (valid(request?.theme) && (!request.profile_id || request.profile_id === document.documentElement.dataset.profileId)) queueTheme(request.theme);
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
