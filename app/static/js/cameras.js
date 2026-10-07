(() => {
  const $ = id => document.getElementById(id);
  const api = '/autodarts/cameras';
  const focus = window.OcheCameraFocus;
  const meter = new focus.Meter({ alpha: .25 });
  const canvas = $('camera-focus-canvas');
  const context = canvas.getContext('2d');
  const zoom = $('camera-zoom-canvas');
  const zoomContext = zoom.getContext('2d');
  const source = document.createElement('canvas');
  const sourceContext = source.getContext('2d', { willReadFrequently: true });
  const defaultROI = () => ({ x: .35, y: .35, width: .3, height: .3 });
  const slots = Array.from({ length: 3 }, (_, index) => ({
    index, image: $('camera-image-' + index), state: $('camera-image-state-' + index),
    bitmap: null, url: null, controller: null, lastAttempt: 0,
  }));
  let data = null, draft = null, draftRevision = null, selected = 0;
  let roi = defaultROI(), paused = false, visible = true, saving = false;
  let loadingData = false, lastDataAttempt = -Infinity, running = false;
  let generation = 0, dataGeneration = 0, lastMode = '', lastResult = null, drag = null;
  let settingsRenderKey = '';
  let audio = null, oscillator = null, gain = null;

  async function request(path, options = {}) {
    const response = await fetch(api + path, { cache: 'no-store', ...options });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = typeof body.detail === 'string' ? body.detail : 'The camera request failed. Please try again.';
      throw new Error(detail);
    }
    return response.json();
  }

  function hasUnsavedChanges() {
    return draft && data && (draft.width !== data.settings.width || draft.height !== data.settings.height ||
      draft.fps !== data.settings.fps || draft.devices.some((id, index) => id !== data.settings.devices[index]));
  }

  function option(select, value, label) {
    const node = document.createElement('option');
    node.value = String(value);
    node.textContent = label;
    select.appendChild(node);
  }

  function cameraDevices() {
    return draft.devices.map(id => data.devices.find(device => device.id === id));
  }

  function settingsKey() {
    return JSON.stringify([data.devices, data.settings, draft, running, saving]);
  }

  function sharedFPS(devices, width, height) {
    const rates = devices.map(device => (device?.modes || [])
      .filter(mode => mode.width === width && mode.height === height)
      .flatMap(mode => mode.fps || []).map(Number).filter(value => Number.isInteger(value) && value > 0));
    return [...new Set(rates[0])].filter(rate => rates.every(list => list.includes(rate))).sort((a, b) => a - b);
  }

  function updateModes() {
    const devices = cameraDevices();
    const unique = draft.devices.every(Boolean) && new Set(draft.devices).size === 3;
    const known = devices.every(device => device?.modes?.length);
    let common = known ? devices[0].modes.filter(mode => devices.every(device =>
      device.modes.some(candidate => candidate.width === mode.width && candidate.height === mode.height))) : [];
    common = common.filter((mode, index) => common.findIndex(other => other.width === mode.width && other.height === mode.height) === index && sharedFPS(devices, mode.width, mode.height).length);
    const resolution = $('camera-resolution');
    resolution.replaceChildren();
    for (const mode of common) option(resolution, `${mode.width}x${mode.height}`, `${mode.width} × ${mode.height}`);
    const currentResolution = `${draft.width}x${draft.height}`;
    const supportedResolution = common.some(mode => `${mode.width}x${mode.height}` === currentResolution);
    if (!supportedResolution) option(resolution, currentResolution, `${draft.width} × ${draft.height} (not available for this selection)`);
    resolution.value = currentResolution;
    const sharedRates = supportedResolution ? sharedFPS(devices, draft.width, draft.height) : [];
    const fps = $('camera-fps');
    fps.replaceChildren();
    for (const rate of sharedRates) option(fps, rate, `${rate} FPS`);
    if (!sharedRates.includes(draft.fps)) option(fps, draft.fps, `${draft.fps} FPS (not available for this selection)`);
    fps.value = String(draft.fps);
    let hint = 'Resolution and FPS must be supported by all three selected cameras.';
    if (!unique) hint = 'Choose three different cameras.';
    else if (!known) hint = 'Supported modes are unavailable for a selected camera. Refresh devices or choose an available camera before applying.';
    else if (!common.length) hint = 'These cameras have no shared resolution and FPS combination.';
    else if (!supportedResolution) hint = 'Choose a resolution supported by all three cameras.';
    else if (!sharedRates.length) hint = 'These cameras have no shared FPS at this resolution.';
    else if (!sharedRates.includes(draft.fps)) hint = 'Choose an FPS supported by all three cameras.';
    $('camera-settings-hint').textContent = hint;
    $('camera-apply').disabled = saving || !running || !unique || !known || !supportedResolution || !sharedRates.includes(draft.fps) || !hasUnsavedChanges();
    settingsRenderKey = settingsKey();
  }

  function renderSettings() {
    for (let index = 0; index < 3; index++) {
      const select = $('camera-device-' + index);
      select.replaceChildren();
      option(select, '', 'Choose a camera');
      for (const device of data.devices) option(select, device.id, device.label || device.id);
      const saved = draft.devices[index];
      if (saved && !data.devices.some(device => device.id === saved)) option(select, saved, `${saved} (not available)`);
      select.value = saved || '';
    }
    updateModes();
  }

  function showData(next, replaceDraft = false) {
    const dirty = hasUnsavedChanges();
    const mode = JSON.stringify(next.settings);
    if (lastMode && lastMode !== mode) {
      resetFrames();
    }
    lastMode = mode;
    data = next;
    running = next.running;
    if (!draft || replaceDraft || !dirty) {
      draft = { devices: [...next.settings.devices], width: next.settings.width, height: next.settings.height, fps: next.settings.fps };
      draftRevision = next.revision;
    }
    $('camera-settings-fields').disabled = saving || !running;
    // An unchanged status poll should not disturb an open native select menu.
    if (settingsRenderKey !== settingsKey()) renderSettings();
    $('camera-status').textContent = paused ? 'Preview paused. Autodarts continues running.' : next.capturing === false
      ? 'Autodarts is running but is not currently capturing camera images.' : 'Live camera views from Autodarts. Select a camera below to focus it.';
    const warnings = $('camera-warnings');
    warnings.replaceChildren();
    for (const warning of next.warnings || []) {
      const item = document.createElement('li');
      item.textContent = warning;
      warnings.appendChild(item);
    }
    warnings.hidden = !warnings.children.length;
    for (let index = 0; index < 3; index++) {
      const camera = next.cameras.find(value => value.index === index);
      const delivered = Number.isFinite(camera?.fps) ? `${Number(camera.fps.toFixed(1))} engine FPS` : 'Engine FPS unavailable';
      const resolution = next.capture_resolution;
      $('camera-health-' + index).textContent = `${delivered} · ${next.settings.fps} requested` + (resolution ? ` · ${resolution.width} × ${resolution.height} actual` : '');
    }
  }

  async function loadData(replaceDraft = false) {
    if (loadingData || saving) return;
    loadingData = true;
    const requestedGeneration = dataGeneration;
    lastDataAttempt = performance.now();
    $('camera-refresh').disabled = true;
    try {
      const next = await request('/data', { signal: AbortSignal.timeout(8000) });
      if (requestedGeneration !== dataGeneration) return;
      showData(next, replaceDraft);
    } catch (error) {
      if (requestedGeneration !== dataGeneration) return;
      running = false;
      resetFrames();
      $('camera-status').textContent = error.message || 'Could not reach Autodarts. Start it in Supervisor, then refresh.';
      $('camera-settings-fields').disabled = true;
      mute();
    } finally {
      loadingData = false;
      $('camera-refresh').disabled = saving;
    }
  }

  function resetFrames() {
    generation++;
    cancelDrag();
    resetFocus();
    for (const slot of slots) {
      slot.controller?.abort();
      slot.bitmap?.close();
      slot.bitmap = null;
      if (slot.url) URL.revokeObjectURL(slot.url);
      slot.url = null;
      slot.image.removeAttribute('src');
      slot.image.hidden = true;
      slot.lastAttempt = 0;
      slot.state.hidden = false;
      slot.state.textContent = 'Waiting for an image';
    }
    context.clearRect(0, 0, canvas.width, canvas.height);
    zoomContext.clearRect(0, 0, zoom.width, zoom.height);
    $('camera-focus-empty').hidden = false;
  }

  function resetFocus() {
    meter.reset();
    lastResult = null;
    $('camera-focus-current').textContent = '—';
    $('camera-focus-best').textContent = '—';
    $('camera-focus-meter').value = 0;
    $('camera-focus-trend').textContent = 'Keep the selected area still while adjusting the lens.';
    mute();
  }

  function drawFocus() {
    const bitmap = slots[selected].bitmap;
    if (!bitmap) return;
    if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      resetFocus();
    }
    context.drawImage(bitmap, 0, 0);
    const x = roi.x * canvas.width, y = roi.y * canvas.height;
    const width = roi.width * canvas.width, height = roi.height * canvas.height;
    const color = getComputedStyle(document.documentElement).getPropertyValue('--brand').trim();
    context.strokeStyle = color;
    context.lineWidth = Math.max(2, canvas.width / 300);
    context.strokeRect(x, y, width, height);
    const scale = Number($('camera-zoom').value);
    const cropWidth = Math.min(bitmap.width, Math.max(width, height * zoom.width / zoom.height) / scale);
    const cropHeight = Math.min(bitmap.height, cropWidth * zoom.height / zoom.width);
    const cropX = Math.min(bitmap.width - cropWidth, Math.max(0, x + width / 2 - cropWidth / 2));
    const cropY = Math.min(bitmap.height - cropHeight, Math.max(0, y + height / 2 - cropHeight / 2));
    zoomContext.drawImage(bitmap, cropX, cropY, cropWidth, cropHeight, 0, 0, zoom.width, zoom.height);
    $('camera-focus-empty').hidden = true;
  }

  function analyze(bitmap) {
    if (drag) return;
    if (source.width !== bitmap.width || source.height !== bitmap.height) {
      source.width = bitmap.width;
      source.height = bitmap.height;
    }
    sourceContext.drawImage(bitmap, 0, 0);
    const x = Math.floor(roi.x * source.width), y = Math.floor(roi.y * source.height);
    const width = Math.max(1, Math.ceil((roi.x + roi.width) * source.width) - x);
    const height = Math.max(1, Math.ceil((roi.y + roi.height) * source.height) - y);
    // Read only the selected pixels. The crop is already the ROI.
    const result = meter.push(focus.analyze(sourceContext.getImageData(x, y, width, height)));
    lastResult = result;
    $('camera-focus-current').textContent = result.usable ? result.current.toFixed(1) : '—';
    $('camera-focus-best').textContent = result.best > 0 ? result.best.toFixed(1) : '—';
    $('camera-focus-meter').value = result.usable ? result.relative : 0;
    const trends = { improving: 'Sharpness is improving.', declining: 'Sharpness has decreased.', steady: 'Sharpness is steady.' };
    $('camera-focus-trend').textContent = result.warning || result.lightingWarning || (result.samples < 4 ? 'Measuring… keep the selection still.' : trends[result.trend] || 'Waiting for usable detail.');
    updateAudio();
  }

  async function loadFrame(slot) {
    if (slot.controller) return;
    const controller = new AbortController();
    slot.controller = controller;
    slot.lastAttempt = performance.now();
    const requestedGeneration = generation;
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(`${api}/frame/${slot.index}`, { cache: 'no-store', signal: controller.signal });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === 'string' ? body.detail : 'Image unavailable');
      }
      const blob = await response.blob();
      const bitmap = await createImageBitmap(blob);
      if (requestedGeneration !== generation) { bitmap.close(); return; }
      if (controller.signal.aborted) { bitmap.close(); throw new Error('Image request timed out. Retrying…'); }
      slot.bitmap?.close();
      slot.bitmap = bitmap;
      if (slot.url) URL.revokeObjectURL(slot.url);
      slot.url = URL.createObjectURL(blob);
      slot.image.src = slot.url;
      slot.image.hidden = false;
      slot.state.hidden = true;
      if (slot.index === selected) { drawFocus(); analyze(bitmap); }
    } catch (error) {
      if (requestedGeneration !== generation) return;
      slot.state.textContent = controller.signal.aborted ? 'Image request timed out. Retrying…' : error.message || 'Image unavailable';
      slot.state.hidden = false;
      if (slot.index === selected) {
        $('camera-focus-current').textContent = '—';
        $('camera-focus-meter').value = 0;
        $('camera-focus-trend').textContent = 'No fresh image. Focus guidance will resume when this camera responds.';
        lastResult = null;
        mute();
      }
    } finally {
      clearTimeout(timeout);
      if (slot.controller === controller) slot.controller = null;
    }
  }

  function isVisible() {
    if (document.hidden) return false;
    try {
      let current = window;
      while (current !== current.parent) {
        const frame = current.frameElement;
        if (!frame || !frame.getClientRects().length || !frame.getBoundingClientRect().width) return false;
        current = current.parent;
      }
      return !current.document.hidden;
    } catch (_) { return true; }
  }

  function mute() {
    if (audio && gain) gain.gain.setTargetAtTime(0, audio.currentTime, .04);
  }

  function updateAudio() {
    if (!audio || !oscillator || !gain) return;
    if (!$('camera-audio').checked || !running || !visible || paused || drag || !lastResult?.usable || lastResult.samples < 4) { mute(); return; }
    oscillator.frequency.setTargetAtTime(220 + 660 * lastResult.relative, audio.currentTime, .12);
    gain.gain.setTargetAtTime(.035, audio.currentTime, .1);
  }

  function tick() {
    const now = performance.now();
    const nextVisible = isVisible();
    if (visible && !nextVisible) {
      generation++;
      cancelDrag();
      for (const slot of slots) slot.controller?.abort();
      mute();
    }
    if (!visible && nextVisible) { lastDataAttempt = -Infinity; for (const slot of slots) slot.lastAttempt = 0; }
    visible = nextVisible;
    if (!visible) return;
    if (now - lastDataAttempt > 5000) loadData();
    if (paused || saving || !running) return;
    for (const slot of slots) if (now - slot.lastAttempt >= (slot.index === selected ? 200 : 1000)) loadFrame(slot);
  }

  document.querySelectorAll('[data-camera]').forEach(button => button.addEventListener('click', () => {
    cancelDrag();
    selected = Number(button.dataset.camera);
    roi = defaultROI();
    resetFocus();
    document.querySelectorAll('[data-camera]').forEach(item => {
      item.classList.toggle('selected', item === button);
      item.setAttribute('aria-pressed', String(item === button));
    });
    $('camera-focus-title').textContent = `Focus Camera ${selected + 1}`;
    context.clearRect(0, 0, canvas.width, canvas.height);
    zoomContext.clearRect(0, 0, zoom.width, zoom.height);
    $('camera-focus-empty').hidden = Boolean(slots[selected].bitmap);
    drawFocus();
  }));

  function point(event) {
    const bounds = canvas.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)) };
  }

  function moveROI(selection, x, y) {
    return { ...selection, x: Math.max(0, Math.min(1 - selection.width, x)), y: Math.max(0, Math.min(1 - selection.height, y)) };
  }

  function cancelDrag() {
    if (!drag) return;
    const previous = drag;
    roi = previous.previous;
    drag = null;
    if (canvas.hasPointerCapture(previous.pointerId)) canvas.releasePointerCapture(previous.pointerId);
  }

  canvas.addEventListener('pointerdown', event => {
    if (!slots[selected].bitmap || (event.pointerType === 'mouse' && event.button !== 0)) return;
    event.preventDefault();
    canvas.focus({ preventScroll: true });
    canvas.setPointerCapture(event.pointerId);
    drag = { start: point(event), previous: roi, pointerId: event.pointerId };
    mute();
  });
  canvas.addEventListener('pointermove', event => {
    if (!drag) return;
    const end = point(event);
    roi = focus.clampROI({ x: Math.min(drag.start.x, end.x), y: Math.min(drag.start.y, end.y), width: Math.abs(end.x - drag.start.x), height: Math.abs(end.y - drag.start.y) });
    drawFocus();
  });
  canvas.addEventListener('pointerup', event => {
    if (!drag) return;
    const end = point(event);
    if (Math.hypot(end.x - drag.start.x, end.y - drag.start.y) < .025) {
      roi = moveROI(drag.previous, end.x - drag.previous.width / 2, end.y - drag.previous.height / 2);
    }
    drag = null;
    resetFocus();
    drawFocus();
  });
  canvas.addEventListener('pointercancel', () => { cancelDrag(); drawFocus(); });
  canvas.addEventListener('lostpointercapture', () => { cancelDrag(); drawFocus(); });
  canvas.addEventListener('keydown', event => {
    const moves = { ArrowLeft: [-.02, 0], ArrowRight: [.02, 0], ArrowUp: [0, -.02], ArrowDown: [0, .02] };
    if (!moves[event.key]) return;
    event.preventDefault();
    const [x, y] = moves[event.key];
    roi = moveROI(roi, roi.x + x, roi.y + y);
    resetFocus();
    drawFocus();
  });
  $('camera-roi-reset').addEventListener('click', () => { cancelDrag(); roi = defaultROI(); resetFocus(); drawFocus(); });
  $('camera-focus-reset').addEventListener('click', resetFocus);
  $('camera-zoom').addEventListener('input', () => { $('camera-zoom-value').textContent = $('camera-zoom').value + '×'; drawFocus(); });
  window.addEventListener('oche:themechange', drawFocus);
  $('camera-preview-toggle').addEventListener('click', () => {
    cancelDrag();
    drawFocus();
    paused = !paused;
    $('camera-preview-toggle').textContent = paused ? 'Resume preview' : 'Pause preview';
    $('camera-preview-toggle').setAttribute('aria-pressed', String(paused));
    generation++;
    for (const slot of slots) { slot.controller?.abort(); slot.lastAttempt = 0; }
    mute();
    if (data && running) $('camera-status').textContent = paused ? 'Preview paused. Autodarts continues running.' : 'Live camera views from Autodarts. Select a camera below to focus it.';
  });
  $('camera-audio').addEventListener('change', async () => {
    if (!$('camera-audio').checked) { mute(); return; }
    try {
      if (!audio) {
        audio = new AudioContext();
        oscillator = audio.createOscillator();
        gain = audio.createGain();
        gain.gain.value = 0;
        oscillator.connect(gain).connect(audio.destination);
        oscillator.start();
      }
      await audio.resume();
      updateAudio();
    } catch (_) {
      $('camera-audio').checked = false;
      $('camera-focus-trend').textContent = 'Audio is unavailable on this device. The visual focus meter still works.';
    }
  });
  for (let index = 0; index < 3; index++) $('camera-device-' + index).addEventListener('change', event => {
    draft.devices[index] = event.target.value;
    $('camera-settings-status').textContent = 'Changes are ready to review. Apply them when you are ready.';
    updateModes();
  });
  $('camera-resolution').addEventListener('change', event => {
    [draft.width, draft.height] = event.target.value.split('x').map(Number);
    updateModes();
  });
  $('camera-fps').addEventListener('change', event => { draft.fps = Number(event.target.value); updateModes(); });
  $('camera-refresh').addEventListener('click', () => loadData());
  $('camera-reset-settings').addEventListener('click', () => {
    $('camera-settings-status').textContent = '';
    if (data) showData(data, true);
    loadData();
  });
  $('camera-settings-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (saving || $('camera-apply').disabled) return;
    saving = true;
    cancelDrag();
    dataGeneration++;
    generation++;
    for (const slot of slots) slot.controller?.abort();
    mute();
    $('camera-refresh').disabled = true;
    $('camera-settings-fields').disabled = true;
    $('camera-settings-status').textContent = 'Applying camera settings…';
    try {
      const next = await request('/settings', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: draftRevision, ...draft }), signal: AbortSignal.timeout(15000) });
      showData(next, true);
      $('camera-settings-status').textContent = 'Camera settings applied.';
    } catch (error) {
      $('camera-settings-status').textContent = error.message || 'Could not apply settings. Refresh and review the values before trying again.';
    } finally {
      saving = false;
      $('camera-refresh').disabled = loadingData;
      $('camera-settings-fields').disabled = !running;
      updateModes();
      lastDataAttempt = -Infinity;
    }
  });
  window.addEventListener('pagehide', () => { generation++; for (const slot of slots) slot.controller?.abort(); mute(); });
  document.addEventListener('visibilitychange', tick);
  setInterval(tick, 200);
  loadData();
})();
