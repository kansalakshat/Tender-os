// The tender page (templates/tender.html). Server values arrive as data-*
// attributes, so this file stays static and cacheable.
(function () {
  var back = document.getElementById('backToBrowse');
  if (!back) return;
  try {
    var q = sessionStorage.getItem('browseQuery');
    if (q) back.href = '/browse?' + q;
  } catch (_) {}
})();

// Remember that this tender was opened, so lists can tick it (site.js).
(function () {
  var m = location.pathname.match(/^\/t\/(\d+)/);
  if (m && typeof markSeen === 'function') markSeen(m[1]);
})();

// The match score (up to ~3s when its cache is cold) and the related lists come
// after the notice itself, so the details never wait on them.
fetch(document.getElementById('moreSlot').dataset.src, {credentials: 'same-origin'})
  .then(function (r) { if (!r.ok) throw r; return r.json(); })
  .then(function (d) {
    // Signed out there is no fit: the slot keeps its sign-up prompt.
    if (d.fit) document.getElementById('fitSlot').innerHTML = d.fit;
    document.getElementById('moreSlot').innerHTML = d.more;
    shareLinks(document.getElementById('moreSlot'));
  })
  .catch(function () {
    var note = '<p class=hint role=status>Could not load this part. Reload the page to try again.</p>';
    if (document.querySelector('#fitSlot .sk')) document.getElementById('fitSlot').innerHTML = note;
    document.getElementById('moreSlot').innerHTML = note;
  });

// The CPPP box. Each start runs a cloud browser, so only a click starts one.
(function () {
  var start = document.getElementById('cpppStart');
  if (!start) return;
  var form = document.getElementById('cpppForm');
  var msg = document.getElementById('cpppMsg');
  var state = document.getElementById('cpppState');
  function say(text) { msg.textContent = text; msg.hidden = !text; }
  function load(button, waiting) {
    button.disabled = true;
    say(waiting);
    fetch(start.dataset.captcha, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(state.value ? {state: state.value} : {})
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        button.disabled = false;
        if (!d.image) { say(d.error || d.detail || 'CPPP is not answering. Please use the link below.'); return; }
        state.value = d.state;
        document.getElementById('cpppImg').src = d.image;
        start.hidden = true; form.hidden = false; say('');
        form.captcha.value = ''; form.captcha.focus();
      })
      .catch(function () {
        button.disabled = false;
        say('CPPP is not answering. Please use the link below.');
      });
  }
  start.addEventListener('click', function () {
    load(start, 'Finding this tender on CPPP. This takes about half a minute. Please keep this page open.');
  });
  var again = document.getElementById('cpppNew');
  again.addEventListener('click', function () { load(again, 'Getting a new image...'); });
})();

// ---- the tabs ----------------------------------------------------------------
// Overview, Eligibility, Documents and AI Analysis. Without this script they
// are plain links down one long page; with it, one panel shows at a time, the
// address bar's #part picks the tab, and links into a section open its tab.
(function () {
  var nav = document.querySelector('[data-tabs]');
  if (!nav) return;
  var links = nav.querySelectorAll('[data-tab]');
  var panels = document.querySelectorAll('[data-panel]');
  nav.setAttribute('role', 'tablist');
  for (var i = 0; i < links.length; i++) {
    links[i].setAttribute('role', 'tab');
    links[i].id = 'tab-' + links[i].dataset.tab;
  }
  for (var j = 0; j < panels.length; j++) {
    panels[j].setAttribute('role', 'tabpanel');
    panels[j].setAttribute('aria-labelledby', 'tab-' + panels[j].dataset.panel);
  }
  // The tab a #part belongs to: the panel that holds that element.
  function tabFor(hash) {
    var el = hash && hash.length > 1 && document.getElementById(hash.slice(1));
    var p = el && el.closest('[data-panel]');
    return p ? p.dataset.panel : null;
  }
  // The bar under the chosen tab slides to it, and the new panel slides in
  // from the side the tab is on: from the right when moving right, from the
  // left when moving back. Reduced motion turns both off (site.css).
  var bar = document.createElement('span');
  bar.className = 'txbar';
  bar.setAttribute('aria-hidden', 'true');
  nav.appendChild(bar);
  var order = Array.prototype.map.call(links, function (a) { return a.dataset.tab; });
  var current = null;
  function slideBar(a) {
    bar.style.width = a.offsetWidth + 'px';
    // The bar sits inside the scrolling tab strip, so it scrolls with the
    // tabs: offsetLeft alone keeps it under the chosen one.
    bar.style.transform = 'translateX(' + a.offsetLeft + 'px)';
  }
  function show(name, focus) {
    var from = order.indexOf(current), to = order.indexOf(name);
    for (var i = 0; i < links.length; i++) {
      var on = links[i].dataset.tab === name;
      links[i].setAttribute('aria-selected', String(on));
      links[i].tabIndex = on ? 0 : -1;
      if (on) slideBar(links[i]);
      if (on && focus) links[i].focus();
    }
    for (var j = 0; j < panels.length; j++) {
      var p = panels[j], open = p.dataset.panel === name;
      p.hidden = !open;
      p.classList.remove('slide-l', 'slide-r');
      if (open && current !== null && from !== to) {
        void p.offsetWidth;                       // restart the animation
        p.classList.add(to > from ? 'slide-r' : 'slide-l');
      }
    }
    current = name;
  }
  window.addEventListener('resize', function () {
    var a = nav.querySelector('[aria-selected=true]');
    if (a) slideBar(a);
  });
  nav.addEventListener('click', function (e) {
    var a = e.target.closest('[data-tab]');
    if (!a) return;
    e.preventDefault();
    show(a.dataset.tab);
    history.replaceState(null, '', '#' + a.dataset.tab);
  });
  // Arrow keys move between tabs, as tabs usually do.
  nav.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    var list = Array.prototype.slice.call(links);
    var k = list.indexOf(document.activeElement);
    if (k < 0) return;
    var next = list[(k + (e.key === 'ArrowRight' ? 1 : list.length - 1)) % list.length];
    show(next.dataset.tab, true);
    history.replaceState(null, '', '#' + next.dataset.tab);
  });
  // Links elsewhere on the page that point into another tab open it first.
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a[href^="#"]');
    if (!a || nav.contains(a)) return;
    var name = a.dataset.tabJump || tabFor(a.getAttribute('href'));
    if (!name) return;
    show(name);
    var target = document.getElementById(a.getAttribute('href').slice(1));
    if (target) {
      e.preventDefault();
      // The address keeps the part shown, so a reload or a shared link opens
      // it; a new history entry, so Back returns to the tab it came from.
      history.pushState(null, '', a.getAttribute('href'));
      target.scrollIntoView({block: 'start'});
    }
  });
  // Back and Forward between those entries: show the tab the address names.
  function fromAddress() { show(tabFor(location.hash) || 'overview'); }
  window.addEventListener('hashchange', fromAddress);
  window.addEventListener('popstate', fromAddress);
  show(tabFor(location.hash) || 'overview');
})();

// "Download Report": the browser's own print dialog, which saves a PDF. The
// print stylesheet shows every tab, so the report has all of it.
(function () {
  var b = document.querySelector('[data-action=print]');
  if (b) b.addEventListener('click', function () { window.print(); });
})();
