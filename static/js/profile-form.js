function collectProfile(f){
  const el=n=>f.elements[n];
  const v=n=>el(n)?el(n).value.trim():'';
  const csv=n=>{const x=v(n); return x?x.split(',').map(s=>s.trim()).filter(Boolean):[];};
  const picked=n=>[...f.querySelectorAll('input[name="'+n+'"]:checked')]
                    .map(c=>c.value);
  return {
    name:v('name'),
    contact_email:v('contact_email')||null,
    sectors:picked('sectors'),
    keywords:csv('keywords'),
    districts:picked('districts'),
    states:picked('states'),
    buyers:picked('buyers'),
    exclude_keywords:csv('exclude_keywords'),
    exclude_buyers:picked('exclude_buyers'),
    min_lead_days:parseInt(v('min_lead_days')||'7',10),
    max_project_value:v('max_project_value')||null,
    years_in_business:v('years_in_business')===''?null:parseInt(v('years_in_business'),10),
    annual_turnover:v('annual_turnover')||null,
    largest_similar_work:v('largest_similar_work')||null,
    bid_capacity:v('bid_capacity')||null,
    emd_budget:v('emd_budget')||null,
    registrations:picked('registrations')
  };
}
function saveProfile(body){
  return fetch('/companies',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})
   .then(r=>r.json().then(d=>{if(!r.ok) throw d.detail; return d;}));
}
function showErr(e){
  const box = document.getElementById('err');
  box.textContent = typeof e==='string' ? e : JSON.stringify(e,null,1);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
// Same three weights as rank_class() on the server, so a scored preview and a
// saved results page read identically.
function rankClass(score, top){
  if(!top) return 's0';
  const share = score/top;
  return share>=0.75 ? 's3' : share>=0.45 ? 's2' : 's1';
}
function tags(list){
  return (list||[]).length
    ? '<ul class=tags>'+list.map(r=>'<li class=tag>'+esc(r)+'</li>').join('')+'</ul>' : '';
}
