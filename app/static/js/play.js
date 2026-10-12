(() => {
  const frame = document.getElementById('play-frame');
  // This also allows HTTP on localhost, where the browser supports Play.
  // Defer loading the external page so insecure connections never show its error.
  if (window.isSecureContext) {
    frame.src = frame.dataset.src;
    frame.hidden = false;
    return;
  }

  document.getElementById('play-https-required').hidden = false;
  const status = document.getElementById('play-https-status');
  const settings = document.getElementById('play-https-settings');

  async function checkHTTPS() {
    try {
      const response = await fetch('/config/https/status', {cache: 'no-store'});
      if (!response.ok) throw new Error('HTTPS status unavailable');
      const data = await response.json();
      if (data.running) {
        const trusted = data.mode === 'letsencrypt';
        const url = new URL('/play', location.href);
        url.protocol = 'https:';
        url.hostname = trusted ? data.domain : (data.local_ip || location.hostname);
        url.port = String(data.port);
        const link = document.getElementById('play-https-open');
        link.href = url.href;
        link.hidden = false;
        status.textContent = 'Your selected ' + (trusted ? 'domain' : 'local')
          + ' HTTPS method is ready at ' + url.origin + '. Switch to HTTPS to continue.';
        settings.textContent = 'HTTPS settings';
        settings.classList.add('btn-neutral');
        document.getElementById('play-https-warning').hidden = trusted;
      } else if (data.enabled) {
        status.textContent = 'Your selected HTTPS method is not running. Open HTTPS settings to check it and try again.';
        settings.textContent = 'Check HTTPS settings';
      } else {
        status.textContent = 'HTTPS is off. Choose and enable an HTTPS method in Settings to continue.';
      }
    } catch (_) {
      status.textContent = 'Could not check your HTTPS settings. Open Settings to choose or check your HTTPS method.';
      settings.textContent = 'HTTPS settings';
    }
  }

  checkHTTPS();
})();
