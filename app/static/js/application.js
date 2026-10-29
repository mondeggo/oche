(() => {
  let message;
  function announce(text) {
    if (!message) {
      message = document.createElement('div');
      message.id = 'application-message';
      message.setAttribute('role', 'status');
      document.body.append(message);
    }
    message.textContent = text;
  }
  const buttons = document.querySelectorAll('[data-restart]');
  if (!buttons.length) return;
  fetch('/updates/status').then(response => response.json()).then(data => {
    buttons.forEach(button => { button.disabled = !data.enabled; button.title = data.enabled ? tr("Restart Oche") : tr("Run Oche through its launcher to restart from here."); });
  }).catch(() => {});
  buttons.forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const previous = await (await fetch('/healthz')).json();
      const response = await fetch('/system/restart', {method:'POST'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || tr("Restart failed."));
      announce(tr("Restarting Oche. This page will reconnect automatically."));
      let attempts = 0, checking = false;
      const timer = setInterval(async () => {
        if (checking) return;
        if (++attempts > 60) { clearInterval(timer); announce(tr("Oche has not reconnected. Check the container logs.")); button.disabled = false; return; }
        checking = true;
        try {
          const next = await (await fetch('/healthz', {signal:AbortSignal.timeout(1500)})).json();
          if (next.ok && next.boot_id !== previous.boot_id) { clearInterval(timer); location.reload(); }
        } catch { /* Startup is in progress. */ }
        finally { checking = false; }
      }, 1500);
    } catch (error) { announce(error.message); button.disabled = false; }
  }));
})();
