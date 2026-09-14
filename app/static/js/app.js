// Close the panels dropdown when leaving it or pressing Escape.
(() => {
  const menu = document.querySelector('.panels-menu');
  if (!menu) return;
  document.addEventListener('click', (event) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && menu.open) {
      menu.open = false;
      menu.querySelector('summary').focus();
    }
  });
})();

// Optional header autohide, opted into per-page by rendering the toggle
// button (base.html's allow_header_autohide flag). Header visibility is a
// plain on/off toggle - no hover-to-reveal behavior, no change to the header
// itself beyond sliding it in/out.
(function () {
  const topbar = document.getElementById('topbar');
  const toggleBtn = document.getElementById('header-toggle-btn');
  if (!topbar || !toggleBtn) return; // page didn't opt in

  const STORAGE_KEY = 'oche.headerHidden';
  document.body.classList.add('autohide-header');

  function setHidden(hidden, { persist } = { persist: false }) {
    topbar.classList.toggle('header-hidden', hidden);
    toggleBtn.classList.toggle('header-is-hidden', hidden);
    toggleBtn.title = hidden ? 'Show header' : 'Hide header';
    if (persist) {
      try { sessionStorage.setItem(STORAGE_KEY, hidden ? '1' : '0'); } catch (e) { /* ignore */ }
    }
  }

  toggleBtn.addEventListener('click', () => {
    setHidden(!topbar.classList.contains('header-hidden'), { persist: true });
  });

  // The server-sent config default always wins on a fresh load; a manual
  // click only overrides it for the current tab (sessionStorage), so a
  // config change takes effect on next reload instead of being shadowed
  // forever by a stale per-browser preference.
  let initiallyHidden = toggleBtn.dataset.defaultHidden === '1';
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (stored !== null) initiallyHidden = stored === '1';
  } catch (e) { /* ignore */ }
  setHidden(initiallyHidden);
})();
