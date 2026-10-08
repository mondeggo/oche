(() => {
  const status = document.getElementById('update-status');
  const modules = document.getElementById('update-modules');
  const check = document.getElementById('check-updates');
  const updateAll = document.getElementById('update-all');
  const installationNotice = document.getElementById('update-installation-notice');
  const labels = {
    autodarts: ['AutoDarts', 'Board detection & calibration'],
    autoglow: ['AutoGlow', 'Lighting & effects'],
    ochecore: ['OcheCore', 'Caller, scores & integrations'],
    oche: ['Oche', 'Web interface & supervisor']
  };
  const rows = new Map();
  let submitting = false;
  let refreshing = false;
  let requestError = '';
  function element(parent, tag, value, className) {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    parent.append(node);
    return node;
  }
  function message(value, state = '') {
    status.textContent = value;
    status.dataset.state = state;
  }
  async function action(path) {
    submitting = true;
    requestError = '';
    document.querySelectorAll('#update-modules button, #check-updates, #update-all').forEach(b => b.disabled = true);
    try {
      const response = await fetch(path, {method: 'POST'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Update request failed');
    } catch (error) {
      requestError = error.message;
    } finally {
      submitting = false;
      await refresh();
    }
  }
  function version(parent, label, value, fallback) {
    const cell = element(parent, 'div', '', 'update-version');
    element(cell, 'span', label, 'update-mobile-label');
    const short = value && /^[a-f0-9]{40,64}$/i.test(value) ? value.slice(0, 12) : value;
    const display = element(cell, value ? 'code' : 'span', short || fallback);
    if (value) display.title = value;
  }
  function renderRow(item, data, busy) {
    let row = rows.get(item.name);
    if (!row) {
      const article = element(modules, 'article', '', 'update-row');
      article.setAttribute('aria-label', labels[item.name]?.[0] || item.name);
      row = {article, signature: ''};
      rows.set(item.name, row);
    }
    const signature = JSON.stringify([item, data.enabled, busy, data.job.module, data.job.status]);
    if (signature === row.signature) return;
    const open = row.article.querySelector('details')?.open || false;
    const focusedAction = row.article.contains(document.activeElement) ? document.activeElement.dataset.action : null;
    row.signature = signature;
    row.article.replaceChildren();
    const article = row.article;
    const service = element(article, 'div', '', 'update-service');
    const [name, description] = labels[item.name] || [item.name, ''];
    element(service, 'h2', name);
    element(service, 'p', description);
    const active = item.active || item.bundled_revision || item.bundled;
    const available = item.available && item.available !== active;
    const working = item.pending || (data.job.status === 'running' && data.job.module === item.name);
    const badge = element(service, 'span', working ? 'Updating…' : item.error ? 'Needs attention' : available ? 'Update available' : item.available ? 'Up to date' : 'Not checked', 'update-badge');
    badge.dataset.state = working ? 'pending' : item.error ? 'error' : available ? 'available' : '';
    const readable = value => value && !/^[a-f0-9]{40,64}$/i.test(value) ? value : null;
    version(article, 'Installed', item.active_version || readable(active), 'Version unavailable');
    version(article, 'Available', item.available_version || readable(item.available), item.available ? 'Version unavailable' : 'Not checked');
    const actions = element(article, 'div', '', 'update-actions');
    for (const [verb, label, enabled, explanation] of [
      ['update', 'Update', available, item.available ? 'This release is already installed.' : 'Check for an available release first.'],
      ['rollback', 'Roll back', item.previous, 'No previous version is installed.']
    ]) {
      const button = element(actions, 'button', label, 'update-' + verb);
      button.type = 'button';
      button.dataset.action = verb;
      button.setAttribute('aria-label', `${label} ${name}`);
      button.disabled = !data.enabled || busy || !enabled;
      button.title = !data.enabled ? 'Start Oche through the stable launcher to install releases.' : busy ? 'Wait for the current operation to finish.' : !enabled ? explanation : `${label} ${name}`;
      button.addEventListener('click', () => action(`/updates/${item.name}/${verb}`));
    }
    const details = element(article, 'details', '', 'update-details');
    details.open = open;
    element(details, 'summary', 'Version history');
    element(details, 'p', `Bundled: ${item.bundled_version || readable(item.bundled) || 'Version unavailable'}`);
    element(details, 'p', `Previous: ${item.previous_version || readable(item.previous) || (item.previous ? 'Version unavailable' : 'No previous version')}`);
    for (const [label, revision] of [['Installed', item.active_revision], ['Bundled', item.bundled_revision], ['Available', item.available_revision], ['Previous', item.previous_revision]]) {
      if (revision) element(details, 'p', `${label} revision: ${revision}`);
    }
    element(details, 'p', `Stored build IDs: ${(item.installed || []).join(', ') || 'None in persistent storage'}`);
    if (item.error) element(article, 'p', item.error, 'update-error');
    if (focusedAction) article.querySelector(`[data-action="${focusedAction}"]`)?.focus();
  }
  async function refresh() {
    if (submitting || refreshing) return;
    refreshing = true;
    try {
      const response = await fetch('/updates/status', {cache: 'no-store'});
      if (!response.ok) throw new Error('Status unavailable');
      const data = await response.json();
      document.dispatchEvent(new CustomEvent('oche:updates', {detail: data}));
      if (submitting) return;
      const busy = data.job.status === 'running' || data.modules.some(m => m.pending);
      const count = data.modules.filter(item => item.available && item.available !== (item.active || item.bundled_revision || item.bundled)).length;
      const availability = count ? `${count} ${count === 1 ? 'package available' : 'packages available'} to update.` : 'All packages are up to date.';
      updateAll.textContent = count ? `Update all (${count})` : 'Update all';
      updateAll.disabled = !data.enabled || busy || !count;
      updateAll.title = !data.enabled ? 'Start Oche through the stable launcher to install releases.' : busy ? 'Wait for the current operation to finish.' : !count ? 'No packages available to update.' : 'Update all available packages. Oche updates last.';
      check.disabled = busy;
      check.textContent = data.job.status === 'running' && (data.job.action === 'check' || (!data.job.action && !data.job.module)) ? 'Checking…' : 'Check for updates';
      installationNotice.hidden = data.enabled;
      let jobMessage = data.job.status === 'idle' ? 'Check for releases to see what’s available.' : data.job.status === 'complete' ? `${data.job.action === 'update-all' || data.job.module ? 'Update complete.' : 'Check complete.'} ${availability}` : data.job.message;
      if (data.job.action === 'update-all' && data.job.current) {
        const progress = `Package ${data.job.current} of ${data.job.total}`;
        if (data.job.status === 'running') jobMessage = `${progress}: ${data.job.message}`;
        if (data.job.status === 'error') jobMessage = `${progress} failed. Remaining updates stopped. ${data.job.message}`;
      }
      message(requestError || (data.modules.some(m => m.pending) ? 'Restarting and checking application health…' : jobMessage), requestError ? 'error' : busy ? 'running' : data.job.status);
      for (const item of data.modules) renderRow(item, data, busy);
      // A request disables buttons immediately; unchanged rows also need restoring.
      for (const item of data.modules) {
        const row = rows.get(item.name).article;
        row.querySelector('[data-action="update"]').disabled = !data.enabled || busy || !item.available || item.available === (item.active || item.bundled_revision || item.bundled);
        row.querySelector('[data-action="rollback"]').disabled = !data.enabled || busy || !item.previous;
      }
    } catch (_) {
      message('Waiting for Oche to reconnect…', 'running');
      document.querySelectorAll('#update-modules button, #check-updates, #update-all').forEach(b => b.disabled = true);
    } finally {
      refreshing = false;
    }
  }
  check.addEventListener('click', () => action('/updates/check'));
  updateAll.addEventListener('click', () => action('/updates/update-all'));
  refresh();
  setInterval(refresh, 3000);
})();
