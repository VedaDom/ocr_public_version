const API = `${window.location.origin}/api/v1/ocr`;

const qs = (sel, el = document) => el.querySelector(sel);
const qsa = (sel, el = document) => Array.from(el.querySelectorAll(sel));

const toast = (msg, ms = 2200) => {
  const el = qs('#toast');
  el.textContent = msg;
  el.hidden = false;
  setTimeout(() => (el.hidden = true), ms);
};

const overlay = (show) => {
  qs('#overlay').hidden = !show;
};

async function fetchJSON(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}: ${t}`);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return null;
}

async function loadTemplates() {
  overlay(true);
  try {
    const data = await fetchJSON(`${API}/templates`);
    const list = qs('#templates-list');
    list.innerHTML = '';
    data.forEach((t) => {
      const item = document.createElement('div');
      item.className = 'item';
      item.dataset.id = t.id;
      item.innerHTML = `
        <div>
          <div>${escapeHtml(t.name)}</div>
          <div class="meta">${new Date(t.updated_at).toLocaleString()}</div>
        </div>
        <button class="btn" data-action="open">Open</button>
      `;
      item.addEventListener('click', (e) => {
        if (e.target.closest('button')) {
          selectTemplate(t.id);
        } else {
          selectTemplate(t.id);
        }
      });
      list.appendChild(item);
    });
  } catch (e) {
    console.error(e);
    toast('Failed to load templates');
  } finally {
    overlay(false);
  }
}

function markActiveTemplate(id) {
  qsa('#templates-list .item').forEach((el) => {
    el.classList.toggle('active', el.dataset.id === id);
  });
}

async function selectTemplate(id) {
  markActiveTemplate(id);
  await loadTemplateDetail(id);
}

async function loadTemplateDetail(id) {
  overlay(true);
  try {
    const t = await fetchJSON(`${API}/templates/${id}`);
    qs('#empty-state').hidden = true;
    const panel = qs('#template-detail');
    panel.hidden = false;
    panel.dataset.id = t.id;
    qs('#tpl-name').textContent = t.name;
    qs('#tpl-meta').textContent = `Updated ${new Date(t.updated_at).toLocaleString()} • ${t.fields.length} field(s)`;

    // Bind add field form
    const addForm = qs('#add-field-form');
    addForm.onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(addForm);
      const payload = {
        name: fd.get('name').trim(),
        label: fd.get('label').trim(),
        field_type: fd.get('field_type'),
        required: fd.get('required') === 'true',
        description: (fd.get('description') || '').toString(),
        order_index: Number(fd.get('order_index') || 0) || 0,
      };
      try {
        overlay(true);
        await fetchJSON(`${API}/templates/${t.id}/fields`, {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        toast('Field added');
        addForm.reset();
        await loadTemplateDetail(t.id);
      } catch (err) {
        console.error(err);
        toast('Failed to add field');
      } finally {
        overlay(false);
      }
    };

    // Render fields
    const list = qs('#fields-list');
    list.innerHTML = '';
    t.fields.forEach((f) => list.appendChild(renderFieldRow(t.id, f)));
  } catch (e) {
    console.error(e);
    toast('Failed to load template');
  } finally {
    overlay(false);
  }
}

function renderFieldRow(tplId, f) {
  const row = document.createElement('div');
  row.className = 'field-row';
  row.dataset.id = f.id;
  row.innerHTML = `
    <input class="inp name" value="${escapeAttr(f.name)}" placeholder="name" />
    <input class="inp label" value="${escapeAttr(f.label)}" placeholder="label" />
    <select class="sel type">
      ${['string','number','boolean','date'].map(v => `<option value="${v}" ${v===f.field_type?'selected':''}>${v}</option>`).join('')}
    </select>
    <label style="display:flex;align-items:center;gap:6px;">
      <input type="checkbox" class="chk required" ${f.required?'checked':''} /> req
    </label>
    <input class="inp desc" value="${escapeAttr(f.description)}" placeholder="description" />
    <input type="number" class="inp order" value="${Number(f.order_index)||0}" min="0" step="1" />
    <div class="actions">
      <button class="btn" data-action="save">Save</button>
      <button class="btn danger" data-action="delete">Delete</button>
    </div>
  `;

  row.addEventListener('click', async (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === 'save') {
      const payload = buildUpdatePayload(row);
      if (!Object.keys(payload).length) { toast('No changes'); return; }
      try {
        overlay(true);
        await fetchJSON(`${API}/templates/${tplId}/fields/${f.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
        toast('Field updated');
      } catch (err) {
        console.error(err);
        const msg = String(err.message || '').includes('409') ? 'Field name already exists' : 'Failed to update';
        toast(msg);
      } finally {
        overlay(false);
      }
    } else if (action === 'delete') {
      if (!confirm('Delete this field?')) return;
      try {
        overlay(true);
        await fetchJSON(`${API}/templates/${tplId}/fields/${f.id}`, { method: 'DELETE' });
        toast('Field deleted');
        row.remove();
      } catch (err) {
        console.error(err);
        toast('Failed to delete');
      } finally {
        overlay(false);
      }
    }
  });

  return row;
}

function buildUpdatePayload(row) {
  const name = qs('.name', row).value.trim();
  const label = qs('.label', row).value.trim();
  const field_type = qs('.type', row).value;
  const required = qs('.required', row).checked;
  const description = qs('.desc', row).value;
  const order_index = Number(qs('.order', row).value || 0);
  const p = {};
  if (name) p.name = name;
  if (label) p.label = label;
  if (field_type) p.field_type = field_type;
  p.required = required;
  p.description = description;
  p.order_index = order_index;
  return p;
}

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeAttr(s) { return escapeHtml(s).replace(/`/g, '\\`'); }

// Create template form
qs('#create-template-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.currentTarget);
  const payload = {
    name: fd.get('name').toString().trim(),
    description: (fd.get('description') || '').toString(),
    callback_url: (fd.get('callback_url') || '').toString() || null,
  };
  try {
    overlay(true);
    await fetchJSON(`${API}/templates`, { method: 'POST', body: JSON.stringify(payload) });
    e.currentTarget.reset();
    toast('Template created');
    await loadTemplates();
  } catch (err) {
    console.error(err);
    const msg = String(err.message || '').includes('409') ? 'Template name exists' : 'Failed to create';
    toast(msg);
  } finally {
    overlay(false);
  }
});

// Initial load
loadTemplates();
