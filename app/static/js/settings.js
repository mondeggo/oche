(() => {
  const status = document.getElementById('settings-status');
  const group = name => [...document.querySelectorAll(`[data-group="${name}"]`)];
  const masters = [...document.querySelectorAll('[data-master]')];
  const pendingWrites = new Set();
  const prepareAppearance = window.ocheBeforeAppearanceChange;
  window.ocheBeforeAppearanceChange = async () => {
    await prepareAppearance?.();
    await Promise.all(pendingWrites);
  };
  function queueSave(...args) {
    const task = save(...args);
    pendingWrites.add(task);
    task.finally(() => pendingWrites.delete(task));
  }

  // A master switch is on when all its pages are, and half-way when only some are.
  function syncMaster(master) {
    const children = group(master.dataset.master);
    const on = children.filter(input => input.checked).length;
    master.checked = on === children.length;
    master.indeterminate = on > 0 && on < children.length;
  }

  // The sidebar on this page follows the choices without a reload.
  function syncSidebar() {
    const shown = key => document.querySelector(`[data-setting="${key}"]`)?.checked;
    const set = (id, visible) => { const element = document.getElementById(id); if (element) element.hidden = !visible; };
    set('nav-link-play', shown('show_play_in_navbar'));
    set('sidebar-panels', shown('show_panels_in_navbar'));
    set('nav-link-cameras', shown('show_page_cameras'));
    // A section is shown while at least one of its pages is.
    let systemVisible = false;
    for (const page of ['autodarts', 'system', 'updates']) {
      const visible = shown(`show_page_${page}`);
      systemVisible ||= visible;
      set(`nav-link-${page}`, visible);
    }
    set('sidebar-system', systemVisible);
  }

  async function save(payload, inputs, undo) {
    inputs.forEach(input => { input.disabled = true; });
    try {
      const response = await fetch('/config/data', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(tr("Settings could not be saved. Try again."));
      status.textContent = tr("Saved");
    } catch (error) {
      status.textContent = tr(error.message);
      undo();
    } finally {
      inputs.forEach(input => { input.disabled = false; });
      masters.forEach(syncMaster);
      syncSidebar();
    }
  }

  document.querySelectorAll('[data-setting]').forEach(input => {
    input.addEventListener('change', () => {
      queueSave({[input.dataset.setting]: input.checked}, [input], () => { input.checked = !input.checked; });
    });
  });

  masters.forEach(master => {
    syncMaster(master);
    master.addEventListener('change', () => {
      const children = group(master.dataset.master);
      const previous = children.map(input => input.checked);
      children.forEach(input => { input.checked = master.checked; });
      const payload = Object.fromEntries(children.map(input => [input.dataset.setting, master.checked]));
      queueSave(payload, [master, ...children], () => children.forEach((input, index) => { input.checked = previous[index]; }));
    });
  });
})();
