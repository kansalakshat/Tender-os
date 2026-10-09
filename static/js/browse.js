const LIMIT=25; let offset=0;
// esc() comes from the shared chrome in HEAD. Re-declaring it with const here
// threw "Identifier 'esc' has already been declared", which killed this whole
// script block -- the listing never rendered and the page sat on "loading...".

const form = document.getElementById('f');
// The form's fields that become GET /tenders parameters, by name.
const FIELDS = ['q', 'organization', 'state', 'city', 'sector', 'source_id'];
const DISTRICTS = JSON.parse(form.dataset.districts);

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
  return p;
}

function go(e,off){
  if(e) e.preventDefault();
  offset = off ?? 0;
  const p = params();
  p.set('limit', LIMIT); p.set('offset', offset);
  // The search lives in the address bar, so a reload, a bookmark or a pasted
  // link all come back to the same page of the same results.
  history.replaceState(null, '', '?'+p);
  // The previous results stay visible (dimmed) until the new ones arrive.
  document.getElementById('count').textContent = 'Loading tenders…';
  document.getElementById('rows').setAttribute('aria-busy', 'true');
  fetch('/tenders?'+p).then(r=>{ if(!r.ok) throw r; return r.json(); }).then(d=>{
    document.getElementById('count').textContent =
      d.total.toLocaleString() + ' tenders, showing ' +
      (d.total?offset+1:0) + ' to ' + Math.min(offset+LIMIT, d.total);
    const rows = document.getElementById('rows');
    rows.className = 'rows browse';
    rows.innerHTML = d.items.map(t=>{
      const [label, rank] = left(t.deadline);
      return card(t, label, rank);
    }).join('')
      || '<li class=empty><p>Nothing matched those filters. Try a shorter word, or '
         + 'clear a filter.</p></li>';
    document.getElementById('prev').disabled = offset===0;
    document.getElementById('next').disabled = offset+LIMIT >= d.total;
    document.getElementById('err').textContent = '';
    rows.removeAttribute('aria-busy');
  }).catch(()=>{
    document.getElementById('rows').removeAttribute('aria-busy');
    document.getElementById('count').textContent = '';
    document.getElementById('err').textContent =
      'Could not load tenders. The server did not answer, please try again.';
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
form.addEventListener('change', e => {
  if(e.target.tagName === 'SELECT' || e.target.type === 'checkbox') go(null, 0);
});
prev.onclick=()=>go(null, Math.max(0, offset-LIMIT));
next.onclick=()=>go(null, offset+LIMIT);
go(null, seed());
