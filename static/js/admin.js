// Operator dashboard: start a fetch, then follow it.
//
// Polled rather than streamed: the job log lives in this process's memory
// (app/adminjobs.py), a run is minutes long, and a poll every few seconds is
// less machinery than a socket for something one person watches occasionally.

// ---- live ticker ----------------------------------------------------------
// Always polling, not only during a job: rows also arrive from backfills run
// outside this process, and an operator watching wants the corpus, not a
// progress bar for their own click.
const fmt = new Intl.NumberFormat('en-IN');
let firstTotal = null, lastTotal = null, lastAt = null;

function tick(d){
  if(!d) return;
  const el = id => document.getElementById(id);
  el('livetotal').textContent = fmt.format(d.total);
  el('liveopen').textContent  = fmt.format(d.open);

  if(firstTotal === null) firstTotal = d.total;
  const delta = d.total - firstTotal;
  el('livedelta').textContent = (delta >= 0 ? '+' : '') + fmt.format(delta);

  // Rate from the gap between the last two samples, not from page-open: a
  // dashboard left open overnight would otherwise average a burst away to zero.
  const now = Date.parse(d.at);
  if(lastTotal !== null && now > lastAt){
    const perMin = (d.total - lastTotal) / ((now - lastAt) / 60000);
    el('liverate').textContent = perMin >= 1 ? fmt.format(Math.round(perMin)) : perMin > 0 ? '<1' : '0';
  }
  lastTotal = d.total; lastAt = now;

  const ul = el('livesources');
  ul.innerHTML = (d.sources || []).slice(0, 6).map(s =>
    '<li><b>' + esc(s.name) + '</b> ' + fmt.format(s.total) + '</li>').join('');
}

async function poll(){
  try{
    const r = await fetch('/admin/live', {headers:{'Accept':'application/json'}});
    if(!r.ok) return;
    const d = await r.json();
    tick(d);
    if(d.job) show(d.job);
  }catch(e){ /* a blip must not stop the ticker */ }
}
// 10s, matched to the server-side cache: polling faster only re-serves
// the same cached numbers.
setInterval(poll, 10000);
poll();

const form = document.getElementById('fetchform');
const out  = document.getElementById('joblog');

function show(job){
  if(!job){ return; }
  out.textContent = (job.lines || []).join('\n') || 'starting...';
  // Pinned to the bottom: the useful end of a log that is still being written
  // is the newest line, and re-reading from the top every poll is unusable.
  out.scrollTop = out.scrollHeight;
  return job.status;
}

let timer = null;
function follow(){
  clearInterval(timer);
  timer = setInterval(async () => {
    try{
      const r = await fetch('/admin/stats', {headers:{'Accept':'application/json'}});
      if(!r.ok) return;                       // a blip must not stop the watch
      const d = await r.json();
      if(show(d.job) !== 'running'){
        clearInterval(timer);
        // The counters moved while that ran; show the new ones rather than
        // leaving numbers on screen that the run has already made wrong.
        location.reload();
      }
    }catch(e){ /* keep polling */ }
  }, 3000);
}

if(form){
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = form.querySelector('button');
    btn.disabled = true;
    out.textContent = 'starting...';
    try{
      const fd = new FormData(form);
      const body = {connector: fd.get('connector') || 'GeM'};
      // Blank means "the connector's own default", so send nothing rather than
      // a zero, which would read as "fetch no pages".
      if(fd.get('pages')) body.pages = Number(fd.get('pages'));
      if(fd.get('since_hours')) body.since_hours = Number(fd.get('since_hours'));
      const r = await fetch('/admin/fetch', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body),
      });
      // A 500 returns HTML, not JSON, and blindly parsing it hid the real error
      // behind "Unexpected token 'I'".
      const text = await r.text();
      let d;
      try{ d = JSON.parse(text); }
      catch(_){ out.textContent = 'server error ' + r.status + ': ' + text.slice(0, 300); btn.disabled = false; return; }
      if(!d.ok){ out.textContent = d.message || 'could not start'; btn.disabled = false; return; }
      follow();
    }catch(err){
      out.textContent = 'could not reach the server: ' + err;
      btn.disabled = false;
    }
  });
}

// A run started before this page loaded is still worth following.
if(out && out.textContent.indexOf('No run started yet.') === -1){ follow(); }
