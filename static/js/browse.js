const LIMIT=25; let offset=0;
let latest=0;   // only the newest request may paint: two quick clicks can answer out of order
// esc() comes from the shared chrome in HEAD. Re-declaring it with const here
// threw "Identifier 'esc' has already been declared", which killed this whole
// script block -- the listing never rendered and the page sat on "loading...".

const form = document.getElementById('f');
// The form's fields that become GET /tenders parameters, by name. The search
// box and the sort sit outside the form on the page but belong to it (form=f).
const FIELDS = ['q', 'organization', 'state', 'city', 'sector', 'source_id'];
const DISTRICTS = JSON.parse(form.dataset.districts);
// What each filter is called on its chip in the "filters in use" row.
const LABELS = {q: 'Work', organization: 'Company', state: 'State', city: 'City',
                sector: 'Category', source_id: 'Portal'};
const CLOSING = {today: 'Today', '7': 'Next 7 days', '30': 'Next 30 days'};

// The city list follows the state: only that state's districts.
function cities(keep){
  const st = form.state.value, sel = form.city;
  const list = DISTRICTS[st] || [];
  sel.innerHTML = '<option value="">' + (st ? 'All of ' + esc(st) : 'Pick a state first')
    + '</option>' + list.map(d => '<option>' + esc(d) + '</option>').join('');
  sel.disabled = !st;
  if(keep && list.includes(keep)) sel.value = keep;
}
form.state.addEventListener('change', () => cities());

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
  // "Closing within": sent as today / 7 / 30, and GET /tenders turns it into a
  // deadline against India's date on each request, for the same reason as the
  // open-only rule above (and so a page left open past midnight stays right).
  if(form.closing.value) p.set('closing', form.closing.value);
  return p;
}

// ---- the sidebar on a narrow screen ----------------------------------------
// On a phone the sidebar would push every result below a long column of
// filters, so it folds away behind a "Filters" button instead.
const ftoggle = document.getElementById('ftoggle');
ftoggle.addEventListener('click', () => {
  const open = form.classList.toggle('open');
  ftoggle.setAttribute('aria-expanded', String(open));
  if(open) form.scrollIntoView({block: 'start'});
});

// ---- "Closing" quick buttons ----------------------------------------------
const quick = form.querySelectorAll('[data-closing]');
function pressClosing(v){
  form.closing.value = v;
  for(const b of quick) b.setAttribute('aria-pressed', String(b.dataset.closing === v));
}
for(const b of quick) b.addEventListener('click', () => { pressClosing(b.dataset.closing); go(null, 0); });

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
  if(form.closing.value) out.push(['closing', 'Closing', CLOSING[form.closing.value]]);
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
  else if(k === 'closing') pressClosing('');
  else form[k].value = '';
  if(k === 'state') cities();
  go(null, 0);
});

// ---- one result ------------------------------------------------------------
// The site's shared card (card() in site.js), with the searched words marked.

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
  // The previous results stay visible (dimmed) until the new ones arrive.
  document.getElementById('count').textContent = 'Loading tenders…';
  const rows = document.getElementById('rows');
  rows.setAttribute('aria-busy', 'true');
  const mine = ++latest;
  fetch('/tenders?'+p).then(r=>{ if(!r.ok) throw r; return r.json(); }).then(d=>{
    if(mine !== latest) return;
    // A bookmarked later page can fall past the end once fewer tenders match
    // ("Showing 26 to 20" over an empty list): start again from the first page.
    if(!d.items.length && d.total && offset > 0){ go(null, 0); return; }
    // The total sits beside the page title; the line over the list says
    // which part of it is on screen.
    document.getElementById('total').textContent =
      d.total.toLocaleString('en-IN') + (d.total === 1 ? ' result' : ' results');
    document.getElementById('count').textContent = d.total
      ? 'Showing ' + (offset + 1) + ' to ' + Math.min(offset + LIMIT, d.total)
      : 'No tenders found';
    rows.className = 'rows browse';
    rows.innerHTML = d.items.map(t => card(t, '', left(t.deadline)[1], '', form.q.value)).join('')
      || '<li class=empty><p>Nothing matched those filters. Try a shorter word, or '
         + 'remove a filter.</p></li>';
    document.getElementById('prev').disabled = offset===0;
    document.getElementById('next').disabled = offset+LIMIT >= d.total;
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
  const q = new URLSearchParams(location.search);
  for(const k of FIELDS) if(q.has(k) && k !== 'city') form[k].value = q.get(k);
  cities(q.get('city'));
  if(q.has('sort')) form.sort.value = q.get('sort');
  if(q.get('include_closed') === 'true') form.open_only.checked = false;
  if(CLOSING[q.get('closing')]) pressClosing(q.get('closing'));
  return Math.max(0, parseInt(q.get('offset') || '0', 10) || 0);
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
// A changed select applies at once; text boxes wait for Search or Enter.
// The sort sits outside the form on the page, so it is listened to directly.
form.addEventListener('change', e => {
  if(e.target.tagName === 'SELECT' || e.target.type === 'checkbox') go(null, 0);
});
form.sort.addEventListener('change', () => go(null, 0));
prev.onclick=()=>go(null, Math.max(0, offset-LIMIT));
next.onclick=()=>go(null, offset+LIMIT);
go(null, seed());
