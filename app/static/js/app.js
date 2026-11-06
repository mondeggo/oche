import { tr } from "./i18n.mjs";
import { engineHealth, localEventsHealth, manualHealth, syncHealthRows } from './health.mjs';

// Sidebar: collapses to an icon rail on wide screens and becomes a drawer on small ones.
(() => {
  const shell = document.getElementById('oche-page');
  const sidebar = document.getElementById('app-sidebar');
  const collapseButton = document.getElementById('sidebar-collapse');
  const menuButton = document.getElementById('nav-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!shell || !sidebar) return;
  const compact = matchMedia('(max-width: 900px)');
  const storageKey = 'oche.sidebar.' + shell.dataset.sidebarPage;

  // Labels are visible when expanded; the rail relies on tooltips instead.
  function labelRail() {
    const collapsed = shell.classList.contains('sidebar-collapsed') && !compact.matches;
    for (const link of sidebar.querySelectorAll('.nav-item[data-label], .brand')) {
      if (collapsed) link.title = link.dataset.label || link.getAttribute('aria-label') || '';
      else if (link.dataset.label) link.removeAttribute('title');
    }
    const label = compact.matches ? tr("Close menu") : collapsed ? tr("Expand sidebar") : tr("Collapse sidebar");
    collapseButton.title = label;
    collapseButton.setAttribute('aria-label', label);
    collapseButton.setAttribute('aria-expanded', String(!collapsed));
  }

  function setCollapsed(collapsed) {
    shell.classList.toggle('sidebar-collapsed', collapsed);
    try { sessionStorage.setItem(storageKey, collapsed ? '1' : '0'); } catch (_) { /* Keep it for this page. */ }
    labelRail();
    // Embedded tools such as the terminal and lighting editor resize with the page.
    window.dispatchEvent(new Event('resize'));
  }

  function setDrawer(open) {
    shell.classList.toggle('nav-open', open);
    menuButton.setAttribute('aria-expanded', String(open));
    menuButton.setAttribute('aria-label', open ? tr("Close menu") : tr("Open menu"));
    backdrop.hidden = !open;
    if (open) sidebar.querySelector('.nav-item:not([hidden])')?.focus();
  }

  collapseButton.addEventListener('click', () => {
    if (compact.matches) setDrawer(false);
    else setCollapsed(!shell.classList.contains('sidebar-collapsed'));
  });
  menuButton.addEventListener('click', () => setDrawer(!shell.classList.contains('nav-open')));
  backdrop.addEventListener('click', () => setDrawer(false));
  sidebar.addEventListener('click', event => {
    if (event.target.closest('a') && compact.matches) setDrawer(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && shell.classList.contains('nav-open')) {
      setDrawer(false);
      menuButton.focus();
    }
  });
  compact.addEventListener('change', () => { setDrawer(false); labelRail(); });
  labelRail();
  // A game collapses the sidebar to the rail for more room, and restores it when the game ends.
  // The flag survives a reload during the game.
  const gameKey = storageKey + '.game';
  const forGame = value => {
    try {
      if (value === undefined) return sessionStorage.getItem(gameKey) === '1';
      sessionStorage.setItem(gameKey, value ? '1' : '0');
    } catch (_) { /* Restore nothing after a reload. */ }
    return false;
  };
  window.ocheSidebar = {
    ...window.ocheSidebar,
    game(playing) {
      const collapsed = shell.classList.contains('sidebar-collapsed');
      if (playing && !collapsed && !compact.matches) { forGame(true); setCollapsed(true); }
      else if (!playing && forGame()) { forGame(false); if (collapsed) setCollapsed(false); }
    },
  };
})();

// Enabled integrations own their indicators; Manual is shown without a selected engine.
(() => {
  const container = document.getElementById('sidebar-health');
  if (!container) return;
  const modules = new Map();
  const load = url => {
    if (!modules.has(url)) modules.set(url, import(url));
    return modules.get(url);
  };
  const getJSON = url => fetch(url, {cache: 'no-store', signal: AbortSignal.timeout(4000)})
    .then(response => response.ok ? response.json() : null).catch(() => null);

  // Keep each cause and recovery action in the tooltip and accessible name.
  function show(row, tone, text, detail = '') {
    row.dataset.tone = tone;
    row.title = `${row.dataset.label}: ${text}${detail ? ' · ' + detail : ''}`;
    row.setAttribute('aria-label', row.title);
  }

  async function check(row, status) {
    if (!status) return ['bad', tr("Status unavailable")];
    if (row.dataset.health === 'manual') return manualHealth(tr);
    const engine = status.vision?.engines?.find(engine => engine.name === row.dataset.health);
    // Inactive or unavailable engines need no event check, and never inherit
    // the selected engine's detection state.
    if (engine && (!engine.active || engine.status !== 'running' || !engine.scores)) {
      return engineHealth(engine, null, null, tr);
    }
    const module = await load(row.dataset.healthModule);
    const events = engine && status.local?.active
      ? localEventsHealth(status.local, tr) : await module.health(row.dataset.health, status, tr);
    return engine ? engineHealth(engine, status.local_detection, events, tr) : events;
  }

  let loading = false;
  async function refresh() {
    // Retained pages keep running hidden; only the visible one needs fresh status.
    if (loading || window.frameElement?.hidden) return;
    loading = true;
    try {
      const status = await getJSON('/api/status');
      const rows = status?.health ? syncHealthRows(container, status.health, tr)
        : [...container.querySelectorAll('[data-health]')];
      await Promise.all(rows.filter(row => !row.hidden).map(async row => {
        try {
          show(row, ...await check(row, status));
        } catch {
          show(row, 'bad', tr("Status unavailable"));
        }
      }));
    } finally {
      loading = false;
    }
  }

  window.ocheSidebar = { ...window.ocheSidebar, refresh };
  refresh();
  setInterval(refresh, 5000);
})();

// Outside Home, the sidebar's profile menu switches the active profile directly.
(() => {
  const select = document.getElementById('app-profile');
  if (!select || document.getElementById('core-main')) return;
  let current = select.value;
  select.addEventListener('change', async () => {
    select.disabled = true;
    try {
      await window.ocheTheme?.prepare();
      await window.ocheTheme?.flush();
      const listing = await fetch('/api/profiles', {cache: 'no-store'});
      const etag = listing.headers.get('ETag');
      const response = await fetch('/api/profile', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json', ...(etag ? {'If-Match': etag} : {})},
        body: JSON.stringify({id: select.value}),
      });
      if (!response.ok) throw new Error(tr("Profile not switched"));
      const applied = await response.json();
      window.ocheTheme?.applyProfile(applied.appearance, applied.active_profile);
      current = select.value;
      select.removeAttribute('title');
    } catch (_) {
      select.value = current;
      select.title = tr("Could not switch the profile. Try again.");
    } finally {
      select.disabled = false;
    }
  });
})();
