(() => {
  const container = document.getElementById('terminal');
  const status = document.getElementById('terminal-status');
  const reconnect = document.getElementById('terminal-reconnect');
  const copyButton = document.getElementById('terminal-copy');
  const contextMenu = document.getElementById('terminal-context-menu');
  const copyStatus = document.getElementById('terminal-copy-status');
  const isMac = /Mac/.test(navigator.platform);
  const selectionHint = `Hold ${isMac ? 'Option' : 'Shift'} and drag to select text.`;
  let selectedText = '';
  let hoveredLink = null;
  let pressedLink = null;
  function webUrl(uri) {
    try {
      const url = new URL(uri);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch (_) { return null; }
  }
  function openLink(event, uri) {
    if (event.button !== 0 || event.shiftKey || (isMac && event.altKey)) return;
    event.preventDefault();
    const url = webUrl(uri);
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  }
  function renderedLinkAt(event) {
    // Read the whole rendered row: colors and the cursor split a URL into spans.
    // Resolve at click time so a missed hover or repaint cannot hide the link.
    for (const row of container.querySelectorAll('.xterm-rows > div')) {
      const bounds = row.getBoundingClientRect();
      if (event.clientY < bounds.top || event.clientY >= bounds.bottom) continue;
      for (const match of row.textContent.matchAll(/https?:\/\/[^\s<>"'`]+/gi)) {
        const text = match[0].replace(/[.,;!?]+$/, '');
        const uri = webUrl(text);
        if (!uri) continue;
        const range = document.createRange();
        const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
        let offset = 0;
        let started = false;
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const end = offset + node.textContent.length;
          if (!started && match.index < end) {
            range.setStart(node, match.index - offset);
            started = true;
          }
          if (started && match.index + text.length <= end) {
            range.setEnd(node, match.index + text.length - offset);
            for (const rect of range.getClientRects()) {
              if (event.clientX >= rect.left && event.clientX < rect.right &&
                  event.clientY >= rect.top && event.clientY < rect.bottom) return uri;
            }
            break;
          }
          offset = end;
        }
      }
    }
    return null;
  }
  const linkHandler = {
    activate: openLink,
    hover: (_event, uri) => { hoveredLink = webUrl(uri); container.title = uri; },
    leave: () => { hoveredLink = null; container.removeAttribute('title'); },
  };
  const terminal = new Terminal({
    cursorBlink: true, fontSize: 14, scrollback: 2000,
    theme: { background: '#18181b', foreground: '#ffffff' },
    linkHandler,
    macOptionClickForcesSelection: true,
  });
  const fit = new FitAddon.FitAddon();
  terminal.loadAddon(fit);
  terminal.loadAddon(new WebLinksAddon.WebLinksAddon(openLink, linkHandler));
  terminal.open(container);
  terminal.onSelectionChange(() => {
    // Keep selected text available if the live TUI repaints before Copy is clicked.
    if (terminal.hasSelection()) selectedText = terminal.getSelection();
    copyStatus.textContent = '';
  });
  function copyWithTextarea(text) {
    // Clipboard API is unavailable on ordinary HTTP connections to a LAN host.
    const field = document.createElement('textarea');
    field.value = text;
    field.readOnly = true;
    field.style.cssText = 'position:fixed;left:0;top:0;opacity:0;';
    document.body.appendChild(field);
    const focused = document.activeElement;
    try {
      field.select();
      return document.execCommand('copy');
    } finally {
      field.remove();
      focused?.focus({ preventScroll: true });
    }
  }
  async function copySelection() {
    const text = selectedText || terminal.getSelection();
    if (!text) {
      copyStatus.textContent = selectionHint;
      return;
    }
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        try { await navigator.clipboard.writeText(text); copied = true; } catch (_) { /* Try HTTP fallback. */ }
      }
      if (!copied) copied = copyWithTextarea(text);
    } catch (_) { /* Show failure without losing the selected text. */ }
    copyStatus.textContent = copied ? 'Copied.' : 'Copy failed. Select text and use your browser’s Copy command.';
  }
  function hideContextMenu() { contextMenu.hidden = true; }
  container.addEventListener('contextmenu', event => {
    if (!selectedText && !terminal.hasSelection()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    contextMenu.hidden = false;
    contextMenu.style.left = `${Math.max(0, Math.min(event.clientX, innerWidth - contextMenu.offsetWidth))}px`;
    contextMenu.style.top = `${Math.max(0, Math.min(event.clientY, innerHeight - contextMenu.offsetHeight))}px`;
    copyButton.focus({ preventScroll: true });
  }, true);
  // Keep right-clicks out of the TUI while opening the Copy menu.
  for (const name of ['mousedown', 'mouseup']) {
    container.addEventListener(name, event => {
      if (event.button === 2 && (selectedText || terminal.hasSelection())) {
        event.stopImmediatePropagation();
      }
    }, true);
  }
  document.addEventListener('mousedown', event => {
    if (!contextMenu.contains(event.target)) hideContextMenu();
  }, true);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !contextMenu.hidden) {
      event.preventDefault();
      hideContextMenu();
      terminal.focus();
    }
  });
  window.addEventListener('blur', hideContextMenu);
  copyButton.addEventListener('click', () => {
    copySelection();
    hideContextMenu();
    terminal.focus();
  });
  terminal.attachCustomKeyEventHandler(event => {
    if (event.type === 'keydown' && (event.ctrlKey || event.metaKey) && !event.altKey &&
        event.key.toLowerCase() === 'c' && (terminal.hasSelection() || event.shiftKey)) {
      event.preventDefault();
      copySelection();
      return false;
    }
    return true;
  });
  // TUI mouse reporting can trigger a redraw before xterm receives mouseup.
  // Keep link clicks in the browser; other mouse input still reaches the app.
  container.addEventListener('mousedown', event => {
    pressedLink = null;
    if (event.button === 0) {
      selectedText = '';
      copyStatus.textContent = '';
    }
    if (event.button !== 0 || event.shiftKey || (isMac && event.altKey)) return;
    const uri = renderedLinkAt(event) || hoveredLink;
    if (!uri) return;
    pressedLink = { uri, x: event.clientX, y: event.clientY };
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  window.addEventListener('mouseup', event => {
    if (event.button !== 0 || !pressedLink) return;
    const link = pressedLink;
    pressedLink = null;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (container.contains(event.target) &&
        Math.hypot(event.clientX - link.x, event.clientY - link.y) < 5) {
      openLink(event, link.uri);
    }
  }, true);
  let socket;
  let sessionError = false;

  function send(message) {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  }
  function resize() {
    if (!container.clientWidth || !container.clientHeight) return;
    const fontSize = container.clientWidth < 600 ? 12 : 14;
    if (terminal.options.fontSize !== fontSize) terminal.options.fontSize = fontSize;
    fit.fit();
    const rows = Math.max(2, Math.min(200, terminal.rows));
    const cols = Math.max(2, Math.min(500, terminal.cols));
    if (rows !== terminal.rows || cols !== terminal.cols) terminal.resize(cols, rows);
    send({ type: 'resize', rows, cols });
  }
  function connect() {
    if (socket && socket.readyState < WebSocket.CLOSING) return;
    terminal.reset();
    hideContextMenu();
    selectedText = '';
    copyStatus.textContent = '';
    hoveredLink = null;
    pressedLink = null;
    sessionError = false;
    reconnect.disabled = true;
    status.textContent = 'Connecting…';
    const url = new URL('/autodarts/terminal/ws', location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(url);
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => {
      status.textContent = 'Connected';
      resize();
      if (!matchMedia('(pointer: coarse)').matches) terminal.focus();
    };
    socket.onmessage = event => {
      if (typeof event.data === 'string') {
        const message = JSON.parse(event.data);
        if (message.error) {
          sessionError = true;
          status.textContent = message.error;
        }
      } else {
        terminal.write(new Uint8Array(event.data));
      }
    };
    socket.onclose = () => {
      reconnect.disabled = false;
      if (!sessionError) status.textContent = 'Disconnected. Reconnect to open board setup.';
    };
    socket.onerror = () => {
      sessionError = true;
      status.textContent = 'Unable to connect. Check that Autodarts is running, then reconnect.';
    };
  }
  terminal.onData(data => {
    // A large paste should not exceed the server's per-message limit. Keep
    // surrogate pairs intact while splitting UTF-16 strings into chunks.
    for (let start = 0; start < data.length;) {
      let end = Math.min(start + 4096, data.length);
      if (end < data.length && /[\uD800-\uDBFF]/.test(data[end - 1])) end--;
      send({ type: 'input', data: data.slice(start, end) });
      start = end;
    }
  });
  new ResizeObserver(resize).observe(container);
  const touchKeys = { Escape: '\x1b', Tab: '\t', ArrowLeft: '\x1b[D',
    ArrowUp: '\x1b[A', ArrowDown: '\x1b[B', ArrowRight: '\x1b[C', Enter: '\r' };
  document.querySelector('.terminal-touch-keys').addEventListener('click', event => {
    const key = event.target.closest('[data-key]')?.dataset.key;
    let data = touchKeys[key];
    if (data && key.startsWith('Arrow') && terminal.modes.applicationCursorKeysMode) {
      data = data.replace('[', 'O');
    }
    if (data) send({ type: 'input', data });
  });
  document.getElementById('terminal-keyboard').addEventListener('click', () => terminal.focus());
  document.getElementById('terminal-keys-toggle').addEventListener('click', event => {
    const keys = document.getElementById('terminal-touch-keys');
    keys.hidden = !keys.hidden;
    event.currentTarget.setAttribute('aria-expanded', String(!keys.hidden));
  });
  reconnect.addEventListener('click', connect);
  window.addEventListener('pagehide', () => socket?.close());
  connect();
})();
