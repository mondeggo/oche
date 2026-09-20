(() => {
  const form = document.getElementById('panels-form');
  const list = document.getElementById('panel-list');
  const status = document.getElementById('panels-status');
  const addButton = document.getElementById('add-panel');
  const countLabel = document.getElementById('panels-count');
  const MAX_PANELS = 30;

  let saveTimer = null;
  let statusClearTimer = null;

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

  // The navbar (pinned panels + the "Panels" dropdown) lives on this same
  // page, via base.html - update it in place instead of waiting for a reload.
  function renderNav(panels) {
    const pinnedContainer = document.getElementById('nav-pinned-panels');
    if (pinnedContainer) {
      pinnedContainer.replaceChildren(...panels.filter(p => p.pinned).map(makePanelLink('pinned-panel')));
    }
    const dropdownList = document.getElementById('panels-menu-list');
    if (dropdownList) {
      if (panels.length) {
        dropdownList.replaceChildren(...panels.map(p => {
          const item = document.createElement('div');
          item.className = 'panels-menu-item';
          item.append(makePanelLink('panel-menu-name')(p));
          return item;
        }));
      } else {
        const empty = document.createElement('p');
        empty.className = 'panels-menu-empty';
        empty.textContent = 'Keep your favorite tools in reach.';
        dropdownList.replaceChildren(empty);
      }
    }
  }

  function makePanelLink(className) {
    return (p) => {
      const a = document.createElement('a');
      a.className = className;
      a.title = p.name;
      a.textContent = p.name;
      if (p.open_in_new_tab) {
        a.href = p.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
      } else {
        a.href = `/panels/${p.id}`;
      }
      return a;
    };
  }

  async function save() {
    status.textContent = 'Saving…';
    try {
      const response = await fetch('/panels/data', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ panels: collectPanels() }),
      });
      if (!response.ok) {
        throw new Error(response.status === 422
          ? 'Check each name and URL. URLs must start with http:// or https://.'
          : 'Could not save panels. Please try again.');
      }
      const data = await response.json();
      // Newly added rows have no id yet - adopt the ones the server assigned,
      // matched by position, without a full page reload.
      Array.from(list.children).forEach((row, i) => {
        if (data.panels[i]) row.dataset.id = data.panels[i].id;
      });
      renderNav(data.panels);
      status.textContent = 'Saved';
      statusClearTimer = setTimeout(() => { status.textContent = ''; }, 1500);
    } catch (error) {
      status.textContent = error.message || 'Could not save panels. Please try again.';
    }
  }

  function scheduleSave(delay) {
    clearTimeout(saveTimer);
    clearTimeout(statusClearTimer);
    if (!form.checkValidity()) {
      status.textContent = list.children.length ? 'Fill in each panel’s name and URL to save.' : '';
      return;
    }
    saveTimer = setTimeout(save, delay);
  }

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
