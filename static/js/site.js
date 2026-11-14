// Shared chrome for every page (templates/base.html), loaded before the content.

// Everything below writes into innerHTML, so anything originating from the
// database goes through this first. Input validation rejects markup in an email
// address; this is the second line, for whatever validation ever misses.
function esc(s){return String(s??'').replace(/[<>&"']/g,
  c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));}

// Time left, as the row labels it. Mirrors days_left() in web.py. Display only:
// whether a tender still counts as open is decided by the server's clock.
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

// A card's dates: "23 Oct 2026".
function shortDate(iso){
  if(!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  return isNaN(d) ? iso : d.toLocaleDateString('en-IN', {day: 'numeric', month: 'short', year: 'numeric'});
}
// The title with each searched word marked. Split the raw text first and
// escape every piece, so a mark can never land inside an HTML entity.
function marked(text, q){
  const words = (q || '').trim().split(/\s+/).filter(w => w.length > 1);
  if(!words.length) return esc(text);
  const re = new RegExp('(' + words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi');
  return text.split(re).map((part, i) => i % 2 ? '<mark>' + esc(part) + '</mark>' : esc(part)).join('');
}
// WhatsApp share link for a tender: its title and our page's full address.
function shareUrl(id, title){
  return 'https://wa.me/?text=' + encodeURIComponent(title + '\n' + location.origin + '/t/' + id);
}

// One tender in a list, as the card the whole site shares. Mirrors row() in
// _macros.html, which draws the same card on the server; the facts arrive
// preformatted from app/facts.py. `gutter` is the list's own number for the
// row (a match score), `tagsHtml` its reasons, `q` words to mark in the title.
function card(t, gutter, rank, tagsHtml, q){
  const f = Object.fromEntries(t.facts || []);
  const days = left(t.deadline)[0];
  const value = f['Estimated value'], emd = f['EMD (bid security)'];
  const where = [t.city, t.state].filter(Boolean).join(', ');
  const url = '/t/' + esc(t.id);
  // The publish date is already on the line above, so the chips skip it.
  const chips = (f.Source ? '<li class=port>' + esc(f.Source) + '</li>' : '')
    + (t.external_ref ? '<li>ID ' + esc(t.external_ref) + '</li>' : '')
    + (t.department && t.department !== t.organization ? '<li>' + esc(t.department) + '</li>' : '')
    + ['Bid type', 'Quantity', 'Contract period'].filter(k => f[k])
        .map(k => '<li>' + k + ': ' + esc(f[k]) + '</li>').join('');
  return '<li class="row bcard ' + rank + '">'
    + '<p class=borg>' + (gutter && gutter !== days ? '<span class=bscore>' + esc(gutter) + '</span>' : '')
    +   esc(t.organization || 'Unnamed buyer') + '</p>'
    + '<p class=bmeta>'
    +   (where ? '<span>' + ico('map-pin') + esc(where) + '</span>' : '')
    +   (t.published_date ? '<span>' + ico('calendar') + 'Published ' + esc(shortDate(t.published_date)) + '</span>' : '')
    + '</p>'
    + '<a class=t href="' + url + '">' + marked(t.title, q) + '</a>'
    + (chips ? '<ul class=chips>' + chips + '</ul>' : '')
    + (tagsHtml || '')
    + '<div class=bfoot>'
    +   '<p class=bmoney>' + (value || emd
          ? '<span>' + (value ? 'Value' : 'EMD') + '</span><b>' + esc(value || emd) + '</b>'
          : '<span>Value</span><b class=none>not stated</b>') + '</p>'
    +   '<p class="bdue' + (rank === 's3' ? ' hot' : '') + '">' + ico('calendar')
    +     '<span>Closes</span><b><time datetime="' + esc(t.deadline || '') + '">'
    +     esc(f['Bids close'] || shortDate(t.deadline) || 'not stated') + '</time></b>'
    +     (days ? '<em>' + esc(days) + '</em>' : '') + '</p>'
    +   '<p class=bact>'
    +     '<a class="btn ghost sm wa" href="' + esc(shareUrl(t.id, t.title)) + '" target=_blank rel="noopener noreferrer">'
    +       ico('share') + 'Share</a>'
    +     '<a class="btn sm" href="' + url + '">View details</a></p>'
    + '</div></li>';
}

// Cards drawn on the server do not know the site's own address, so their
// Share buttons point at the tender page until this gives them the WhatsApp
// link. Also run for lists that arrive later (the tender page's related lists).
function shareLinks(root){
  for(const a of (root || document).querySelectorAll('a[data-share]')){
    a.href = shareUrl(a.dataset.share, a.dataset.title || '');
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.removeAttribute('data-share');
  }
}
shareLinks();

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
  _path === '/profile'    ? '/matches' :
  _path === '/wishlist'   ? '/dashboard' :
  _path.startsWith('/buyer') ? '/' : _path;
for(const a of document.querySelectorAll('.top nav a')){
  if(a.getAttribute('href') === _section) a.setAttribute('aria-current','page');
}

function resend(){
  fetch('/auth/resend-verification',{method:'POST'}).then(r=>r.json().then(d=>{
    if(!r.ok) return flash(esc(d.detail||'Could not send the link. Please try again.'));
    flash(d.delivered
      ? 'Sent. Check your inbox.'
      : d.status==='already verified' ? 'This address is already confirmed.'
      : 'This server has no mail credentials, so the link was written to '
        +'<code>outbox.log</code> in the project folder.', 'good');
  }));
}

// Messages carried back on a redirect (verification click, OAuth failure).
const _q = new URLSearchParams(location.search);
if(_q.get('verify')==='ok') flash('Email address confirmed. Thanks.', 'good');
if(_q.get('verify')==='signin') flash('Sign in to confirm your email address.');
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
    signinPrompt();
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
    +   '<a href="/dashboard">'+ico('stack-simple')+'My dashboard</a>'
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
// Dark (the blended system) or light (the brutalist one). theme.js applied the
// saved choice before first paint; this only flips it and remembers.
function toggleTheme(){
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('tos-theme', next); } catch(e) { /* private mode */ }
}
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
  if(el.dataset.action === 'theme') toggleTheme();
});
// Matches page: picking a new sort key applies it at once, in the direction
// already selected, instead of waiting for Ascending/Descending to be clicked again.
document.addEventListener('change', e => {
  const s = e.target;
  if (!s.matches('.sortbar select')) return;
  s.form.requestSubmit(s.form.querySelector('button[aria-pressed="true"]'));
});


// ---- sign-in prompt (signed-out visitors) ----------------------------------
// After a visitor has read a little (scrolled ~600px, or 20 seconds on the
// page) the dialog in base.html asks them to sign in. At most once per visit,
// and "Not now" quiets it for three days. Never on the pages that are already
// about signing in. Storage can be blocked, so every read and write is guarded:
// without it the prompt simply shows once per page load.
function signinPrompt(){
  const pop = document.getElementById('signpop');
  if(!pop || typeof pop.showModal !== 'function') return;
  if(/^\/(login|signup|admin)/.test(location.pathname)) return;
  const QUIET = 3 * 24 * 3600 * 1000;
  // "Not now" is shared by every tab (localStorage), so it is read again just
  // before opening: one tab's "Not now" must also quiet a tab opened earlier.
  function quiet(){
    try {
      return Date.now() - Number(localStorage.getItem('signpopLater') || 0) < QUIET;
    } catch (_) { return false; }
  }
  try { if(sessionStorage.getItem('signpopShown')) return; } catch (_) {}
  if(quiet()) return;
  // Sign in brings them back to this page.
  document.getElementById('signpopLogin').href =
    '/login?next=' + encodeURIComponent(location.pathname + location.search);

  let done = false;
  function open(){
    if(done || pop.open || document.querySelector('dialog[open]')) return;
    done = true;
    window.removeEventListener('scroll', onScroll);
    clearTimeout(timer);
    if(quiet()) return;
    try { sessionStorage.setItem('signpopShown', '1'); } catch (_) {}
    pop.showModal();
  }
  function onScroll(){ if(window.scrollY > 600) open(); }
  window.addEventListener('scroll', onScroll, {passive: true});
  const timer = setTimeout(open, 20000);

  // A click on the dimmed backdrop (outside the box) closes it too.
  pop.addEventListener('click', e => { if(e.target === pop) pop.close('close'); });
  pop.addEventListener('close', () => {
    if(pop.returnValue === 'later'){
      try { localStorage.setItem('signpopLater', String(Date.now())); } catch (_) {}
    }
  });
}
