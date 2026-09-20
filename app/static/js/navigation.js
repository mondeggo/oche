// Keep camera-bearing documents attached for the lifetime of this browser tab.
// Moving an iframe between DOM parents also reloads it, so each view stays put.
(() => {
  const persistent = path => path === '/autodarts';
  const internal = url => url.origin === location.origin &&
    (/^\/(?:board|autodarts|supervisor|play|autoglow|config)?$/.test(url.pathname) ||
     /^\/panels\/[^/]+$/.test(url.pathname));
  let shell = window;
  try {
    if (parent !== window && parent.ocheNavigation) shell = parent;
  } catch (_) { /* Oche can itself be embedded by another origin. */ }

  if (shell === window) {
    const boardUrl = `${location.protocol}//${location.hostname}:3180/`;
    let sharedBoardFrame = null;
    let sharedBoardReady = false;
    let sharedBoardLoaded = false;
    let sharedBoardPid = null;
    let activeBoardSlot = null;
    let boardTrackingFrame = null;

    function boardSlotRect(slot) {
      if (!slot || !slot.isConnected || slot.hidden) return null;
      let rect = slot.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return null;
      let view = slot.ownerDocument.defaultView;
      while (view && view !== window) {
        const hostFrame = view.frameElement;
        if (!hostFrame || hostFrame.hidden) return null;
        const hostRect = hostFrame.getBoundingClientRect();
        if (hostRect.width < 1 || hostRect.height < 1) return null;
        rect = {
          left: hostRect.left + rect.left,
          top: hostRect.top + rect.top,
          width: rect.width,
          height: rect.height,
        };
        view = hostFrame.ownerDocument.defaultView;
      }
      return rect;
    }

    function parkBoard() {
      if (!sharedBoardFrame) return;
      sharedBoardFrame.classList.remove('active');
      sharedBoardFrame.style.left = '-100000px';
      sharedBoardFrame.style.top = '0';
    }

    function renderBoard() {
      if (!sharedBoardFrame || !activeBoardSlot || !sharedBoardReady) {
        parkBoard();
        return;
      }
      const rect = boardSlotRect(activeBoardSlot);
      if (!rect) {
        parkBoard();
        return;
      }
      sharedBoardFrame.style.left = `${rect.left}px`;
      sharedBoardFrame.style.top = `${rect.top}px`;
      sharedBoardFrame.style.width = `${rect.width}px`;
      sharedBoardFrame.style.height = `${rect.height}px`;
      sharedBoardFrame.classList.add('active');
    }

    function trackBoard() {
      boardTrackingFrame = null;
      if (!activeBoardSlot) return;
      renderBoard();
      boardTrackingFrame = requestAnimationFrame(trackBoard);
    }

    function startBoardTracking() {
      if (boardTrackingFrame === null) {
        boardTrackingFrame = requestAnimationFrame(trackBoard);
      }
    }

    function ensureSharedBoard(pid) {
      if (!sharedBoardFrame) {
        sharedBoardFrame = document.createElement('iframe');
        sharedBoardFrame.id = 'oche-board-frame';
        sharedBoardFrame.className = 'oche-board-frame';
        sharedBoardFrame.title = 'Autodarts Board';
        sharedBoardFrame.allowFullscreen = true;
        sharedBoardFrame.addEventListener('load', () => {
          sharedBoardReady = true;
          renderBoard();
        });
        document.body.appendChild(sharedBoardFrame);
      }

      const pidChanged = pid != null && sharedBoardPid != null && pid !== sharedBoardPid;
      if (pid != null) sharedBoardPid = pid;
      if (!sharedBoardLoaded || pidChanged) {
        sharedBoardReady = false;
        sharedBoardFrame.src = boardUrl;
        sharedBoardLoaded = true;
      }
    }

    window.ocheBoard = {
      show(slot, pid) {
        ensureSharedBoard(pid);
        if (!boardSlotRect(slot)) return false;
        activeBoardSlot = slot;
        renderBoard();
        startBoardTracking();
        return true;
      },
      hide(slot) {
        if (slot && activeBoardSlot !== slot) return;
        activeBoardSlot = null;
        parkBoard();
      },
    };

    const original = document.getElementById('oche-page');
    const initialUrl = new URL(location.href);
    const views = new Map();
    let active = { element: original, url: initialUrl, original: true, title: document.title };
    if (persistent(initialUrl.pathname)) views.set(initialUrl.pathname + initialUrl.search, active);

    function navigate(href, push = true) {
      const url = new URL(href, location.href);
      if (!internal(url)) return false;
      if (push && url.href === location.href) return true;
      window.ocheBoard.hide();
      let next = views.get(url.pathname + url.search);
      const retained = Boolean(next);
      if (!next) {
        const frame = document.createElement('iframe');
        frame.className = 'oche-page-frame';
        frame.title = 'Oche';
        frame.allowFullscreen = true;
        frame.hidden = true;
        frame.src = url.href;
        next = { element: frame, url };
        frame.addEventListener('load', () => {
          if (active === next) document.title = frame.contentDocument.title;
        });
        document.body.appendChild(frame);
        if (persistent(url.pathname)) views.set(url.pathname + url.search, next);
      }
      active.element.hidden = true;
      if (!active.original && !persistent(active.url.pathname) && active !== next) {
        active.element.remove();
      }
      active = next;
      active.element.hidden = false;
      if (push) history.pushState(null, '', url.href);
      const doc = active.original ? document : active.element.contentDocument;
      if (active.original) document.title = active.title;
      else if (doc && doc.title) document.title = doc.title;
      // Settings and pinned panels may have changed while this view was hidden.
      // Refresh only navigation markup; never replace its camera-bearing body.
      if (retained && doc) {
        fetch(url.href).then(response => {
          if (!response.ok) throw new Error('Navigation refresh failed');
          return response.text();
        }).then(html => {
          const updated = new DOMParser().parseFromString(html, 'text/html');
          const header = updated.getElementById('topbar');
          if (header) doc.getElementById('topbar').innerHTML = header.innerHTML;
        }).catch(() => { /* Keep usable navigation when temporarily offline. */ });
      }
      return true;
    }
    window.ocheNavigation = { navigate };
    window.addEventListener('popstate', () => navigate(location.href, false));
  }

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
