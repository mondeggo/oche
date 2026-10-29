(() => {
  const status = document.getElementById('update-status');
  const modules = document.getElementById('update-modules');
  const check = document.getElementById('check-updates');
  const updateAll = document.getElementById('update-all');
  const installationNotice = document.getElementById('update-installation-notice');
  const labels = {
    autodarts: ['Autodarts', tr("Board detection & calibration")],
    oche: ['Oche', tr("Application, caller, lighting & scoreboard")]
  };
  const needsUpdate = item => item.has_update ?? Boolean(item.available && item.available !== (item.active || item.bundled_revision || item.bundled));
  const rows = new Map();
  let submitting = false;
  let refreshing = false;
  let requestError = '';
  let latestData = null;
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
    const selfUpdate = path.startsWith('/updates/oche/') || (path === '/updates/update-all' && latestData?.modules.some(item => item.name === 'oche' && needsUpdate(item) && item.runtime_compatible !== false));
    if (selfUpdate) window.ocheUpdateWait?.begin(latestData);
    document.querySelectorAll('#update-modules button, #check-updates, #update-all').forEach(b => b.disabled = true);
    try {
      const response = await fetch(path, {method: 'POST'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || tr("Update request failed"));
      if (selfUpdate) window.ocheUpdateWait?.accepted(result.job_id);
    } catch (error) {
      requestError = error.message;
      if (selfUpdate) window.ocheUpdateWait?.fail(error.message);
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
    const available = needsUpdate(item);
    const incompatible = available && item.runtime_compatible === false;
    const working = item.pending || (data.job.status === 'running' && data.job.module === item.name);
    const badge = element(service, 'span', working ? tr("Updating…") : item.error || item.discovery_error ? tr("Needs attention") : incompatible ? tr("Docker image upgrade required") : available ? tr("Update available") : item.available ? tr("Up to date") : tr("Not checked"), 'update-badge');
    badge.dataset.state = working || incompatible ? 'pending' : item.error || item.discovery_error ? 'error' : available ? 'available' : '';
    const readable = value => value && !/^[a-f0-9]{40,64}$/i.test(value) ? value : null;
    version(article, tr("Installed"), item.active_version || readable(active), tr("Version unavailable"));
    version(article, tr("Available"), item.available_version || readable(item.available), item.discovery_error ? tr("Check failed") : item.available ? tr("Version unavailable") : tr("Not checked"));
    const actions = element(article, 'div', '', 'update-actions');
    for (const [verb, label, enabled, explanation] of [
      ['update', tr("Update"), available && !incompatible, incompatible ? tr("Upgrade the Docker image before installing this package.") : item.available ? tr("This release is already installed.") : tr("Check for an available release first.")],
      ['rollback', tr("Roll back"), item.previous, tr("No previous version is installed.")]
    ]) {
      const button = element(actions, 'button', label, 'update-' + verb);
      button.type = 'button';
      button.dataset.action = verb;
      button.setAttribute('aria-label', `${label} ${name}`);
      button.disabled = !data.enabled || busy || !enabled;
      button.title = !data.enabled ? tr("Start Oche through the stable launcher to install releases.") : busy ? tr("Wait for the current operation to finish.") : !enabled ? explanation : `${label} ${name}`;
      button.addEventListener('click', () => action(`/updates/${item.name}/${verb}`));
    }
    const details = element(article, 'details', '', 'update-details');
    details.open = open;
    element(details, 'summary', tr("Version history"));
    element(details, 'p', tr("Bundled: {0}", {"0": (item.bundled_version || readable(item.bundled) || tr("Version unavailable"))}));
    element(details, 'p', tr("Previous: {0}", {"0": (item.previous_version || readable(item.previous) || (item.previous ? tr("Version unavailable") : tr("No previous version")))}));
    for (const [label, revision] of [[tr("Installed"), item.active_revision], [tr("Bundled"), item.bundled_revision], [tr("Available"), item.available_revision], [tr("Previous"), item.previous_revision]]) {
      if (revision) element(details, 'p', `${label} revision: ${revision}`);
    }
    element(details, 'p', tr("Stored build IDs: {0}", {"0": ((item.installed || []).join(', ') || tr("None in persistent storage"))}));
    element(details, 'p', tr("Release source: {0}", {"0": (item.source || tr("Oche releases"))}));
    if (item.verification) element(details, 'p', tr("Verification: {0}", {"0": (item.verification)}));
    if (item.error || item.discovery_error) element(article, 'p', item.error || item.discovery_error, 'update-error');
    if (focusedAction) article.querySelector(`[data-action="${focusedAction}"]`)?.focus();
  }
  async function refresh() {
    if (submitting || refreshing) return;
    refreshing = true;
    try {
      const response = await fetch('/updates/status', {cache: 'no-store'});
      if (!response.ok) throw new Error(tr("Status unavailable"));
      const data = await response.json();
      latestData = data;
      document.dispatchEvent(new CustomEvent('oche:updates', {detail: data}));
      if (submitting) return;
      const busy = data.job.status === 'running' || data.modules.some(m => m.pending);
      const count = data.modules.filter(needsUpdate).length;
      const blocked = data.modules.filter(item => needsUpdate(item) && item.runtime_compatible === false).length;
      const installable = count - blocked;
      const failedChecks = Object.keys(data.check_errors || {}).length;
      const availability = (count ? `${count} ${count === 1 ? 'package available' : 'packages available'} to update.` : failedChecks ? tr("No updates found from reachable sources.") : tr("All packages are up to date.")) + (failedChecks ? ` ${failedChecks} package checks failed; see their rows.` : '') + (blocked ? ` ${blocked} ${blocked === 1 ? 'requires' : 'require'} a Docker image upgrade.` : '');
      updateAll.textContent = installable ? tr("Update all ({0})", {"0": (installable)}) : tr("Update all");
      updateAll.disabled = !data.enabled || busy || !installable;
      updateAll.title = !data.enabled ? tr("Start Oche through the stable launcher to install releases.") : busy ? tr("Wait for the current operation to finish.") : !installable ? (blocked ? tr("Available packages require a Docker image upgrade.") : tr("No packages available to update.")) : tr("Update compatible packages. Oche updates last.");
      check.disabled = busy;
      check.textContent = data.job.status === 'running' && (data.job.action === 'check' || (!data.job.action && !data.job.module)) ? tr("Checking…") : tr("Check for updates");
      installationNotice.hidden = data.enabled;
      let jobMessage = data.job.status === 'idle' ? tr("Check for releases to see what’s available.") : data.job.status === 'complete' ? `${data.job.action === 'update-all' || data.job.module ? 'Update complete.' : 'Check complete.'} ${availability}` : data.job.message;
      if (data.job.status === 'running' && (data.job.action === 'check' || data.job.message === 'check')) {
        jobMessage = tr("Checking for updates…");
      }
      if (data.job.action === 'update-all' && data.job.current) {
        const progress = tr("Package {0} of {1}", {"0": (data.job.current), "1": (data.job.total)});
        if (data.job.status === 'running') jobMessage = `${progress}: ${data.job.message}`;
        if (data.job.status === 'error') jobMessage = `${progress} failed. Remaining updates stopped. ${data.job.message}`;
      }
      message(requestError || (data.modules.some(m => m.pending) ? tr("Restarting and checking application health…") : jobMessage), requestError ? 'error' : busy ? 'running' : data.job.status);
      for (const item of data.modules) renderRow(item, data, busy);
      // A request disables buttons immediately; unchanged rows also need restoring.
      for (const item of data.modules) {
        const row = rows.get(item.name).article;
        row.querySelector('[data-action="update"]').disabled = !data.enabled || busy || item.runtime_compatible === false || !needsUpdate(item);
        row.querySelector('[data-action="rollback"]').disabled = !data.enabled || busy || !item.previous;
      }
    } catch (_) {
      message(tr("Waiting for Oche to reconnect…"), 'running');
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
