const LIMIT=25; let offset=0;
// esc() comes from the shared chrome in HEAD. Re-declaring it with const here
// threw "Identifier 'esc' has already been declared", which killed this whole
// script block -- the listing never rendered and the page sat on "loading...".

// The gutter carries the number the page is ranked by. Here that is time left,
// which is what the default sort orders on. Display only: whether a tender still
// counts as open is decided by the server against its own clock, not this one.
function left(d){
  if(!d) return ['', 's0'];
  const ms = Date.parse(d+'T00:00:00');
  if(isNaN(ms)) return ['', 's0'];
  // Midnight today, not Date.now(): measured from the current instant, a tender
  // closing tonight came out as -1 day and every row on the page read "closed".
  const today = new Date(); today.setHours(0,0,0,0);
  const n = Math.round((ms - today.getTime())/864e5);
  if(n < 0) return ['closed', 's0'];
  if(n === 0) return ['today', 's3'];
  return [n+'d', n<=3 ? 's3' : n<=10 ? 's2' : 's1'];
}

function go(e,off){
  if(e) e.preventDefault();
  offset = off ?? 0;
  const f=document.getElementById('f'), p=new URLSearchParams();
  if(f.q.value.trim()) p.set('q', f.q.value.trim());
  if(f.organization.value.trim()) p.set('organization', f.organization.value.trim());
  if(f.source_id.value) p.set('source_id', f.source_id.value);
  p.set('sort', f.sort.value);
  // GET /tenders hides past-deadline rows by default, against the server's
  // date. Reading the visitor's clock instead meant a wrong or simply
  // differently-zoned device decided what counted as still open.
  if(!f.open_only.checked) p.set('include_closed', 'true');
  p.set('limit', LIMIT); p.set('offset', offset);
  // The search lives in the address bar, so a reload, a bookmark or a pasted
  // link all come back to the same page of the same results.
  history.replaceState(null, '', p.toString() ? '?'+p : location.pathname);
  // The previous results stay visible (dimmed) until the new ones arrive.
  document.getElementById('count').textContent = 'Loading tenders…';
  document.getElementById('rows').setAttribute('aria-busy', 'true');
  fetch('/tenders?'+p).then(r=>r.json()).then(d=>{
    document.getElementById('count').textContent =
      d.total.toLocaleString() + ' tenders, showing ' +
      (d.total?offset+1:0) + ' to ' + Math.min(offset+LIMIT, d.total);
    const rows = document.getElementById('rows');
    rows.className = 'rows browse';
    rows.innerHTML = d.items.map(t=>{
      const [label, rank] = left(t.deadline);
      return '<li class="row '+rank+'"><div class=n>'+esc(label)+'</div><div>'
        + '<a class=t href="/t/'+esc(t.id)+'">'+esc(t.title)+'</a>'
        + '<p class=m>'+esc(t.organization||'unnamed buyer')+' &middot; closes '
        + '<time datetime="'+esc(t.deadline||'')+'">'
        + esc(t.deadline||'not stated')+'</time>'
        + '</p>'+synopsis(t)+extra(t)+facts(t)+docLinks(t)+VENDOR_DOCS+'</div></li>';
    }).join('')
      || '<li class=empty><p>Nothing matched that search. Try a shorter word, or '
         + 'clear the source filter.</p></li>';
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
  const f = document.getElementById('f'), q = new URLSearchParams(location.search);
  if(q.has('q')) f.q.value = q.get('q');
  if(q.has('organization')) f.organization.value = q.get('organization');
  if(q.has('source_id')) f.source_id.value = q.get('source_id');
  if(q.has('sort')) f.sort.value = q.get('sort');
  if(q.get('include_closed') === 'true') f.open_only.checked = false;
  return Math.max(0, parseInt(q.get('offset') || '0', 10) || 0);
}

document.getElementById('f').addEventListener('submit', e=>go(e,0));
prev.onclick=()=>go(null, Math.max(0, offset-LIMIT));
next.onclick=()=>go(null, offset+LIMIT);
go(null, seed());
