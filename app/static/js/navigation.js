import { canonicalUrl as resolveUrl, isHome, persistent, viewKey } from "./routes.mjs";
// Keep Home's Caller audio and Supervisor previews attached in this tab.
// Moving an iframe between DOM parents also reloads it, so each view stays put.
(() => {
  const canonicalUrl = href => resolveUrl(href, location.href);
  const internal = url => url.origin === location.origin &&
    (isHome(url.pathname) || url.pathname === '/integrations/cameras' ||
     /^\/(?:supervisor|play|local(?:\/tournament)?|config(?:\/https)?)?$/.test(url.pathname) ||
     /^\/panels\/[^/]+$/.test(url.pathname));
  let shell = window;
  try {
    if (parent !== window && parent.ocheNavigation) shell = parent;
  } catch (_) { /* Oche can itself be embedded by another origin. */ }

  if (shell === window) {
    const original = document.getElementById('oche-page');
    const initialUrl = canonicalUrl(location.href);
    if (initialUrl.href !== location.href) history.replaceState(null, '', initialUrl.href);
    const views = new Map();
    let active = { element: original, url: initialUrl, original: true, title: document.title };
    if (persistent(initialUrl.pathname)) views.set(viewKey(initialUrl), active);

    function syncWorkspace(view) {
      if (!isHome(view.url.pathname)) return;
      const doc = view.original ? document : view.element.contentDocument;
      const home = doc?.defaultView;
      if (!home || (home !== window && !isHome(home.location.pathname))) return;
      if (home !== window) home.history.replaceState(null, '', view.url.pathname + view.url.search + view.url.hash);
      home.dispatchEvent(new home.Event('oche:route'));
    }

    function navigate(href, push = true) {
      const url = canonicalUrl(href);
      if (!internal(url)) return false;
      if (push && url.href === location.href) return true;
      let next = views.get(viewKey(url));
      const retained = Boolean(next);
      if (!next) {
        const frame = document.createElement('iframe');
        frame.className = 'oche-page-frame';
        frame.title = 'Oche';
        frame.allowFullscreen = true;
        frame.allow = 'autoplay';
        frame.hidden = true;
        frame.src = url.href;
        next = { element: frame, url };
        frame.addEventListener('load', () => {
          if (active === next) {
            syncWorkspace(next);
            document.title = frame.contentDocument.title;
          }
        });
        document.body.appendChild(frame);
        if (persistent(url.pathname)) views.set(viewKey(url), next);
      }
      active.element.hidden = true;
      if (!active.original && !persistent(active.url.pathname) && active !== next) {
        active.element.remove();
      }
      active = next;
      active.url = url;
      active.element.hidden = false;
      if (push) history.pushState(null, '', url.href);
      else if (url.href !== location.href) history.replaceState(null, '', url.href);
      const doc = active.original ? document : active.element.contentDocument;
      if (active.original) document.title = active.title;
      else if (doc && doc.title) document.title = doc.title;
      syncWorkspace(active);
      // Settings, panels and integrations may have changed while this view was hidden.
      // Refresh only sidebar markup; never replace its camera-bearing body.
      if (retained && doc) {
        fetch(url.href).then(response => {
          if (!response.ok) throw new Error(tr("Navigation refresh failed"));
          return response.text();
        }).then(html => {
          const updated = new DOMParser().parseFromString(html, 'text/html');
          // Sync visibility and the panel list; Home keeps its own live sidebar state.
          for (const source of updated.querySelectorAll('[data-nav-sync][id]')) {
            const target = doc.getElementById(source.id);
            if (target) target.hidden = source.hidden;
          }
          const panels = updated.getElementById('sidebar-panel-list');
          if (panels && doc.getElementById('sidebar-panel-list')) {
            doc.getElementById('sidebar-panel-list').innerHTML = panels.innerHTML;
          }
          const profiles = updated.getElementById('app-profile');
          if (profiles && doc.getElementById('app-profile') && !doc.getElementById('core-main')) {
            doc.getElementById('app-profile').innerHTML = profiles.innerHTML;
          }
          doc.defaultView.ocheTheme?.refresh();
          doc.defaultView.ocheSidebar?.refresh();
        }).catch(() => { /* Keep usable navigation when temporarily offline. */ });
      }
      return true;
    }
    window.ocheNavigation = { navigate };
    window.addEventListener('popstate', () => navigate(location.href, false));
  }

  window.ocheNavigate = href => shell.ocheNavigation.navigate(href);

  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey ||
        event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.hasAttribute('download') ||
        (link.target && link.target !== '_self')) return;
    const url = new URL(link.href);
    if (!internal(url)) return;
    // Keep local anchors in the current document.
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    if (shell.ocheNavigation.navigate(url.href)) event.preventDefault();
  });
})();
