import { tr } from "./i18n.mjs";
import { autodartsHealth, localHealth, cloudHealth } from './health.mjs';

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
})();

// Sidebar status: Autodarts process, local detector readiness and cloud connection.
(() => {
  const rows = Object.fromEntries([...document.querySelectorAll('#sidebar-health [data-health]')]
    .map(row => [row.dataset.health, row]));
  if (!rows.autodarts) return;
  const getJSON = url => fetch(url, {cache: 'no-store', signal: AbortSignal.timeout(4000)})
    .then(response => response.ok ? response.json() : null).catch(() => null);

  // Keep each cause and recovery action in the tooltip and accessible name.
  function show(name, tone, text, detail = '') {
    const row = rows[name];
    if (!row) return;
    row.dataset.tone = tone;
    row.title = `${row.dataset.label}: ${text}${detail ? ' · ' + detail : ''}`;
    row.setAttribute('aria-label', row.title);
  }

  function age(iso) {
    const seconds = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
    if (seconds < 10) return tr("Just now");
    if (seconds < 60) return tr("{0} s ago", {0: seconds});
    if (seconds < 3600) return tr("{0} min ago", {0: Math.round(seconds / 60)});
    return tr("{0} h ago", {0: Math.round(seconds / 3600)});
  }

  let loading = false;
  async function refresh() {
    // Retained pages keep running hidden; only the visible one needs fresh status.
    if (loading || window.frameElement?.hidden) return;
    loading = true;
    try {
      const [local, status] = await Promise.all([getJSON('/autodarts/status'), getJSON('/api/status')]);
      show('autodarts', ...autodartsHealth(local));
      if (!status) {
        show('local', ...localHealth(null));
        return show('cloud', ...cloudHealth(null));
      }
      show('local', ...localHealth(status.local_detection));
      const [tone, text, reason] = cloudHealth(status);
      const last = status.cloud.last_message_at;
      const linked = status.auth.state === 'authenticated';
      const lastEvent = last ? tr("Last event {0}", {"0": (age(last).toLowerCase())}) : linked ? tr("No game events yet") : '';
      show('cloud', tone, text, [reason, lastEvent].filter(Boolean).join(' · '));
    } finally {
      loading = false;
    }
  }

  window.ocheSidebar = { refresh };
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
