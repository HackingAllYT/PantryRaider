// Inventory page (templates/inventory.html).
// Whether label printing is turned on (FoodAssistant-fb8x). When off, no print
// affordance renders at all, so the feature is invisible until enabled.
const INVENTORY_DATA = JSON.parse(document.getElementById('inventory-data').textContent);
const PRINTING_ENABLED = INVENTORY_DATA.printing_enabled;

// Panels (built-in + custom + "other"), injected from the server-side registry.
const PANELS = INVENTORY_DATA.panels;
const PANEL_KEYS = PANELS.map(c => c.key);
const BUCKET_LABELS = Object.fromEntries(PANELS.map(c => [c.key, c.label]));

const URGENCY_BADGE = {
  expired: ['badge-expired', '⚠ Expired'],
  today:   ['badge-today',   'Expires today'],
  '3d':    ['badge-3d',      null],  // filled dynamically
  '7d':    ['badge-7d',      null],
  ok:      ['badge-ok',      null],
  unknown: ['badge-unknown', 'No date'],
};

// The best-by date Grocy gives stock that never expires.
const NEVER_EXPIRES = '2999-12-31';

function expiryBadge(item) {
  // Counted in days, stock that never expires would read several hundred
  // thousand of them.
  if (item.best_before_date === NEVER_EXPIRES) {
    return '<span class="expiry-badge badge-unknown">No expiry</span>';
  }
  const u = item.urgency;
  const cls = (URGENCY_BADGE[u] || URGENCY_BADGE.unknown)[0];
  let label;
  if (u === 'expired') {
    label = `⚠ ${Math.abs(item.days_remaining)}d ago`;
  } else if (u === 'today') {
    label = 'Today';
  } else if (item.days_remaining != null) {
    label = `${item.days_remaining}d`;
  } else {
    label = 'No date';
  }
  return `<span class="expiry-badge ${cls}">${label}</span>`;
}

// A best-by date as "Oct 24", in the browser's own language. The year joins
// it only for a date more than 300 days away, so a freezer date next autumn
// does not read as this autumn's.
function shortDate(iso) {
  const day = new Date(`${iso}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const opts = { month: 'short', day: 'numeric' };
  if (Math.abs(Math.round((day - today) / 86400000)) > 300) opts.year = 'numeric';
  return day.toLocaleDateString(undefined, opts);
}

// How much of a row's stock has one best-by date, the amount written the way
// the row's own is: "2 by Oct 24", "1 with no date", "3 with no expiry".
function bestByPart(entry) {
  const n = entry.amount % 1 === 0 ? entry.amount : entry.amount.toFixed(1);
  if (!entry.best_before_date) return `${n} with no date`;
  if (entry.best_before_date === NEVER_EXPIRES) return `${n} with no expiry`;
  return `${n} by ${shortDate(entry.best_before_date)}`;
}

// The line under the name of a row whose stock has more than one best-by
// date, earliest first. The first three show and the rest are counted; the
// title lists them all. Screen readers hear a comma where the dot is, and a
// line that wraps on a phone breaks between dates, never inside one.
function bestByLine(dates) {
  const parts = dates.map(bestByPart);
  const sep = '<span aria-hidden="true"> · </span><span class="visually-hidden">, </span>';
  let shown = parts.slice(0, 3).map(p => `<span class="text-nowrap">${PR.esc(p)}</span>`).join(sep);
  if (parts.length > 3) shown += ` <span class="text-nowrap">+${parts.length - 3} more</span>`;
  return `<div class="item-dates" title="${PR.esc(parts.join(' · '))}">`
    + '<i class="bi bi-calendar3 me-1" aria-hidden="true"></i>'
    + `<span class="visually-hidden">Best-by dates: </span>${shown}</div>`;
}


function renderPanel(panelId, countId, items, bucket) {
  const el = document.getElementById(panelId);
  const cnt = document.getElementById(countId);
  const showAdded = document.getElementById('sortSelect').value.startsWith('added');
  cnt.textContent = items.length;
  if (items.length === 0) {
    el.innerHTML = '<div class="panel-empty"><i class="bi bi-check-circle text-success me-1"></i>Empty</div>';
    return;
  }
  el.innerHTML = items.map(item => {
    const loc = item.location_id || null;
    // The row's own Grocy location, unless the panel name already says it.
    const showLoc = item.location_name
      && item.location_name.trim().toLowerCase() !== (BUCKET_LABELS[bucket] || '').trim().toLowerCase();
    const where = showLoc ? item.location_name : '';
    return `
    <div class="item-row" id="${rowId(item.product_id, loc)}" draggable="true"
         data-product-id="${item.product_id}" data-location-id="${loc || ''}"
         data-bucket="${bucket}" data-name="${PR.esc(item.name)}"
         data-search="${PR.esc(searchKey(item.name))}" data-group="${PR.esc(groupKey(item))}"
         data-on-dragstart="dragStart(event)" data-on-dragend="dragEnd(event)">
      <div class="item-name">
        <div class="item-name-text" title="${PR.esc(item.name)}${item.added_date ? ', added ' + item.added_date.slice(0,10) : ''}">${PR.esc(item.name)}</div>
        ${showLoc ? `<div class="item-location"><i class="bi bi-geo-alt me-1"></i>${PR.esc(item.location_name)}</div>` : ''}
        ${item.best_by_dates && item.best_by_dates.length > 1 ? bestByLine(item.best_by_dates) : ''}
      </div>
      ${showAdded && item.added_date ? `<span class="item-qty">${item.added_date.slice(0,10)}</span>` : ''}
      <span class="item-qty">${item.amount % 1 === 0 ? item.amount : item.amount.toFixed(1)}${item.unit ? ' ' + PR.esc(item.unit) : ''}</span>
      ${item.amount_opened > 0 ? `<span class="expiry-badge badge-opened" title="${item.amount_opened % 1 === 0 ? item.amount_opened : item.amount_opened.toFixed(1)} of these ${PR.esc(item.name)} already open">Opened</span>` : ''}
      ${expiryBadge(item)}
      <div class="item-actions">
      <button class="btn btn-outline-secondary btn-icon" title="Edit category / expiry"
              aria-label="Edit ${PR.esc(item.name)}"
              data-on-click="openEditModal(${item.product_id}, ${loc})">
        <i class="bi bi-pencil"></i>
      </button>
      <button class="btn btn-outline-secondary btn-icon" title="Move to another location"
              aria-label="Move ${PR.esc(item.name)} to another location"
              data-on-click="openMoveModal(${item.product_id}, ${PR.escAttrJs(item.name)}, ${loc})">
        <i class="bi bi-arrow-left-right"></i>
      </button>
      ${item.amount > 0 ? `
      <button class="btn btn-outline-secondary btn-icon" title="Mark one ${PR.esc(item.name)} opened"
              data-on-click="markOpened(${item.product_id}, ${PR.escAttrJs(item.name)})">
        <i class="bi bi-box2"></i>
      </button>
      <button class="btn btn-outline-secondary btn-icon" title="Use one ${PR.esc(item.name)}"
              aria-label="Use one ${PR.esc(item.name)}"
              data-on-click="useOne(${item.product_id}, ${PR.escAttrJs(item.name)}, ${loc})">
        <i class="bi bi-dash-circle"></i>
      </button>` : ''}
      <button class="btn btn-outline-secondary btn-icon" title="Mark all ${PR.esc(item.name)} consumed"
              aria-label="Mark ${PR.esc(item.name)} consumed"
              data-on-click="consume(${item.product_id}, ${item.amount}, ${PR.escAttrJs(item.name)}, ${loc}, ${PR.escAttrJs(where)})">
        <i class="bi bi-check2"></i>
      </button>
      ${PRINTING_ENABLED ? `
      <button class="btn btn-outline-secondary btn-icon" title="Print label"
              aria-label="Print a label for ${PR.esc(item.name)}"
              data-on-click="printLabel(${item.product_id}, ${PR.escAttrJs(item.name)})">
        <i class="bi bi-printer"></i>
      </button>` : ''}
      </div>
    </div>
  `;
  }).join('');
}

// A product kept in two Grocy locations has one row per location, so a row is
// addressed by product and location together.
function rowId(productId, locationId) {
  return `row-${productId}-${locationId || 'none'}`;
}

// The rows on the page by row id, so the Edit window opens with the values of
// the row that was clicked: a product kept in two places can have a different
// best-by date in each. Rebuilt on every load, like the rows themselves.
const itemsByRow = new Map();

function storeItems(items) {
  itemsByRow.clear();
  items.forEach(i => { itemsByRow.set(rowId(i.product_id, i.location_id), i); });
}

// Edit modal
let editTarget = null;  // { productId, locationId, shownBestBy }

// The template's built-in categories, read once so they stay on offer even
// before a Grocy group of that name exists (saving one creates it).
const BUILTIN_EDIT_CATEGORIES = [...document.getElementById('editCategory').options]
  .map(o => o.value).filter(Boolean);

// Grocy's own product groups (custom ones like Supplements too) join the
// built-ins, so the Edit menu offers every category the filter does.
function fillEditCategories(groupNames) {
  const sel = document.getElementById('editCategory');
  const byKey = new Map();
  [...BUILTIN_EDIT_CATEGORIES, ...groupNames].forEach(name => {
    const key = searchKey(name);
    if (name && !byKey.has(key)) byKey.set(key, name);
  });
  const names = [...byKey.values()].sort((a, b) => a.localeCompare(b));
  sel.innerHTML = '<option value="">(unchanged)</option>'
    + names.map(n => `<option value="${PR.esc(n)}">${PR.esc(n)}</option>`).join('');
}

function openEditModal(productId, locationId) {
  const item = itemsByRow.get(rowId(productId, locationId));
  if (!item) return;
  document.getElementById('editModalName').textContent = item.name;

  const catSel = document.getElementById('editCategory');
  catSel.value = item.category || '';
  if (!catSel.value && item.category) {
    // category exists but wasn't in the list, so add a temporary option
    const opt = new Option(item.category, item.category, true, true);
    catSel.add(opt);
  }

  // Remembered as shown, so Save can tell whether the date was changed.
  const dateInput = document.getElementById('editBestBy');
  dateInput.value = item.best_before_date || '';
  editTarget = { productId, locationId, shownBestBy: dateInput.value };
  new bootstrap.Modal(document.getElementById('editModal')).show();
}

async function saveEdit() {
  // The row this Save is for. The window can be opened on another row while
  // the list reloads after this one, so only this row's target is let go.
  const target = editTarget;
  if (target == null) return;
  const btn = document.getElementById('editSaveBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…';

  const category = document.getElementById('editCategory').value || null;
  // A best-by date is written onto every stock entry of the product, so it is
  // sent only when it was changed, and null leaves every date as it is. The
  // pre-filled one, re-sent with a category change, wiped out all the others.
  const picked = document.getElementById('editBestBy').value;
  const bestBy = picked !== target.shownBestBy ? (picked || null) : null;

  try {
    const r = await fetch(`inventory/edit/${target.productId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category, best_before_date: bestBy }),
    });
    if (!r.ok) throw new Error(await prErrText(r));
    bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
    if (editTarget === target) editTarget = null;
    showToast('✓ Item updated');
    await loadDashboard();
  } catch(e) {
    // The window stays open with its row, so Save can simply be tried again.
    showToast(`Save failed: ${e.message}`, true);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-check2 me-1"></i>Save';
  }
}

// Drag & Drop
let dragged = null;  // { productId, locationId, fromBucket, name }

function dragStart(e) {
  const row = e.currentTarget;
  dragged = {
    productId: parseInt(row.dataset.productId),
    locationId: parseInt(row.dataset.locationId) || null,
    fromBucket: row.dataset.bucket,
    name: row.dataset.name,
  };
  row.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', row.dataset.productId);
}

function dragEnd(e) {
  e.currentTarget.classList.remove('dragging');
  document.querySelectorAll('.drop-target').forEach(p => p.classList.remove('drop-target'));
}

function dragOver(e) {
  if (!dragged) return;
  const panel = e.currentTarget;
  if (panel.dataset.bucket !== dragged.fromBucket) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    panel.classList.add('drop-target');
  }
}

function dragLeave(e) {
  e.currentTarget.classList.remove('drop-target');
}

async function dropItem(e) {
  e.preventDefault();
  const panel = e.currentTarget;
  panel.classList.remove('drop-target');
  if (!dragged || panel.dataset.bucket === dragged.fromBucket) return;
  const { productId, locationId, name } = dragged;
  dragged = null;
  await moveItem(productId, panel.dataset.bucket, name, locationId);
}

// Move via modal (touch devices)
let moveTarget = null;  // { productId, name, locationId }

function openMoveModal(productId, name, locationId) {
  moveTarget = { productId, name, locationId };
  document.getElementById('moveModalName').textContent = name;
  new bootstrap.Modal(document.getElementById('moveModal')).show();
}

async function moveFromModal(bucket) {
  bootstrap.Modal.getInstance(document.getElementById('moveModal')).hide();
  if (!moveTarget) return;
  const { productId, name, locationId } = moveTarget;
  moveTarget = null;
  await moveItem(productId, bucket, name, locationId);
}

async function moveItem(productId, bucket, name, locationId) {
  try {
    // Only this row's stock moves; the same product kept elsewhere stays put.
    const body = { bucket };
    if (locationId) body.from_location_id = locationId;
    const r = await fetch(`inventory/move/${productId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await prErrText(r));
    // Crossing temperature natures adjusts the best-by (freezing extends,
    // thawing shortens, FoodAssistant-jty6); say so in the confirmation.
    const d = await r.json().catch(() => ({}));
    let msg = `✓ Moved ${name} to ${BUCKET_LABELS[bucket]}`;
    if (d.new_best_by && d.best_by_change) {
      msg += `, best-by ${d.best_by_change} to ${shortDate(d.new_best_by)}`;
    }
    showToast(msg);
    await loadDashboard();
  } catch(e) {
    showToast(`Move failed: ${e.message}`, true);
  }
}

async function loadDashboard() {
  const sort = document.getElementById('sortSelect').value;
  document.getElementById('loading-spinner').style.display = '';
  const status = document.getElementById('inventory-status');
  try {
    // dates=breakdown adds the list of best-by dates to a row that holds
    // more than one; without it the reply is the plain one Home Assistant reads.
    const r = await fetch(`inventory/dashboard?sort=${sort}&dates=breakdown`);
    if (!r.ok) {
      // Outages answer with a JSON detail written for people ("Your inventory
      // is not reachable right now..."), so surface that, never a raw error
      // page. A Grocy setup problem (a config.php that needs an edit) brings
      // its fix hint and, on a Pi appliance, the one-click repair; the "comes
      // back on its own" line is only for a plain connection failure.
      const body = await r.json().catch(() => ({}));
      const err = new Error(body.detail || 'The inventory list did not load. It usually comes back on its own; Refresh retries now.');
      err.hint = body.hint || '';
      err.repairable = !!body.repairable;
      throw err;
    }
    const data = await r.json();
    status.classList.add('d-none');
    status.classList.remove('d-flex');
    const allItems = PANEL_KEYS.flatMap(k => data[k] || []);
    storeItems(allItems);
    PANEL_KEYS.forEach(k => renderPanel(`panel-${k}`, `count-${k}`, data[k] || [], k));
    // The new rows go through the same filter the user left in place, so a
    // sort change or an edit, move or use never resets it.
    fillCategories(data._product_groups || [], allItems);
    applyFilter();
  } catch(e) {
    document.getElementById('inventory-status-text').textContent = e.message;
    const hint = document.getElementById('inventory-status-hint');
    hint.textContent = e.hint || '';
    hint.classList.toggle('d-none', !e.hint);
    document.getElementById('inventory-repair').classList.toggle('d-none', !e.repairable);
    status.classList.remove('d-none');
    status.classList.add('d-flex');
    PANEL_KEYS.forEach(b => {
      document.getElementById(`panel-${b}`).innerHTML =
        '<div class="panel-empty">Unavailable right now</div>';
      document.getElementById(`count-${b}`).textContent = '–';
    });
    showNoMatch(false);
  } finally {
    document.getElementById('loading-spinner').style.display = 'none';
  }
}

// Mark one unit opened (FoodAssistant-oyef). Grocy applies the product's
// after-opening shelf life, so the best-by date can move; reloading the
// dashboard shows the new date and the Opened badge together.
async function markOpened(productId, name) {
  try {
    const r = await fetch(`inventory/open/${productId}`, { method: 'POST' });
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({}))).detail;
      throw new Error(detail || r.statusText);
    }
    showToast(`✓ Marked one ${name} opened`);
    await loadDashboard();
  } catch(e) {
    showToast(`Could not mark ${name} opened: ${e.message}`, true);
  }
}

// Use one unit, the way a Use stock scan does, for an item without a barcode.
// The check button next to it still consumes the row's whole amount; the
// reload shows the new count, and the row goes once the last one is used.
async function useOne(productId, name, locationId) {
  try {
    let url = `inventory/consume/${productId}?amount=1`;
    if (locationId) url += `&location_id=${encodeURIComponent(locationId)}`;
    const r = await fetch(url, { method: 'POST' });
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({}))).detail;
      throw new Error(detail || r.statusText);
    }
    showToast(`✓ Used one ${name}`);
    await loadDashboard();
  } catch(e) {
    showToast(`Could not use one ${name}: ${e.message}`, true);
  }
}

async function consume(productId, amount, name, locationId, where) {
  // One tap takes the whole amount out of stock and there is no undo, so ask
  // first (the Expiring page asks the same question). A row for one of several
  // places only takes that place's stock, and the question says which.
  const what = where ? `${name} in ${where}` : name;
  const ok = await PR.confirmBox(`Mark all ${what} as consumed? This removes it from your stock.`,
                                 { ok: 'Mark consumed' });
  if (!ok) return;
  try {
    // The JSON endpoint, so a refusal from Grocy reaches the toast instead of
    // hiding behind the page redirect the Expiring form uses.
    let url = `inventory/consume/${productId}?amount=${encodeURIComponent(amount)}`;
    if (locationId) url += `&location_id=${encodeURIComponent(locationId)}`;
    const r = await fetch(url, { method: 'POST' });
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({}))).detail;
      throw new Error(detail || r.statusText);
    }
    const row = document.getElementById(rowId(productId, locationId));
    if (row) row.remove();
    showToast(`✓ Consumed: ${name}`);
    // Update panel counts, through the filter so a filtered list stays filtered
    PANEL_KEYS.forEach(b => {
      const panel = document.getElementById(`panel-${b}`);
      if (!panel.querySelector('.item-row')) renderPanel(`panel-${b}`, `count-${b}`, []);
    });
    applyFilter();
  } catch(e) {
    showToast(`Could not mark ${name} consumed: ${e.message}`, true);
  }
}

// Print a food label for one item (FoodAssistant-fb8x). The server pulls the
// item's name, added date, and best-by date from Grocy, so the button only
// needs the product id.
async function printLabel(productId, name) {
  try {
    const r = await fetch('printing/label', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: productId }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) throw new Error(d.detail || d.error || r.statusText);
    showToast(`✓ Label sent for ${name}`);
  } catch(e) {
    showToast(`Print failed: ${e.message}`, true);
  }
}

function showToast(msg, isError = false) {
  const toast = document.getElementById('consumeToast');
  toast.className = `toast align-items-center border-0 text-bg-${isError ? 'danger' : 'success'}`;
  document.getElementById('toastMsg').textContent = msg;
  new bootstrap.Toast(toast, { delay: 3000 }).show();
}

// Search and category filter (FoodAssistant-7xddm). Every row is already on
// the page, so filtering only hides and shows rows: no request per keystroke,
// and nothing moves between panels or changes in the inventory. The category
// choice is remembered in this browser; the search text is not.
const CATEGORY_STORE_KEY = 'inv_category';
const NO_GROUP = 'none';
const searchInput = document.getElementById('invSearch');
const searchClear = document.getElementById('invSearchClear');
const categorySelect = document.getElementById('invCategory');

// Lower case with accents dropped, so "creme" finds "Crème fraîche".
function searchKey(text) {
  return String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function groupKey(item) {
  return item.product_group_id != null ? String(item.product_group_id) : NO_GROUP;
}

function readSavedCategory() {
  try { return localStorage.getItem(CATEGORY_STORE_KEY) || ''; } catch (e) { return ''; }
}

function saveCategory(value) {
  try {
    if (value) localStorage.setItem(CATEGORY_STORE_KEY, value);
    else localStorage.removeItem(CATEGORY_STORE_KEY);
  } catch (e) { }
}

// Every Grocy product group (custom ones too), plus any group a stocked item
// names that the list missed, then "No category" when an item has none.
// The current choice survives the rebuild; a group that no longer exists falls
// back to All categories.
function fillCategories(groups, items) {
  const names = new Map();
  groups.forEach(g => { if (g && g.id != null && g.name) names.set(String(g.id), g.name); });
  let anyUngrouped = false;
  items.forEach(i => {
    if (i.product_group_id == null) anyUngrouped = true;
    else if (!names.has(String(i.product_group_id))) {
      names.set(String(i.product_group_id), i.product_group_name || i.category || 'Unnamed category');
    }
  });
  const wanted = categorySelect.value || readSavedCategory();
  const sorted = [...names.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  let html = '<option value="">All categories</option>';
  html += sorted.map(([id, name]) => `<option value="${PR.esc(id)}">${PR.esc(name)}</option>`).join('');
  if (anyUngrouped) html += `<option value="${NO_GROUP}">No category</option>`;
  categorySelect.innerHTML = html;
  fillEditCategories([...names.values()]);
  const known = wanted === NO_GROUP ? anyUngrouped : names.has(wanted);
  categorySelect.value = known ? wanted : '';
}

function showNoMatch(on) {
  const box = document.getElementById('invNoMatch');
  box.classList.toggle('d-none', !on);
  box.classList.toggle('d-flex', on);
}

let filterAnnounceTimer = null;

function applyFilter() {
  const q = searchKey(searchInput.value.trim());
  const group = categorySelect.value;
  const active = !!(q || group);
  searchClear.classList.toggle('d-none', !searchInput.value);
  let shownTotal = 0;
  let rowsTotal = 0;
  PANEL_KEYS.forEach(k => {
    const body = document.getElementById(`panel-${k}`);
    const rows = body.querySelectorAll('.item-row');
    let shown = 0;
    rows.forEach(row => {
      const match = (!q || (row.dataset.search || '').includes(q))
        && (!group || row.dataset.group === group);
      row.classList.toggle('d-none', !match);
      if (match) shown += 1;
    });
    shownTotal += shown;
    rowsTotal += rows.length;
    // An empty panel keeps its own "Empty" line and plain count.
    if (!rows.length) return;
    const cnt = document.getElementById(`count-${k}`);
    cnt.textContent = active ? `${shown} of ${rows.length}` : rows.length;
    cnt.title = active ? `${shown} of ${rows.length} items match` : '';
    // A panel with no matches stays where it is, folded to one short line.
    let note = body.querySelector('.panel-nomatch');
    if (active && shown === 0) {
      if (!note) {
        note = document.createElement('div');
        note.className = 'panel-nomatch';
        note.textContent = 'No matches here';
        body.prepend(note);
      }
    } else if (note) {
      note.remove();
    }
  });
  showNoMatch(active && rowsTotal > 0 && shownTotal === 0);
  // One spoken summary once typing pauses, not one per keystroke.
  clearTimeout(filterAnnounceTimer);
  filterAnnounceTimer = setTimeout(() => {
    document.getElementById('invFilterStatus').textContent = !active ? ''
      : shownTotal === 0 ? 'No matching items'
      : `${shownTotal} ${shownTotal === 1 ? 'item matches' : 'items match'}`;
  }, 500);
}

function clearSearch() {
  searchInput.value = '';
  applyFilter();
}

function clearFilters() {
  searchInput.value = '';
  categorySelect.value = '';
  saveCategory('');
  applyFilter();
  searchInput.focus();
}

searchInput.addEventListener('input', applyFilter);
searchInput.addEventListener('keydown', ev => {
  if (ev.key === 'Escape' && searchInput.value) {
    ev.preventDefault();
    clearSearch();
  } else if (ev.key === 'Enter') {
    // The list is already filtered; Enter just puts the keyboard away so the
    // results are not hidden behind it (on a phone or the kitchen screen).
    ev.preventDefault();
    searchInput.blur();
  }
});
searchClear.addEventListener('click', () => { clearSearch(); searchInput.focus(); });
categorySelect.addEventListener('change', () => {
  saveCategory(categorySelect.value);
  applyFilter();
});
document.getElementById('invClearFilters').addEventListener('click', clearFilters);

// Collapsible panels. The open/closed state is remembered in this browser;
// when site data is blocked (or the saved value is damaged) the panels just
// start open each visit.
function readCollapsed() {
  try {
    const v = JSON.parse(localStorage.getItem('inv_collapsed') || '[]');
    return Array.isArray(v) ? v : [];
  } catch (e) { return []; }
}

function togglePanel(bucket) {
  const panel = document.querySelector(`[data-bucket="${bucket}"]`);
  const isCollapsed = panel.classList.toggle('panel-collapsed');
  syncPanelExpanded(panel);
  const collapsed = readCollapsed();
  const updated = isCollapsed
    ? [...new Set([...collapsed, bucket])]
    : collapsed.filter(b => b !== bucket);
  try { localStorage.setItem('inv_collapsed', JSON.stringify(updated)); } catch (e) { }
}

function syncPanelExpanded(panel) {
  const header = panel.querySelector('[data-toggle-panel]');
  if (header) header.setAttribute('aria-expanded', panel.classList.contains('panel-collapsed') ? 'false' : 'true');
}

function restoreCollapsed() {
  readCollapsed().forEach(bucket => {
    const panel = document.querySelector(`[data-bucket="${bucket}"]`);
    if (panel) panel.classList.add('panel-collapsed');
  });
  document.querySelectorAll('#panels [data-bucket]').forEach(syncPanelExpanded);
}

// Panel headers and move buttons carry their bucket id as data, never as code
// spliced into an inline handler.
// The headers are buttons for the keyboard too: Enter or Space toggles one.
document.addEventListener('keydown', function (ev) {
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const header = ev.target.closest('[data-toggle-panel]');
  if (!header || ev.target !== header) return;
  ev.preventDefault();
  togglePanel(header.dataset.togglePanel);
});
document.addEventListener('click', function (ev) {
  const header = ev.target.closest('[data-toggle-panel]');
  if (header) { togglePanel(header.dataset.togglePanel); return; }
  const moveBtn = ev.target.closest('[data-move-to]');
  if (moveBtn) moveFromModal(moveBtn.dataset.moveTo);
});

// Initial load
restoreCollapsed();
loadDashboard();
