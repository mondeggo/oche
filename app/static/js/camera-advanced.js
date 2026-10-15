(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const workspace = window.OcheCameraWorkspace;
  const selection = () => workspace.selection();
  let controls = null, savedProfiles = {}, selectionKey = '', busy = false, loading = false;
  let job = {state: 'idle'}, polling = false, resultKey = '', jobVersion = 0;
  const activeJob = () => job.state === 'running';
  const key = s => JSON.stringify([s.index, s.device, s.revision]);

  async function request(path, method = 'GET', body) {
    const response = await fetch('/autodarts/cameras' + path, {
      method, cache: 'no-store', signal: AbortSignal.timeout(20000),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The camera request failed. Refresh and try again.');
    return data;
  }

  function renderButtons() {
    const s = selection(), locked = busy || activeJob() || job.state === 'recovery_required' || Boolean(controls?.recovery_required);
    $('camera-controls-fields').disabled = locked || !controls || controls.device !== s.device;
    $('camera-profile-save').disabled = locked || !controls || controls.device !== s.device;
    $('camera-profile-apply').disabled = locked || !$('camera-profile-select').value;
    $('camera-profile-delete').disabled = busy || !$('camera-profile-select').value;
    $('camera-calibration-start').disabled = locked || !s.ready || !controls?.controls.length || controls.device !== s.device;
    $('camera-calibration-start').textContent = `Start sweep for Camera ${s.index + 1}`;
    $('camera-calibration-cancel').disabled = busy || !activeJob();
    $('camera-calibration-apply').disabled = busy || job.state !== 'review' || !Object.keys(job.proposal || {}).length;
    $('camera-controls-recover').disabled = busy || activeJob();
    $('camera-target-double').disabled = busy || !s.ready;
    $('camera-target-t20').disabled = busy || !s.ready;
  }

  function renderProfiles() {
    const select = $('camera-profile-select'), previous = select.value;
    select.replaceChildren(new Option('Choose a profile', ''));
    for (const [name, profile] of Object.entries(savedProfiles)) {
      if (profile.device === selection().device) select.add(new Option(name, name));
    }
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
    renderButtons();
  }

  function renderControls() {
    const list = $('camera-controls-list');
    list.replaceChildren();
    for (const control of controls.controls) {
      const label = document.createElement('label');
      label.append(document.createTextNode(control.name.replaceAll('_', ' ')));
      const input = document.createElement(control.kind.includes('menu') || control.kind === 'bool' ? 'select' : 'input');
      input.dataset.control = control.name;
      if (input.tagName === 'SELECT') {
        const menu = control.kind === 'bool' ? {'0': 'Off', '1': 'On'} : control.menu;
        for (const [value, title] of Object.entries(menu)) input.add(new Option(title, value));
      } else {
        input.type = 'number';
        input.min = control.min;
        input.max = control.max;
        input.step = Math.max(1, control.step);
      }
      input.value = String(control.value);
      input.disabled = !control.writable || control.inactive;
      label.append(input);
      if (control.inactive || !control.writable) {
        const hint = document.createElement('small');
        hint.textContent = control.inactive ? 'Controlled by an automatic mode' : 'Unavailable for writing';
        label.append(hint);
      }
      list.append(label);
    }
    $('camera-controls-legend').textContent = `Camera ${selection().index + 1}`;
    $('camera-controls-status').textContent = controls.recovery_required
      ? 'Interrupted changes need restoration before editing controls.'
      : controls.controls.length ? 'Controls read from the camera.' : 'This camera exposes no supported controls.';
    renderButtons();
  }

  async function refresh() {
    if (loading) return;
    const s = selection();
    if (!s.device) return;
    loading = true;
    const requestedKey = key(s);
    try {
      const next = await request(`/controls/${s.index}`);
      if (requestedKey !== key(selection())) return;
      controls = next;
      renderControls();
    } catch (error) {
      if (requestedKey === key(selection())) {
        controls = null;
        $('camera-controls-list').replaceChildren();
        $('camera-controls-status').textContent = error.message;
      }
    } finally {
      loading = false; renderButtons();
      if (requestedKey !== key(selection())) queueMicrotask(refresh);
    }
  }

  async function action(work, status = 'camera-controls-status') {
    if (busy) return;
    busy = true;
    renderButtons();
    try { await work(); }
    catch (error) { $(status).textContent = error.message; }
    finally { busy = false; renderButtons(); }
  }

  async function reloadProfiles() {
    savedProfiles = await request('/profiles');
    renderProfiles();
  }

  function renderJob(next) {
    jobVersion++;
    job = next;
    $('camera-calibration-progress').value = next.progress || 0;
    $('camera-calibration-status').textContent = (next.index === undefined ? '' : `Camera ${next.index + 1}: `) +
      (next.message || 'Ready. Each sweep is limited to three minutes.');
    $('camera-calibration-proposal').textContent = Object.keys(next.proposal || {}).length
      ? 'Proposed controls: ' + Object.entries(next.proposal).map(([name, value]) => `${name}: ${value}`).join(', ') +
        ` ? Before score ${next.baseline_score.toFixed(1)}, proposed ${next.proposed_score.toFixed(1)}.` : '';
    const signature = JSON.stringify([next.id, next.state, next.samples?.length]);
    if (signature !== resultKey) {
      resultKey = signature;
      const results = $('camera-calibration-results');
      results.replaceChildren();
      if (next.samples?.length) {
        const table = document.createElement('table');
        const header = table.createTHead().insertRow();
        for (const title of ['Sample', 'Controls', 'Detail score', 'Brightness', 'Clipped dark / bright']) {
          const th = document.createElement('th'); th.scope = 'col'; th.textContent = title; header.append(th);
        }
        const body = table.createTBody();
        next.samples.forEach((sample, index) => {
          const row = body.insertRow();
          for (const value of [index ? String(index) : 'Before',
            Object.entries(sample.values).map(([name, value]) => `${name}: ${value}`).join(', ') || 'Original controls',
            sample.score.toFixed(1), sample.brightness.toFixed(0),
            `${(sample.clipped_dark * 100).toFixed(0)}% / ${(sample.clipped_bright * 100).toFixed(0)}%`]) row.insertCell().textContent = value;
        });
        results.append(table);
      }
    }
    renderButtons();
  }

  async function poll() {
    const s = selection(), nextKey = key(s);
    if (nextKey !== selectionKey) {
      selectionKey = nextKey;
      controls = null;
      $('camera-controls-list').replaceChildren();
      renderProfiles();
      refresh();
    }
    renderButtons();
    if (polling || document.hidden) return;
    polling = true;
    try {
      const wasRunning = activeJob(), version = jobVersion;
      const next = await request('/calibration');
      if (version !== jobVersion) return;
      renderJob(next);
      if (wasRunning && !activeJob()) { workspace.resetFocus(); refresh(); }
    } catch (error) { $('camera-calibration-status').textContent = 'Could not read calibration status. ' + error.message; }
    finally { polling = false; }
  }

  async function findTarget(mode) {
    const before = selection();
    $('camera-target-status').textContent = 'Finding focus target…';
    const target = await request(`/target/${before.index}?mode=${mode}`);
    const after = selection();
    if (key(before) !== key(after) || (target.revision && target.revision !== after.revision) || target.width !== after.width || target.height !== after.height) {
      throw new Error('The camera view changed. Find the target again.');
    }
    workspace.setROI(target.roi);
    $('camera-target-status').textContent = `${mode === 't20' ? 'T20' : 'Furthest double'}: ${target.method}. The selection stays fixed until you move it.`;
  }

  $('camera-target-double').addEventListener('click', () => action(() => findTarget('furthest_double'), 'camera-target-status'));
  $('camera-target-t20').addEventListener('click', () => action(() => findTarget('t20'), 'camera-target-status'));
  $('camera-controls-refresh').addEventListener('click', refresh);
  $('camera-controls-form').addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
      if (!controls || controls.device !== selection().device) throw new Error('Reload camera controls first.');
      const values = {};
      for (const input of document.querySelectorAll('[data-control]')) {
        const current = controls.controls.find(c => c.name === input.dataset.control);
        if (!input.disabled && Number(input.value) !== current.value) values[current.name] = Number(input.value);
      }
      if (!Object.keys(values).length) return;
      await request(`/controls/${selection().index}`, 'PATCH', {device: controls.device, values});
      workspace.resetFocus();
      await refresh();
    });
  });
  $('camera-controls-defaults').addEventListener('click', () => action(async () => {
    const s = selection();
    await request(`/controls/${s.index}/defaults`, 'POST', {device: s.device});
    workspace.resetFocus(); await refresh();
  }));
  $('camera-controls-recover').addEventListener('click', () => action(async () => {
    await request('/controls/recover', 'POST'); workspace.resetFocus(); await refresh(); await poll();
  }));
  $('camera-profile-save').addEventListener('click', () => action(async () => {
    const s = selection(), name = $('camera-profile-name').value.trim();
    if (!name || !/^[\p{L}\p{N}_ .-]{1,64}$/u.test(name)) throw new Error('Enter a profile name using letters, numbers, spaces, dots, dashes or underscores.');
    await request(`/profiles/${s.index}`, 'POST', {device: s.device, name});
    await reloadProfiles(); $('camera-profile-select').value = name;
    $('camera-controls-status').textContent = 'Current camera controls saved as a profile.';
  }));
  $('camera-profile-apply').addEventListener('click', () => action(async () => {
    const s = selection();
    await request(`/profiles/${s.index}/apply`, 'POST', {device: s.device, name: $('camera-profile-select').value});
    workspace.resetFocus(); await refresh();
  }));
  $('camera-profile-delete').addEventListener('click', () => action(async () => {
    const name = $('camera-profile-select').value;
    if (!name) return;
    await request('/profiles/' + encodeURIComponent(name), 'DELETE'); await reloadProfiles();
  }));
  $('camera-profile-select').addEventListener('change', renderButtons);
  $('camera-calibration-start').addEventListener('click', () => action(async () => {
    const s = selection();
    renderJob(await request(`/calibration/start/${s.index}`, 'POST', {device: s.device, roi: s.roi}));
    workspace.resetFocus();
  }, 'camera-calibration-status'));
  $('camera-calibration-cancel').addEventListener('click', () => action(async () => {
    renderJob(await request('/calibration/cancel', 'POST'));
  }, 'camera-calibration-status'));
  $('camera-calibration-apply').addEventListener('click', () => action(async () => {
    await request(`/calibration/apply/${job.id}`, 'POST'); workspace.resetFocus(); await refresh(); await poll();
  }, 'camera-calibration-status'));
  reloadProfiles().catch(error => { $('camera-controls-status').textContent = error.message; });
  setInterval(poll, 2000);
  poll();
})();
