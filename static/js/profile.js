// Pre-fill, so "change one answer" is never "type all of them again".
fetch('/me').then(r=>r.json()).then(d=>{
  const c = d.company; if(!c) return;
  const f = document.getElementById('f');
  f.name.value=c.name||''; f.contact_email.value=c.contact_email||'';
  f.keywords.value=(c.keywords||[]).join(', ');
  f.exclude_keywords.value=(c.exclude_keywords||[]).join(', ');
  f.min_lead_days.value=c.min_lead_days??7;
  f.max_project_value.value=c.max_project_value??'';
  f.years_in_business.value=c.years_in_business??'';
  f.annual_turnover.value=c.annual_turnover??'';
  f.largest_similar_work.value=c.largest_similar_work??'';
  f.bid_capacity.value=c.bid_capacity??'';
  f.emd_budget.value=c.emd_budget??'';
  [['sectors',c.sectors],['states',c.states],['districts',c.districts],
   ['buyers',c.buyers],['exclude_buyers',c.exclude_buyers],
   ['registrations',c.registrations]].forEach(([n,vals])=>{
    const want = new Set(vals||[]);
    f.querySelectorAll('input[name="'+n+'"]').forEach(b=>{ b.checked = want.has(b.value); });
  });
  syncDistricts();
});

let signedIn = false;
fetch('/me').then(r=>r.json()).then(d=>{ signedIn = !!d.user; });

function renderPreview(matches){
  const box = document.getElementById('preview');
  if(!matches.length){
    box.innerHTML = '<div class=empty><p>Nothing matched these answers. Broaden '
      + 'the sectors, drop a state, or lower the preparation days.</p></div>';
    box.scrollIntoView({behavior:'smooth',block:'nearest'});
    return;
  }
  const top = matches[0].score;
  box.innerHTML = '<h2>' + matches.length + ' matching tenders</h2><ol class=rows>'
    + matches.map(m =>
      '<li class="row '+rankClass(m.score, top)+'">'
      + '<div class=n>' + esc(m.score) + '</div><div>'
      + '<a class=t href="/t/' + esc(m.tender.id) + '">'
      + esc(m.tender.title) + '</a>'
      + '<p class=m>' + esc(m.tender.organization || 'unnamed buyer')
      + ' &middot; closes <time datetime="' + esc(m.tender.deadline) + '">'
      + esc(m.tender.deadline) + '</time></p>' + extra(m.tender)
      + tags(m.reasons) + '</div></li>').join('')
    + '</ol><p class=hint>Nothing here was saved. '
    + '<a href="/signup">Create an account</a> to keep these answers.</p>';
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function go(e){
  e.preventDefault();
  const btn = e.target.querySelector('button');
  const body = collectProfile(e.target);
  btn.disabled = true;
  document.getElementById('preview').innerHTML = '<div class=sk aria-busy=true>'
    + '<p class=hint role=status>' + (signedIn ? 'Saving your answers&hellip;'
      : 'Scoring your answers against every open tender&hellip;') + '</p>'
    + '<ol class=rows><li class=row><div class=n><div class="b g"></div></div>'
    + '<div><div class=b></div><div class="b s"></div></div></li></ol></div>';
  if(signedIn){
    saveProfile(body).then(d=>location.assign('/c/'+d.id))
      .catch(err=>{ document.getElementById('preview').innerHTML='';
                    showErr(err); btn.disabled=false; });
    return;
  }
  // No account: score without storing. POST /match persists nothing -- no row,
  // no company name, no contact address.
  fetch('/match?limit=25',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})
   .then(r=>r.json().then(d=>{if(!r.ok) throw d.detail; renderPreview(d);}))
   .catch(err=>{ document.getElementById('preview').innerHTML=''; showErr(err); })
   .finally(()=>{ btn.disabled=false; });
}
document.getElementById('f').addEventListener('submit', go);
