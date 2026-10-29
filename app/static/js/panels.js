(() => {
  const form = document.getElementById('panels-form');
  const list = document.getElementById('panel-list');
  const status = document.getElementById('panels-status');
  const addButton = document.getElementById('add-panel');
  const countLabel = document.getElementById('panels-count');
  const MAX_PANELS = 30;

  let saveTimer = null;
  let statusClearTimer = null;
  let writing = Promise.resolve(true);

  function refresh() {
    const count = list.children.length;
    document.getElementById('panels-empty').hidden = count > 0;
    addButton.disabled = count >= MAX_PANELS;
    countLabel.textContent = count ? `${count} / ${MAX_PANELS}` : '';
  }

  function addPanel(panel = {}) {
    const row = document.getElementById('panel-row-template').content.firstElementChild.cloneNode(true);
    if (panel.id) row.dataset.id = panel.id;
    row.querySelector('.panel-name').value = panel.name || '';
    row.querySelector('.panel-url').value = panel.url || '';
    row.querySelector('.panel-new-tab').checked = panel.open_in_new_tab || false;
    row.querySelector('.panel-pin').checked = panel.pinned || false;
    row.querySelector('.remove-panel').addEventListener('click', () => {
      row.remove();
      refresh();
      scheduleSave(150);
    });
    list.append(row);
    refresh();
    return row;
  }

  function collectPanels() {
    return Array.from(list.children, row => ({
      ...(row.dataset.id ? { id: row.dataset.id } : {}),
      name: row.querySelector('.panel-name').value.trim(),
      url: row.querySelector('.panel-url').value.trim(),
      open_in_new_tab: row.querySelector('.panel-new-tab').checked,
      pinned: row.querySelector('.panel-pin').checked,
    }));
  }

  // The sidebar's panel list lives on this same page, via base.html -
  // update it in place instead of waiting for a reload. Pinned panels come first.
  function renderNav(panels) {
    const list = document.getElementById('sidebar-panel-list');
    if (!list) return;
    const ordered = [...panels.filter(p => p.pinned), ...panels.filter(p => !p.pinned)];
    if (!ordered.length) {
      list.replaceChildren(navLink(tr("Add a panel"), '/config#panels', '+', false, 'nav-empty'));
      return;
    }
    list.replaceChildren(...ordered.map(p => navLink(p.name, p.open_in_new_tab ? p.url : `/panels/${p.id}`,
      p.name.slice(0, 1).toUpperCase(), p.open_in_new_tab)));
  }

  function navLink(name, href, monogram, external, extraClass = '') {
    const a = document.createElement('a');
    a.className = `nav-item ${extraClass}`.trim();
    a.href = href;
    a.dataset.label = name;
    const mark = document.createElement('span');
    mark.className = 'nav-monogram';
    mark.setAttribute('aria-hidden', 'true');
    mark.textContent = monogram;
    const text = document.createElement('span');
    text.className = 'nav-text';
    text.textContent = name;
    a.append(mark, text);
    if (external) {
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      const trailing = document.createElement('span');
      trailing.className = 'nav-trailing';
      trailing.setAttribute('aria-label', tr("Opens in a new tab"));
      trailing.textContent = '↗';
      a.append(trailing);
    }
    return a;
  }

  function save() {
    writing = writing.then(saveNow);
    return writing;
  }

  async function saveNow() {
    status.textContent = tr("Saving…");
    try {
      const response = await fetch('/panels/data', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ panels: collectPanels() }),
      });
      if (!response.ok) {
        throw new Error(response.status === 422
          ? tr("Check each name and URL. URLs must start with http:// or https://.")
          : tr("Could not save panels. Please try again."));
      }
      const data = await response.json();
      // Newly added rows have no id yet - adopt the ones the server assigned,
      // matched by position, without a full page reload.
      Array.from(list.children).forEach((row, i) => {
        if (data.panels[i]) row.dataset.id = data.panels[i].id;
      });
      renderNav(data.panels);
      status.textContent = tr("Saved");
      statusClearTimer = setTimeout(() => { status.textContent = ''; }, 1500);
      return true;
    } catch (error) {
      status.textContent = tr(error.message) || tr("Could not save panels. Please try again.");
      return false;
    }
  }

  function scheduleSave(delay) {
    clearTimeout(saveTimer);
    clearTimeout(statusClearTimer);
    if (!form.checkValidity()) {
      status.textContent = list.children.length ? tr("Fill in each panel’s name and URL to save.") : '';
      return;
    }
    saveTimer = setTimeout(save, delay);
  }

  const prepareAppearance = window.ocheBeforeAppearanceChange;
  window.ocheBeforeAppearanceChange = async () => {
    await prepareAppearance?.();
    await writing;
    if (saveTimer === null) return;
    clearTimeout(saveTimer);
    if (!form.checkValidity()) throw new Error(tr("Fill in each panel’s name and URL to save."));
    if (!await save()) throw new Error(tr("Could not save panels. Please try again."));
  };

  JSON.parse(document.getElementById('initial-panels').textContent).forEach(addPanel);
  refresh();
  addButton.addEventListener('click', () => addPanel().querySelector('input').focus());

  form.addEventListener('input', (event) => {
    const isTextField = event.target.matches('.panel-name, .panel-url');
    scheduleSave(isTextField ? 800 : 150);
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    scheduleSave(0);
  });
})();
