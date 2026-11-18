const LIMIT=25; let offset=0;
let latest=0;   // only the newest request may paint: two quick clicks can answer out of order
// esc() comes from the shared chrome in HEAD. Re-declaring it with const here
// threw "Identifier 'esc' has already been declared", which killed this whole
// script block -- the listing never rendered and the page sat on "loading...".

const form = document.getElementById('f');
// The form's fields that become GET /tenders parameters, by name. The big
// search box and the sort sit outside the form on the page but belong to it
// (form=f).
const FIELDS = ['q', 'organization', 'state', 'city', 'sector', 'source_id', 'closing'];
const DISTRICTS = JSON.parse(form.dataset.districts);
// What each filter is called on its chip in the "filters in use" row.
const LABELS = {q: 'Search', organization: 'Company', state: 'State', city: 'District',
                sector: 'Category', source_id: 'Portal', closing: 'Deadline'};

// The district list follows the state: only that state's districts.
function cities(keep){
  const st = form.state.value, sel = form.city;
  const list = DISTRICTS[st] || [];
  sel.innerHTML = '<option value="">' + (st ? 'All of ' + esc(st) : 'All Districts')
    + '</option>' + list.map(d => '<option>' + esc(d) + '</option>').join('');
  sel.disabled = !st;
  if(keep && list.includes(keep)) sel.value = keep;
}
form.state.addEventListener('change', () => cities());

// The sidebar's keyword box and the big search box are one search: typing in
// either keeps the other the same.
const kq = document.getElementById('kq'), q = document.getElementById('q');
kq.addEventListener('input', () => { q.value = kq.value; });
q.addEventListener('input', () => { kq.value = q.value; });

function params(){
  const p = new URLSearchParams();
  for(const k of FIELDS){
    const v = form[k].value.trim();
    if(v) p.set(k, v);
  }
  p.set('sort', form.sort.value);
  // GET /tenders hides past-deadline rows by default, against the server's
  // date. Reading the visitor's clock instead meant a wrong or simply
  // differently-zoned device decided what counted as still open.
  if(!form.open_only.checked) p.set('include_closed', 'true');
  return p;
}

// ---- the sidebar on a narrow screen ----------------------------------------
// On a phone the sidebar would push every result below a long column of
// filters, so it folds away behind a "Filters" button instead. "More" over
// the results opens it too, at the full category list.
const ftoggle = document.getElementById('ftoggle');
function openFilters(focus){
  const open = !form.classList.contains('open') || focus;
  form.classList.toggle('open', !!open);
  ftoggle.setAttribute('aria-expanded', String(!!open));
  if(open) form.scrollIntoView({block: 'start'});
  if(focus) focus.focus();
}
ftoggle.addEventListener('click', () => openFilters());
document.getElementById('b2more').addEventListener('click', () => openFilters(form.sector));

// ---- quick categories ---------------------------------------------------------
const cats = document.querySelectorAll('[data-sector]');
function pressSector(){
  const v = form.sector.value;
  for(const b of cats) b.setAttribute('aria-pressed', String(b.dataset.sector === v));
}
for(const b of cats) b.addEventListener('click', () => {
  form.sector.value = b.dataset.sector;
  go(null, 0);
});

// ---- the filters in use, as chips -----------------------------------------
// Every filter that narrows the list shows as a chip with its own remove
// button, so taking one off never means hunting for it in the sidebar.
function chips(){
  const out = [];
  for(const k of FIELDS){
    const el = form[k], v = el.value.trim();
    if(!v) continue;
    const shown = el.tagName === 'SELECT' ? el.options[el.selectedIndex].text : v;
    out.push([k, LABELS[k], shown]);
  }
  if(!form.open_only.checked) out.push(['open_only', 'Showing', 'closed tenders too']);
  document.getElementById('fchips').innerHTML = out.map(([k, label, shown]) =>
    '<li><span>' + esc(label) + ': <b>' + esc(shown) + '</b></span>'
    + '<button type=button data-k="' + k + '" aria-label="Remove ' + esc(label) + ' filter">'
    + ico('x') + '</button></li>').join('')
    + (out.length ? '<li class=fall><a href="/browse">Clear all</a></li>' : '');
  document.getElementById('factive').hidden = !out.length;
}
document.getElementById('fchips').addEventListener('click', e => {
  const b = e.target.closest('button[data-k]');
  if(!b) return;
  const k = b.dataset.k;
  if(k === 'open_only') form.open_only.checked = true;
  else form[k].value = '';
  if(k === 'q') kq.value = '';
  if(k === 'state') cities();
  go(null, 0);
});

// ---- one result: a table row -------------------------------------------------
// Title (searched words marked) and its ID, the buyer and where, the closing
// date with days left, the value (or the EMD when only that is stated), and
// whether it is still open.
function status(t){
  const [days, rank] = left(t.deadline);
  if(days === 'closed') return '<span class="b2st off">Closed</span>';
  if(days === 'today') return '<span class="b2st hot">Closes today</span>';
  return '<span class="b2st on">Open</span>';
}
function row(t, words){
  const f = Object.fromEntries(t.facts || []);
  const value = f['Estimated value'], emd = f['EMD (bid security)'];
  const where = [t.city, t.state].filter(Boolean).join(', ');
  const days = left(t.deadline)[0];
  const url = '/t/' + esc(t.id);
  return '<tr>'
    + '<td class=b2title><a href="' + url + '">' + marked(t.title, words) + '</a>'
    +   '<small>' + (f.Source ? esc(f.Source) : '') + (t.external_ref ? ' &middot; ' + esc(t.external_ref) : '') + '</small></td>'
    + '<td class=b2buyer><span>' + esc(t.organization || 'Unnamed buyer') + '</span>'
    +   (where ? '<small>' + esc(where) + '</small>' : '') + '</td>'
    + '<td class=b2date><time datetime="' + esc(t.deadline || '') + '">' + esc(shortDate(t.deadline) || 'Not stated') + '</time>'
    +   (days && days !== 'closed' ? '<small>' + esc(days === 'today' ? 'today' : days + ' left') + '</small>' : '') + '</td>'
    + '<td class=b2val>' + (value || emd
          ? esc(value || emd) + (value ? '' : '<small>EMD</small>')
          : '<span class=none>Not stated</span>') + '</td>'
    + '<td>' + status(t) + '</td>'
    + '</tr>';
}

// ---- numbered pages --------------------------------------------------------------
// Up to five page numbers around the current one, with arrows either side.
function pages(total){
  const n = Math.max(1, Math.ceil(total / LIMIT)), cur = Math.floor(offset / LIMIT) + 1;
  const nav = document.getElementById('pages');
  if(n <= 1){ nav.innerHTML = ''; return; }
  let a = Math.max(1, cur - 2), b = Math.min(n, a + 4);
  a = Math.max(1, b - 4);
  const btn = (p, label, aria, on) => '<button type=button data-page="' + p + '"'
    + (on ? ' aria-current=page' : '') + (aria ? ' aria-label="' + aria + '"' : '') + '>' + label + '</button>';
  let h = cur > 1 ? btn(cur - 1, ico('caret-left'), 'Previous page') : '';
  for(let p = a; p <= b; p++) h += btn(p, String(p), 'Page ' + p, p === cur);
  if(cur < n) h += btn(cur + 1, ico('arrow-right'), 'Next page');
  nav.innerHTML = h;
}
document.getElementById('pages').addEventListener('click', e => {
  const b = e.target.closest('button[data-page]');
  if(!b) return;
  go(null, (parseInt(b.dataset.page, 10) - 1) * LIMIT);
  document.querySelector('.b2main').scrollIntoView({block: 'start'});
});

function go(e,off){
  if(e) e.preventDefault();
  offset = off ?? 0;
  const p = params();
  p.set('limit', LIMIT); p.set('offset', offset);
  // The search lives in the address bar, so a reload, a bookmark or a pasted
  // link all come back to the same page of the same results.
  history.replaceState(null, '', '?'+p);
  try { sessionStorage.setItem('browseQuery', p.toString()); } catch (_) {}
  chips();
  pressSector();
  // The previous results stay visible (dimmed) until the new ones arrive.
  document.getElementById('count').textContent = 'Loading tenders…';
  const rows = document.getElementById('rows');
  rows.setAttribute('aria-busy', 'true');
  const mine = ++latest;
  fetch('/tenders?'+p).then(r=>{ if(!r.ok) throw r; return r.json(); }).then(d=>{
    if(mine !== latest) return;
    // A bookmarked later page can fall past the end once fewer tenders match:
    // start again from the first page.
    if(!d.items.length && d.total && offset > 0){ go(null, 0); return; }
    document.getElementById('count').innerHTML = d.total
      ? 'Showing ' + (offset + 1).toLocaleString('en-IN') + '-'
        + Math.min(offset + LIMIT, d.total).toLocaleString('en-IN') + ' of <b>'
        + d.total.toLocaleString('en-IN') + '</b> tender' + (d.total === 1 ? '' : 's')
      : 'No tenders found';
    rows.innerHTML = d.items.map(t => row(t, form.q.value)).join('')
      || '<tr class=empty><td colspan=5>Nothing matched those filters. Try a shorter word, or '
         + 'remove a filter.</td></tr>';
    pages(d.total);
    document.getElementById('err').textContent = '';
    rows.removeAttribute('aria-busy');
  }).catch(e=>{
    if(mine !== latest) return;
    rows.removeAttribute('aria-busy');
    document.getElementById('count').textContent = '';
    document.getElementById('err').textContent = e && e.status === 429
      ? 'Too many searches in a minute. Wait a moment, then try again.'
      : 'Could not load tenders. The server did not answer, please try again.';
  });
}
// Restore whatever the URL asks for before the first fetch, so a shared link
// opens on the same filters and the same page rather than on defaults.
function seed(){
  const u = new URLSearchParams(location.search);
  for(const k of FIELDS) if(u.has(k) && k !== 'city') form[k].value = u.get(k);
  kq.value = q.value;
  cities(u.get('city'));
  if(u.has('sort')) form.sort.value = u.get('sort');
  if(u.get('include_closed') === 'true') form.open_only.checked = false;
  return Math.max(0, parseInt(u.get('offset') || '0', 10) || 0);
}

// Save the filters as they stand, to run again from the dashboard.
document.getElementById('savesearch').addEventListener('click', async () => {
  const msg = document.getElementById('savemsg');
  const r = await fetch('/searches', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: document.getElementById('sname').value, query: params().toString()})})
    .catch(() => null);
  if(r && r.status === 401){
    location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
    return;
  }
  const d = r ? await r.json().catch(() => ({})) : {};
  msg.innerHTML = r && r.ok
    ? 'Saved. Find it on your <a href="/dashboard#searches">dashboard</a>.'
    : esc(d.detail || 'Could not save that just now.');
});

form.addEventListener('submit', e=>go(e,0));
// A changed select applies at once; text boxes wait for Search, Enter or
// Apply Filters. The sort sits outside the form on the page, so it is
// listened to directly.
form.addEventListener('change', e => {
  if(e.target.tagName === 'SELECT' || e.target.type === 'checkbox') go(null, 0);
});
form.sort.addEventListener('change', () => go(null, 0));
go(null, seed());
