// Shared chrome for every page (templates/base.html), loaded before the content.

// Everything below writes into innerHTML, so anything originating from the
// database goes through this first. Input validation rejects markup in an email
// address; this is the second line, for whatever validation ever misses.
function esc(s){return String(s??'').replace(/[<>&"']/g,
  c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));}

// Second meta line on a tender row. Mirrors extra_line() in web.py, for the rows
// that arrive as JSON instead of server-rendered HTML.
function extra(t){
  const b=[];
  if(t.external_ref) b.push('ID '+esc(t.external_ref));
  if(t.published_date) b.push('published <time datetime="'+esc(t.published_date)+'">'
    +esc(t.published_date)+'</time>');
  if(t.department && t.department!==t.organization) b.push(esc(t.department));
  return b.length ? '<p class="m x">'+b.join(' &middot; ')+'</p>' : '';
}

// Value, EMD, quantity, closing time... Mirrors fact_strip() in _macros.html;
// the pairs arrive preformatted from app/facts.py.
function facts(t){
  if(!t.facts || !t.facts.length) return '';
  return '<dl class=pf>'+t.facts.map(f=>'<div><dt>'+esc(f[0])+'</dt><dd>'
    +esc(f[1])+'</dd></div>').join('')+'</dl>';
}

// The one-line synopsis. Mirrors the rowsum paragraph in _macros.html; the
// sentence is built server-side (summary_for in web.py) so both say the same.
function synopsis(t){
  return t.summary ? '<p class=rowsum>'+esc(t.summary)+'</p>' : '';
}

// Documents the bid points at. Mirrors doc_links() in _macros.html.
// rel=noopener because these open a third-party host in a new tab, and the
// scheme is re-checked here: esc() makes an href safe to print, not safe to
// follow, and only app/facts.py filtering stands between a stored value and a
// javascript: URL.
function docLinks(t){
  if(!t.links || !t.links.length) return '';
  const ok = t.links.filter(l=>/^https?:\/\//i.test(l.url||''));
  if(!ok.length) return '';
  return '<ul class=docs>'+ok.map(l=>'<li><a href="'+esc(l.url)+'"'
    +' target=_blank rel="noopener noreferrer">'+esc(l.label)+'</a></li>').join('')+'</ul>';
}

// Mirrors vendor_docs() in _macros.html. Static text, so it is built here
// rather than sent with every row of every page of JSON.
const VENDOR_DOCS =
  '<details class=vendordocs><summary>Documents to submit for vendor code creation</summary>'
  + '<ol><li>Copy of PAN Card.</li><li>Copy of GSTIN.</li>'
  + '<li>Copy of Cancelled Cheque.</li>'
  + '<li>Copy of EFT Mandate duly certified by Bank.</li></ol>'
  + '<p class=hint>A general requirement for vendor registration, not read from '
  + 'this tender. Always check the bid document for what this buyer asks for.</p></details>';

function flash(html, kind){
  document.getElementById('flash').innerHTML =
    '<div class="notice '+(kind||'warn')+'">'+html+'</div>';
}

// Which nav item is the page you are on. Cheaper than threading the route
// through page() on every handler, and it cannot fall out of sync with the URL.
const _path = location.pathname;
const _section =
  _path.startsWith('/c/') ? '/matches' :
  _path.startsWith('/t/') ? '/browse' :
  _path === '/profile'    ? '/matches' : _path;
for(const a of document.querySelectorAll('.top nav a')){
  if(a.getAttribute('href') === _section) a.setAttribute('aria-current','page');
}

function resend(){
  fetch('/auth/resend-verification',{method:'POST'}).then(r=>r.json()).then(d=>{
    flash(d.delivered
      ? 'Sent. Check your inbox.'
      : 'This server has no mail credentials, so the link was written to '
        +'<code>outbox.log</code> in the project folder.', 'good');
  });
}

// Messages carried back on a redirect (verification click, OAuth failure).
const _q = new URLSearchParams(location.search);
if(_q.get('verify')==='ok') flash('Email address confirmed. Thanks.', 'good');
if(_q.get('verify')==='invalid')
  flash('That confirmation link is invalid or has expired. '
       +'<a href="#" data-action="resend">Send a new one</a>.');
if(_q.get('error')==='state')
  flash('That sign-in link expired or did not come from here. Please try again.');
if(_q.get('error')==='google') flash('Google sign-in did not complete. Please try again.');
if(_q.get('error')==='cancelled') flash('Google sign-in was cancelled.');

// Icons for markup built here. The server renders them once into
// <template id=icons> in base.html, so the SVG paths are not copied into JS.
function ico(name){
  const t = document.getElementById('icons');
  const el = t && t.content.querySelector('[data-icon="'+name+'"]');
  return el ? el.innerHTML : '';
}

// Every page shows who you are, so "did my save go to my account?" is never a guess.
fetch('/me').then(r=>r.json()).then(d=>{
  const el=document.getElementById('me');
  if(!d.user){
    el.innerHTML = '<a href="/login">Sign in</a>'
      + '<a class="btn sm" href="/signup">Get ranked matches</a>';
    return;
  }
  // Signed in: a real account menu, not a row of loose links. The label is the
  // company name when there is one, because that is what the person recognises;
  // the email stays in the menu where it is still checkable.
  const co = d.company && d.company.name ? d.company.name : '';
  const label = co || d.user.email.split('@')[0];
  const initials = (co
      ? co.trim().split(' ').filter(Boolean).slice(0,2).map(w=>w[0])
      : [d.user.email[0]]).join('').toUpperCase();
  const warn = d.user.email_verified ? ''
    : '<span class=menuwarn>Email not confirmed</span>';
  el.innerHTML =
    '<div class=usermenu>'
    + '<button class=userbtn type=button data-action=usermenu'
    +   ' aria-haspopup=true aria-expanded=false>'
    +   '<span class=avatar aria-hidden=true>'+esc(initials)+'</span>'
    +   '<span class=uname>'+esc(label)+'</span>'
    +   ico('caret-down')
    + '</button>'
    + '<div class=menu hidden>'
    +   '<p class=menuhead><b>'+esc(label)+'</b>'
    +     '<span class=menumail>'+esc(d.user.email)+'</span>'+warn+'</p>'
    +   '<a href="/matches">'+ico('target')+'My matches</a>'
    +   '<a href="/profile">'+ico('sliders-horizontal')+'Edit answers</a>'
    +   '<button type=button data-action=signout>'+ico('sign-out')+'Sign out</button>'
    + '</div>'
    + '</div>';
  // Nagged, not blocked: an unconfirmed address must not lock anyone out of the
  // matches they already answered for. Flip REQUIRE_EMAIL_VERIFICATION to change.
  if(d.user && !d.user.email_verified && !_q.get('verify')){
    flash('Please confirm <b>'+esc(d.user.email)+'</b>. '
          +'<a href="#" data-action="resend">Resend the link</a>.'
          +(d.smtp_configured ? '' : ' <i>(no mail server configured here, so '
            +'the link goes to <code>outbox.log</code>)</i>'));
  }
});
function signout(){
  fetch('/auth/logout',{method:'POST'}).then(()=>location.assign('/'));}

// clipboard needs a secure context; over plain http on a LAN address it is
// undefined, so fall back to selecting the id for a manual copy.
function copyId(btn){
  const text = btn.dataset.copy || '';
  const done = () => {
    const was = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = was; }, 1400);
  };
  if(navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(text).then(done).catch(select);
  } else { select(); }
  function select(){
    const el = document.getElementById('tid');
    if(!el) return;
    const r = document.createRange();
    r.selectNodeContents(el);
    const sel = getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
  }
}

// Inline onclick="" attributes are blocked by our own Content-Security-Policy:
// a nonce whitelists a <script> block, it does NOT whitelist attribute handlers.
// One delegated listener covers links that are inserted later via innerHTML.
// One question's answer, wiped. Works for a text box, a number, and a whole
// checkbox list, because every control in a group shares the same name.
function clearField(btn){
  const form = btn.closest('form');
  const name = btn.dataset.clear;
  if(!form || !name) return;
  form.querySelectorAll('[name="'+name+'"]').forEach(el=>{
    if(el.type === 'checkbox' || el.type === 'radio') el.checked = false;
    else if(el.tagName === 'SELECT') [...el.options].forEach(o=>{o.selected=false;});
    else el.value = '';
  });
  const group = btn.closest('.f');
  const filter = group && group.querySelector('.multifilter');
  if(filter){ filter.value = ''; applyFilter(filter); }
  const first = group && group.querySelector('input,select,textarea');
  if(first) first.focus();
}

// Type-to-narrow on the long lists. Hiding the label rather than removing it
// keeps anything already ticked selected while it is filtered out of view.
function applyFilter(inp){
  const list = inp.parentNode.querySelector('.multilist');
  if(!list) return;
  const q = inp.value.trim().toLowerCase();
  let shown = 0;
  list.querySelectorAll('label').forEach(l=>{
    const hit = !q || l.textContent.toLowerCase().includes(q);
    l.hidden = !hit;
    // A label inside a hidden state group is not on screen, match or not.
    if(hit && !l.parentNode.hidden) shown++;
  });
  let none = list.querySelector('.nomatch');
  if(!shown && !none){
    none = document.createElement('p');
    none.className = 'nomatch';
    none.textContent = 'Nothing matches that.';
    list.appendChild(none);
  } else if(none){
    none.hidden = shown > 0;
  }
}
document.addEventListener('input', function(e){
  if(e.target.classList && e.target.classList.contains('multifilter'))
    applyFilter(e.target);
});

// Districts are grouped by state. Show the groups for every ticked state, and
// untick anything in a group that goes away, so a hidden choice is never saved.
function syncDistricts(){
  const box = document.getElementById('districtField');
  if(!box) return;
  const on = new Set([...document.querySelectorAll('input[name="states"]:checked')]
    .map(c => c.value));
  box.querySelectorAll('.dgroup').forEach(g => {
    g.hidden = !on.has(g.dataset.state);
    if(g.hidden) g.querySelectorAll('input:checked').forEach(c => { c.checked = false; });
  });
  box.hidden = on.size === 0;
}
document.addEventListener('change', function(e){
  if(e.target && e.target.name === 'states') syncDistricts();
});
document.addEventListener('DOMContentLoaded', syncDistricts);

function closeMenus(except){
  document.querySelectorAll('.usermenu').forEach(m => {
    if(m === except) return;
    const b = m.querySelector('.userbtn'), p = m.querySelector('.menu');
    if(b) b.setAttribute('aria-expanded','false');
    if(p) p.hidden = true;
  });
}

function toggleMenu(btn){
  const wrap = btn.closest('.usermenu');
  const panel = wrap.querySelector('.menu');
  const open = panel.hidden;
  closeMenus(wrap);
  panel.hidden = !open;
  btn.setAttribute('aria-expanded', String(open));
}

// A click anywhere else closes the menu; Escape does too, and returns focus to
// the button so the keyboard does not get stranded.
document.addEventListener('click', function(e){
  if(!e.target.closest('.usermenu')) closeMenus(null);
});
document.addEventListener('keydown', function(e){
  if(e.key !== 'Escape') return;
  const open = document.querySelector('.usermenu .menu:not([hidden])');
  if(!open) return;
  closeMenus(null);
  const b = open.parentNode.querySelector('.userbtn');
  if(b) b.focus();
});

document.addEventListener('click', function(e){
  const el = e.target.closest('[data-action]');
  if(!el) return;
  e.preventDefault();
  if(el.dataset.action === 'usermenu') return toggleMenu(el);
  if(el.dataset.action === 'clear') return clearField(el);
  if(el.dataset.action === 'signout') signout();
  if(el.dataset.action === 'resend') resend();
  if(el.dataset.action === 'copy') copyId(el);
});
// Matches page: picking a new sort key applies it at once, in the direction
// already selected, instead of waiting for Ascending/Descending to be clicked again.
document.addEventListener('change', e => {
  const s = e.target;
  if (!s.matches('.sortbar select')) return;
  s.form.requestSubmit(s.form.querySelector('button[aria-pressed="true"]'));
});
