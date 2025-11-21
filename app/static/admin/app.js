const API = `${window.location.origin}/api/v1/ocr`;

// --- Utilities ---
const qs = (sel, el = document) => el.querySelector(sel);
const qsa = (sel, el = document) => Array.from(el.querySelectorAll(sel));

const toast = (msg, type = 'info') => {
  const container = qs('#toast-container');
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  if (type === 'error') el.style.background = '#ef4444';
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(20px)';
    setTimeout(() => el.remove(), 300);
  }, 3000);
};

const overlay = (show) => {
  qs('#loading-overlay').hidden = !show;
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

const escapeHtml = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// --- State & Navigation ---
let currentTemplateId = null;
let currentDocumentId = null;

function switchView(viewName) {
  qsa('.view-section').forEach(el => el.hidden = true);
  const target = qs(`#view-${viewName}`);
  if (target) target.hidden = false;
  
  // Update nav
  qsa('.nav-item').forEach(el => el.classList.remove('active'));
  const navItem = qs(`.nav-item[data-view="${viewName}"]`);
  if (navItem) navItem.classList.add('active');
  
  // Special handling
  if (viewName === 'templates') {
    loadTemplates();
    currentTemplateId = null;
  }
  if (viewName === 'documents') {
    loadDocuments();
    currentDocumentId = null;
  }
  if (viewName !== 'documents') {
    closeDocumentDrawer();
  }
}

// --- Templates ---
async function loadTemplates() {
  overlay(true);
  try {
    const data = await fetchJSON(`${API}/templates`);
    const grid = qs('#templates-grid');
    grid.innerHTML = '';
    
    if (data.length === 0) {
      qs('#empty-state-templates').hidden = false;
    } else {
      qs('#empty-state-templates').hidden = true;
      data.forEach(t => {
        const card = document.createElement('div');
        card.className = 'card tpl-card';
        card.onclick = (e) => {
          // Prevent click if selecting text or clicking buttons (if any added later)
          if (window.getSelection().toString().length > 0) return;
          openTemplateDetail(t.id);
        };
        card.innerHTML = `
          <h3>${escapeHtml(t.name)}</h3>
          <p>${escapeHtml(t.description || 'No description')}</p>
          <div class="tpl-meta">
            <span>${typeof t.field_count === 'number' ? t.field_count : (t.fields ? t.fields.length : 0)} fields</span>
            <span>${new Date(t.updated_at).toLocaleDateString()}</span>
          </div>
        `;
        grid.appendChild(card);
      });
    }
  } catch (e) {
    console.error(e);
    toast('Failed to load templates', 'error');
  } finally {
    overlay(false);
  }
}

async function loadDocuments() {
  overlay(true);
  try {
    const data = await fetchJSON(`${API}/documents`);
    const tbody = qs('#documents-tbody');
    if (!tbody) return;

    tbody.innerHTML = '';
    const emptyState = qs('#empty-state-documents');

    if (!data || data.length === 0) {
      if (emptyState) emptyState.hidden = false;
      return;
    }

    if (emptyState) emptyState.hidden = true;

    data.forEach((d, idx) => {
      const tr = document.createElement('tr');
      const tpl = d.template_name || '';
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td>${escapeHtml(tpl || '—')}</td>
        <td>${d.created_at ? new Date(d.created_at).toLocaleString() : ''}</td>
      `;
      tr.onclick = () => {
        openDocumentDrawer(d);
      };
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error(e);
    toast('Failed to load documents', 'error');
  } finally {
    overlay(false);
  }
}

function closeDocumentDrawer() {
  const drawer = qs('#document-drawer');
  if (drawer) {
    drawer.hidden = true;
  }
}

async function openDocumentDrawer(doc) {
  currentDocumentId = doc.id;
  const drawer = qs('#document-drawer');
  if (!drawer) return;

  drawer.hidden = false;

  const titleEl = qs('#document-drawer-title');
  if (titleEl) {
    titleEl.textContent = doc.reference_id || 'Document';
  }

  const metaEl = qs('#document-drawer-meta');
  if (metaEl) {
    const parts = [];
    if (doc.reference_id) {
      parts.push(`Reference: ${doc.reference_id}`);
    }
    parts.push(`ID: ${doc.id}`);
    if (doc.template_name) {
      parts.push(`Template: ${doc.template_name}`);
    }
    if (doc.created_at) {
      parts.push(`Created: ${new Date(doc.created_at).toLocaleString()}`);
    }
    metaEl.innerHTML = parts.map(p => `<span>${escapeHtml(p)}</span>`).join('');
  }

  await loadDocumentFields(doc.id);
}

async function loadDocumentFields(documentId) {
  try {
    const data = await fetchJSON(`${API}/documents/${documentId}/fields`);
    const tbody = qs('#document-fields-tbody');
    if (!tbody) return;

    tbody.innerHTML = '';
    const emptyState = qs('#empty-state-document-fields');
    const tableContainer = qs('#document-drawer .table-container');

    if (!data || data.length === 0) {
      if (emptyState) emptyState.hidden = false;
      if (tableContainer) tableContainer.hidden = true;
      return;
    }

    if (emptyState) emptyState.hidden = true;
    if (tableContainer) tableContainer.hidden = false;

    data.forEach(f => {
      const tr = document.createElement('tr');
      const conf = typeof f.confidence === 'number'
        ? `${(f.confidence * 100).toFixed(0)}%`
        : '—';
      tr.innerHTML = `
        <td>${escapeHtml(f.field_name)}</td>
        <td>${escapeHtml(f.field_label)}</td>
        <td>${escapeHtml(f.value)}</td>
        <td>${conf}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error(e);
    toast('Failed to load document fields', 'error');
  }
}

// --- Template Detail ---
async function openTemplateDetail(id) {
  currentTemplateId = id;
  overlay(true);
  try {
    const t = await fetchJSON(`${API}/templates/${id}`);
    
    qs('#detail-title').textContent = t.name;
    qs('#detail-meta').innerHTML = `
      <span class="badge">Updated ${new Date(t.updated_at).toLocaleString()}</span>
    `;
    
    renderFields(t.fields || []);
    
    switchView('template-detail');
  } catch (e) {
    console.error(e);
    toast('Failed to load template details', 'error');
    switchView('templates');
  } finally {
    overlay(false);
  }
}

function renderFields(fields) {
  const tbody = qs('#fields-tbody');
  tbody.innerHTML = '';
  
  if (fields.length === 0) {
    qs('#empty-state-fields').hidden = false;
    qs('.table-container').hidden = true;
  } else {
    qs('#empty-state-fields').hidden = true;
    qs('.table-container').hidden = false;
    
    // Sort by order_index
    fields.sort((a, b) => (a.order_index || 0) - (b.order_index || 0));
    
    fields.forEach((f, idx) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td style="font-family:monospace; font-weight:600; color:var(--primary)">${escapeHtml(f.name)}</td>
        <td>${escapeHtml(f.label)}</td>
        <td><span class="tag">${f.field_type}</span></td>
        <td>${f.required ? '✓' : ''}</td>
        <td class="text-muted">${escapeHtml(f.description)}</td>
        <td>
          <div class="actions">
            <button class="btn icon-only danger outline btn-delete-field" title="Delete">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            </button>
          </div>
        </td>
      `;
      
      // Delete handler
      tr.querySelector('.btn-delete-field').onclick = (e) => {
        e.stopPropagation();
        deleteField(f.id);
      };
      
      // Edit handler (row click)
      tr.onclick = (e) => {
        if (!e.target.closest('button')) {
          openFieldModal(f);
        }
      };
      
      tbody.appendChild(tr);
    });
  }
}

// --- Actions ---

async function createTemplate(payload) {
  try {
    overlay(true);
    const fd = new FormData();
    if (payload.pdf_url) fd.append('pdf_url', payload.pdf_url);
    if (payload.name) fd.append('name', payload.name);
    if (payload.description) fd.append('description', payload.description);
    if (payload.callback_url) fd.append('callback_url', payload.callback_url);
    if (payload.required_field_names) fd.append('required_field_names', payload.required_field_names);

    const res = await fetch(`${API}/templates/generate`, {
      method: 'POST',
      body: fd,
    });
    if (!res.ok) {
      const t = await res.text().catch(() => '');
      throw new Error(`${res.status} ${res.statusText}: ${t}`);
    }
    toast('Template generation started');
    qs('#modal-create-template').close();
    loadTemplates();
  } catch (e) {
    console.error(e);
    toast(e.message, 'error');
  } finally {
    overlay(false);
  }
}

async function deleteTemplate() {
  if (!confirm('Are you sure you want to delete this template? This cannot be undone.')) return;
  try {
    overlay(true);
    await fetchJSON(`${API}/templates/${currentTemplateId}`, { method: 'DELETE' });
    toast('Template deleted');
    switchView('templates');
  } catch (e) {
    console.error(e);
    toast('Failed to delete template', 'error');
  } finally {
    overlay(false);
  }
}

async function saveField(payload) {
  const isEdit = !!payload.id;
  const url = isEdit 
    ? `${API}/templates/${currentTemplateId}/fields/${payload.id}`
    : `${API}/templates/${currentTemplateId}/fields`;
  const method = isEdit ? 'PATCH' : 'POST';
  
  // Remove ID from payload for API
  const id = payload.id;
  delete payload.id;
  
  try {
    overlay(true);
    await fetchJSON(url, { method, body: JSON.stringify(payload) });
    toast(`Field ${isEdit ? 'updated' : 'added'}`);
    qs('#modal-field').close();
    // Reload details to refresh list
    openTemplateDetail(currentTemplateId);
  } catch (e) {
    console.error(e);
    toast(e.message, 'error');
  } finally {
    overlay(false);
  }
}

async function deleteField(fieldId) {
  if (!confirm('Delete this field?')) return;
  try {
    overlay(true);
    await fetchJSON(`${API}/templates/${currentTemplateId}/fields/${fieldId}`, { method: 'DELETE' });
    toast('Field deleted');
    openTemplateDetail(currentTemplateId);
  } catch (e) {
    console.error(e);
    toast('Failed to delete field', 'error');
  } finally {
    overlay(false);
  }
}

// --- Event Listeners ---

document.addEventListener('DOMContentLoaded', () => {
  // Navigation
  qsa('.nav-item').forEach(el => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      const view = el.dataset.view;
      if (view) switchView(view);
    });
  });
  
  // Create Template
  qs('#btn-create-template').onclick = () => {
    qs('#form-create-template').reset();
    qs('#modal-create-template').showModal();
  };
  
  qs('#form-create-template').onsubmit = (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    createTemplate({
      pdf_url: fd.get('pdf_url').trim(),
      name: fd.get('name').trim(),
      description: fd.get('description').trim(),
      callback_url: fd.get('callback_url').trim() || null,
      required_field_names: (fd.get('required_field_names') || '').toString().trim()
    });
  };
  
  // Template Details
  qs('#btn-back-templates').onclick = () => switchView('templates');
  qs('#btn-delete-template').onclick = deleteTemplate;

  const closeDrawerBtn = qs('#btn-close-document-drawer');
  if (closeDrawerBtn) {
    closeDrawerBtn.onclick = () => {
      closeDocumentDrawer();
    };
  }
  
  // Add/Edit Field
  qs('#btn-add-field').onclick = () => openFieldModal();
  
  qs('#form-field').onsubmit = (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = {
      id: fd.get('id'), // Hidden input
      name: fd.get('name').trim(),
      label: fd.get('label').trim(),
      field_type: fd.get('field_type'),
      required: fd.get('required') === 'on',
      description: fd.get('description').trim(),
      order_index: parseInt(fd.get('order_index') || 0)
    };
    saveField(payload);
  };
  
  // Initial Load
  loadTemplates();
});

function openFieldModal(field = null) {
  const form = qs('#form-field');
  form.reset();
  qs('#modal-field-title').textContent = field ? 'Edit Field' : 'Add Field';
  
  if (field) {
    form.querySelector('[name="id"]').value = field.id;
    form.querySelector('[name="name"]').value = field.name;
    form.querySelector('[name="label"]').value = field.label;
    form.querySelector('[name="field_type"]').value = field.field_type;
    form.querySelector('[name="required"]').checked = field.required;
    form.querySelector('[name="description"]').value = field.description || '';
    form.querySelector('[name="order_index"]').value = field.order_index || 0;
  }
  
  qs('#modal-field').showModal();
}

