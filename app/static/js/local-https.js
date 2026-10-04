(() => {
  const byId = id => document.getElementById(id);
  const status = byId('https-status');
  const domain = byId('https-domain');
  const ip = byId('https-lan-ip');
  const email = byId('https-email');
  const domainStatus = byId('https-domain-status');
  let current;
  let initialized = false;
  let saving = false;
  let refreshTimer;

  function address(scheme, port, host = location.hostname, path = '/config/https') {
    const url = new URL(path, location.href);
    url.protocol = scheme;
    url.hostname = host;
    url.port = String(port);
    return url.href;
  }

  function canonicalDomain() {
    try { return new URL('https://' + domain.value.trim().replace(/\.$/, '')).hostname; }
    catch (_) { return domain.value.trim().toLowerCase(); }
  }

  async function request(path, body) {
    const response = await fetch('/config/https/' + path, body === undefined ? {cache: 'no-store'} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not update HTTPS.');
    return data;
  }

  const date = value => value ? new Date(value).toLocaleString() : '';

  function renderSetup() {
    if (!current) return;
    const acme = current.acme_dns || {};
    const busy = Boolean(acme.busy) || saving;
    const prepared = acme.prepared && canonicalDomain() === acme.domain
      && ip.value.trim() === acme.local_ip && email.value.trim() === acme.email;
    const active = current.running && current.mode === 'letsencrypt' && current.domain === acme.domain;
    byId('https-dns-step').hidden = !prepared;
    byId('https-activate-step').hidden = !prepared;
    byId('https-next-steps').hidden = Boolean(prepared);
    byId('https-next-steps').textContent = acme.prepared
      ? 'Your details have changed. Select Prepare DNS records again to update the next steps.'
      : 'Prepare your domain to see the DNS records and the next steps.';
    byId('https-record-name').textContent = acme.domain || '';
    byId('https-record-ip').textContent = acme.local_ip || '';
    byId('https-cname-name').textContent = acme.cname_name || '';
    byId('https-cname-target').textContent = acme.cname_target || '';
    byId('https-domain-fields').disabled = busy || !acme.available;
    byId('https-prepare').disabled = busy || Boolean(prepared);
    byId('https-prepare').textContent = prepared ? 'DNS records prepared' : 'Prepare DNS records';
    byId('https-domain-submit').disabled = busy || !acme.available || !prepared;
    byId('https-domain-submit').textContent = active ? 'Check DNS and certificate' : 'Verify and activate domain HTTPS';
    byId('https-replace-registration').hidden = !prepared;
    byId('https-replace').disabled = busy || !acme.available;
    const domainLink = byId('https-domain-open');
    domainLink.hidden = !active;
    if (active) {
      domainLink.href = address('https:', current.port, current.domain, '/play');
      domainLink.textContent = 'Open Play: ' + new URL(domainLink.href).origin;
    }
  }

  function render(data) {
    current = data;
    const acme = data.acme_dns || {};
    const busy = Boolean(acme.busy) || saving;
    if (!initialized) {
      domain.value = acme.domain || '';
      email.value = acme.email || '';
      ip.value = acme.local_ip || data.local_ip || '';
      byId('https-terms').checked = Boolean(acme.prepared);
      for (const value of data.local_ips || []) {
        const option = document.createElement('option');
        option.value = value;
        byId('https-local-ips').appendChild(option);
      }
      initialized = true;
    }
    const trusted = data.mode === 'letsencrypt';
    const localActive = data.running && !trusted;
    byId('https-state').textContent = data.running ? (trusted ? 'Domain HTTPS' : 'Local HTTPS') : data.enabled ? 'Needs attention' : 'Off';
    byId('https-local-active').hidden = !data.enabled || trusted;
    byId('https-domain-active').hidden = !data.enabled || !trusted;
    byId('https-local').disabled = busy || localActive;
    byId('https-local').textContent = localActive ? 'Local HTTPS is active' : data.running ? 'Switch to local HTTPS' : 'Enable local HTTPS';
    byId('https-disable').hidden = !data.enabled;
    byId('https-disable').disabled = busy;
    byId('https-retry').hidden = !data.enabled || data.running;
    byId('https-retry').disabled = busy;
    byId('https-retry').textContent = trusted ? 'Retry domain HTTPS' : 'Retry local HTTPS';
    byId('https-expiry').hidden = !data.running || !data.expires;
    byId('https-expiry').textContent = data.expires ? 'Certificate expires: ' + date(data.expires) : '';
    status.textContent = data.error || (data.running
      ? trusted ? 'Domain HTTPS is active for ' + data.domain + '.' : 'Local HTTPS is active. Accept the certificate warning when you first connect in each browser.'
      : data.enabled ? 'HTTPS could not start. Check your settings, then try again.' : 'HTTPS is off. Choose a method below to enable it.');
    byId('https-connection').textContent = 'This page is using ' + (location.protocol === 'https:' ? 'HTTPS' : 'HTTP') + ': ' + location.origin;
    const link = byId('https-open');
    const host = trusted ? data.domain : (data.local_ip || location.hostname);
    link.hidden = !data.running;
    if (data.running) {
      link.href = address('https:', data.port, host);
      link.textContent = 'Open ' + (trusted ? 'domain' : 'local') + ' HTTPS: ' + new URL(link.href).origin;
      link.hidden = new URL(link.href).origin === location.origin;
    }
    const httpLink = byId('http-open');
    httpLink.hidden = location.protocol !== 'https:';
    httpLink.href = address('http:', data.http_port, data.local_ip || location.hostname);
    byId('https-service').textContent = acme.server || 'https://auth.acme-dns.io';
    const phases = {
      preparing: 'Setting up acme-dns and preparing your DNS records…',
      issuing: 'Checking DNS and requesting your certificate. This may take a few minutes. You can leave this page while Oche finishes.',
      renewing: 'Checking whether your domain certificate needs renewal…',
    };
    domainStatus.textContent = acme.busy ? phases[acme.phase] || 'Updating the certificate…'
      : acme.error || acme.renewal_error || (!acme.available ? 'Domain certificates need a newer Oche installation. Update Oche, or rebuild its Docker image, to continue.'
        : acme.last_success ? 'Last successful certificate check: ' + date(acme.last_success)
          : acme.prepared ? 'Your DNS records are ready. Add both records to your DNS settings, then select Verify and activate domain HTTPS.' : '');
    byId('https-renewal-status').textContent = data.enabled && trusted
      ? (acme.next_check ? 'Next automatic certificate check: ' + date(acme.next_check) : 'Automatic renewal checks are enabled.')
      : 'Automatic renewal starts when you activate domain HTTPS.';
    renderSetup();
  }

  async function changeHTTPS(enabled, mode) {
    saving = true;
    if (current) render(current);
    status.textContent = enabled ? 'Activating ' + (mode === 'letsencrypt' ? 'domain' : 'local') + ' HTTPS…' : 'Turning HTTPS off…';
    try {
      const data = await request('status', {enabled, mode});
      saving = false;
      render(data);
      if (!data.enabled && location.protocol === 'https:') {
        window.top.location.href = address('http:', data.http_port, data.local_ip || location.hostname);
      }
    } catch (error) {
      saving = false;
      if (current) render(current);
      status.textContent = error.message;
    }
  }

  async function certificateRequest(path, body) {
    saving = true;
    if (current) render(current);
    domainStatus.textContent = path === 'prepare' ? 'Preparing DNS records…' : 'Starting DNS verification…';
    try {
      await request(path, body);
      saving = false;
      render(await request('status'));
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(refresh, 2000);
    } catch (error) {
      saving = false;
      if (current) render(current);
      domainStatus.textContent = error.message;
    }
  }

  function prepare(replace = false) {
    if (!byId('https-domain-form').reportValidity()) return;
    certificateRequest('prepare', {
      domain: domain.value.trim(), email: email.value.trim(), local_ip: ip.value.trim(),
      terms_accepted: byId('https-terms').checked, replace_registration: replace,
    });
  }

  byId('https-local').addEventListener('click', () => changeHTTPS(true, 'local'));
  byId('https-disable').addEventListener('click', () => changeHTTPS(false, current.mode || 'local'));
  byId('https-retry').addEventListener('click', () => changeHTTPS(true, current.mode || 'local'));
  [domain, ip, email].forEach(input => input.addEventListener('input', renderSetup));
  byId('https-domain-form').addEventListener('submit', event => { event.preventDefault(); prepare(); });
  byId('https-replace').addEventListener('click', () => prepare(true));
  byId('https-domain-submit').addEventListener('click', () => certificateRequest('certificate', {domain: current.acme_dns.domain}));
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const text = byId(button.dataset.copy).textContent;
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
      else {
        const field = document.createElement('textarea');
        field.value = text;
        field.style.position = 'fixed';
        field.style.opacity = '0';
        document.body.appendChild(field);
        field.select();
        const copied = document.execCommand('copy');
        field.remove();
        if (!copied) throw new Error('Copy unavailable');
      }
      domainStatus.textContent = 'Copied to clipboard.';
    } catch (_) { domainStatus.textContent = 'Select the record value above and copy it manually.'; }
  }));

  async function refresh() {
    try {
      if (!saving) render(await request('status'));
    } catch (_) {
      status.textContent = 'Could not read HTTPS status. Check the connection or reload this page.';
    } finally {
      refreshTimer = setTimeout(refresh, current?.acme_dns?.busy ? 2000 : 30000);
    }
  }
  refresh();
})();
