// Read cached release information; navigating never starts an update check.
(() => {
  const panel = document.querySelector('[data-service-update]');
  // Cached-page navigation replaces navbar markup, so resolve current dots.
  const dots = () => document.querySelectorAll('[data-update-dot]');
  const hasUpdate = item => Boolean(item.available && item.available !==
    (item.active || item.bundled_revision || item.bundled));
  function render(data) {
    for (const dot of dots()) {
      const name = dot.dataset.updateDot;
      dot.hidden = !data.modules.some(item => (name === 'updates' || item.name === name) && hasUpdate(item));
    }
    if (!panel) return;
    const item = data.modules.find(item => item.name === panel.dataset.serviceUpdate);
    const available = item && hasUpdate(item);
    const working = item?.pending || (data.job.status === 'running' && data.job.module === item?.name);
    panel.dataset.state = working ? 'running' : available ? 'available' : '';
    panel.querySelector('[data-service-update-dot]').hidden = !available;
    let message = 'Updates not checked yet.';
    if (working) message = 'Update in progress…';
    else if (available) message = `Update available${item.available_version ? ` · ${item.available_version}` : ''}`;
    else if (item?.available) message = 'Up to date · No update available.';
    else if (data.job.status === 'running' && data.job.action === 'check') message = 'Checking for updates…';
    else if (data.job.status === 'error') message = 'Update availability could not be checked.';
    else if (data.job.status === 'complete') message = 'No release information available for this service.';
    panel.querySelector('[data-service-update-message]').textContent = message;
  }
  document.addEventListener('oche:updates', event => render(event.detail));
  // The Updates page already polls this endpoint and shares its response.
  if (document.getElementById('update-modules')) return;
  let loading = false;
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const response = await fetch('/updates/status', {cache: 'no-store'});
      if (!response.ok) throw new Error('Status unavailable');
      render(await response.json());
    } catch (_) {
      dots().forEach(dot => { dot.hidden = true; });
      if (panel) {
        panel.dataset.state = '';
        panel.querySelector('[data-service-update-dot]').hidden = true;
        panel.querySelector('[data-service-update-message]').textContent = 'Update status unavailable. Reconnecting…';
      }
    } finally {
      loading = false;
    }
  }
  refresh();
  setInterval(refresh, 5000);
})();
