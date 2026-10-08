// Keep the waiting screen in the outer page, above retained service iframes.
(() => {
  let owner = window;
  try {
    if (parent !== window && parent.ocheUpdateWait) owner = parent;
  } catch (_) { /* Cross-origin embedding cannot share its parent UI. */ }
  if (owner !== window) {
    window.ocheUpdateWait = owner.ocheUpdateWait;
    document.addEventListener('oche:updates', event => owner.ocheUpdateWait.observe(event.detail));
    return;
  }
  let latest = null;
  let waiting = null;
  let timer = null;
  let polling = false;
  let dialog, heading, description, spinner, returnButton;

  function show() {
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.id = 'oche-update-wait';
      dialog.className = 'update-wait';
      dialog.setAttribute('aria-labelledby', 'update-wait-title');
      spinner = document.createElement('div');
      spinner.className = 'update-wait-spinner';
      spinner.setAttribute('aria-hidden', 'true');
      heading = document.createElement('h1');
      heading.id = 'update-wait-title';
      description = document.createElement('p');
      description.setAttribute('role', 'status');
      description.setAttribute('aria-live', 'polite');
      const hint = document.createElement('p');
      hint.className = 'update-wait-hint';
      hint.textContent = 'Keep this tab open. Oche will reconnect automatically when it is ready.';
      returnButton = document.createElement('button');
      returnButton.type = 'button';
      returnButton.textContent = 'Return to Oche';
      returnButton.addEventListener('click', () => location.reload());
      dialog.append(spinner, heading, description, hint, returnButton);
      dialog.addEventListener('cancel', event => { if (waiting) event.preventDefault(); });
      document.body.append(dialog);
    }
    heading.textContent = 'Updating Oche';
    description.textContent = 'Preparing the update…';
    spinner.hidden = false;
    returnButton.hidden = true;
    if (!dialog.open) dialog.showModal();
  }

  function begin(data = latest, accepted = false) {
    if (waiting) return;
    waiting = {boot: data?.boot_id, accepted, sawPending: false, started: Date.now()};
    show();
    timer = setInterval(poll, 1500);
  }

  function stop() {
    clearInterval(timer);
    timer = null;
    waiting = null;
  }

  function fail(reason) {
    if (!waiting) return;
    stop();
    heading.textContent = 'Oche update needs attention';
    description.textContent = reason;
    spinner.hidden = true;
    returnButton.hidden = false;
    returnButton.focus();
  }

  function observe(data) {
    latest = data;
    const oche = data.modules.find(item => item.name === 'oche');
    if (!waiting && (oche?.pending || (data.job.status === 'running' && data.job.module === 'oche'))) begin(data, true);
    if (!waiting || !waiting.accepted) return;
    if (oche?.pending) waiting.sawPending = true;
    const restarted = waiting.boot && data.boot_id && waiting.boot !== data.boot_id;
    if (waiting.jobId && data.job.id !== waiting.jobId && !restarted && !oche?.pending) return;
    if (restarted && !oche?.pending) {
      if (oche?.error) {
        fail(`Oche restored the previous version. ${oche.error}`);
      } else {
        stop();
        heading.textContent = 'Oche is ready';
        description.textContent = 'Reloading the interface…';
        location.reload(); // Reload the outer shell and all retained views.
      }
      return;
    }
    if (data.job.status === 'error' && !oche?.pending) {
      fail(data.job.message || 'The update failed. Your previous version is still available.');
      return;
    }
    if (waiting.sawPending && !oche?.pending && data.boot_id) {
      // A page first opened during the new process's health check has no old
      // boot ID to compare, but the launcher has now accepted the activation.
      if (oche?.error) fail(oche.error);
      else { stop(); location.reload(); }
      return;
    }
    if (data.job.status === 'complete' && data.job.action !== 'check' && !oche?.pending) {
      // No-op update or a batch with no Oche update left to install.
      stop();
      dialog.close();
      return;
    }
    heading.textContent = oche?.pending ? 'Restarting Oche' : 'Updating applications';
    description.textContent = oche?.pending ? 'Waiting for Oche to start and pass its health check…' :
      data.job.message === 'update' || data.job.message === 'update-all' ? 'Preparing the update…' : data.job.message || 'Preparing the update…';
  }

  async function poll() {
    if (!waiting || polling) return;
    polling = true;
    try {
      const response = await fetch('/updates/status', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('Restarting');
      observe(await response.json());
    } catch (_) {
      if (waiting) {
        heading.textContent = 'Reconnecting to Oche';
        description.textContent = 'Oche is restarting. This page will reconnect automatically.';
      }
    } finally {
      polling = false;
      if (waiting && Date.now() - waiting.started > 180000) {
        description.textContent = 'This is taking longer than expected. Still waiting for Oche; you can also retry loading the interface.';
        returnButton.hidden = false;
      }
    }
  }

  window.ocheUpdateWait = {begin, observe, fail, accepted(jobId) {
    if (waiting) { waiting.accepted = true; waiting.jobId = jobId; }
  }};
  document.addEventListener('oche:updates', event => observe(event.detail));
})();
