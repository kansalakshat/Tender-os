// The signed-out home page (templates/home.html). Everything here is an extra:
// without it the state picker has a Go button, "View All Portals" opens
// Browse, and each buyer's "View Profile" still opens its page.
(function () {
  // The state picker opens that state's tenders as soon as one is chosen.
  for (const form of document.querySelectorAll('form[data-autogo]')) {
    const pick = form.querySelector('select');
    pick.addEventListener('change', () => {
      if (pick.value) form.submit();
      else location.href = form.action;
    });
    if (pick.name === 'state') dropdown(pick);
  }

  // A long state list as a panel with a search box instead of the browser's
  // own tall list: type to narrow it, arrows and Enter to choose, Escape to
  // close. Choosing sets the real <select> and fires its change, so the form
  // behaves exactly as before.
  function dropdown(pick) {
    const wrap = pick.closest('form');
    wrap.classList.add('xdrop');
    const icon = wrap.querySelector(':scope > .i');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'xdropbtn';
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');
    const label = document.createElement('span');
    btn.append(icon || '', label);
    btn.insertAdjacentHTML('beforeend', ico('caret-down'));
    const panel = document.createElement('div');
    panel.className = 'xdroppanel';
    panel.hidden = true;
    const id = (pick.id || 'pick') + '-list';
    panel.innerHTML = '<input type=search autocomplete=off spellcheck=false placeholder="Search states..." aria-label="Search states" aria-controls="' + id + '">'
      + '<ul role=listbox id="' + id + '">' + Array.from(pick.options).map((o, i) =>
        '<li role=option data-i="' + i + '" aria-selected="' + (o.selected ? 'true' : 'false') + '">'
        + '<span>' + esc(o.value || o.text) + '</span>' + (o.dataset.n ? '<b>' + esc(o.dataset.n) + '</b>' : '') + '</li>').join('')
      + '</ul><p class=none hidden>No state matches that.</p>';
    wrap.append(btn, panel);
    const box = panel.querySelector('input'), items = Array.from(panel.querySelectorAll('li'));
    const none = panel.querySelector('.none');
    let act = -1;
    const name = () => pick.options[pick.selectedIndex] ? (pick.value || 'All States') : 'All States';
    label.textContent = name();
    function visible() { return items.filter(li => !li.hidden); }
    function mark(k) {
      const vis = visible();
      act = Math.max(0, Math.min(k, vis.length - 1));
      items.forEach(li => li.classList.remove('act'));
      if (vis[act]) { vis[act].classList.add('act'); vis[act].scrollIntoView({block: 'nearest'}); }
    }
    function open(on) {
      panel.hidden = !on;
      btn.setAttribute('aria-expanded', String(on));
      if (on) { box.value = ''; filter(); box.focus(); mark(items.findIndex(li => li.getAttribute('aria-selected') === 'true')); }
    }
    function filter() {
      const q = box.value.trim().toLowerCase();
      items.forEach(li => { li.hidden = !!q && !li.textContent.toLowerCase().includes(q); });
      none.hidden = visible().length > 0;
      mark(0);
    }
    function choose(li) {
      pick.selectedIndex = +li.dataset.i;
      label.textContent = name();
      open(false);
      btn.focus();
      pick.dispatchEvent(new Event('change', {bubbles: true}));
    }
    btn.addEventListener('click', () => open(panel.hidden));
    box.addEventListener('input', filter);
    panel.addEventListener('click', e => { const li = e.target.closest('li'); if (li) choose(li); });
    panel.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); mark(act + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); mark(act - 1); }
      else if (e.key === 'Enter') { e.preventDefault(); const li = visible()[act]; if (li) choose(li); }
      else if (e.key === 'Escape') { open(false); btn.focus(); }
    });
    document.addEventListener('click', e => { if (!wrap.contains(e.target)) open(false); });
  }

  // "View All Portals" shows the rest of the portals in place.
  for (const btn of document.querySelectorAll('[data-showmore]')) {
    btn.addEventListener('click', (e) => {
      const list = document.querySelector('.' + btn.dataset.showmore);
      if (!list) return;
      e.preventDefault();
      const more = list.querySelectorAll('li.more');
      for (const li of more) li.hidden = false;
      // Focus goes to the first card shown, before the button it was on hides.
      const first = more[0] && more[0].querySelector('a');
      if (first) first.focus();
      btn.closest('p').hidden = true;
    });
  }

  // Choosing a buyer in the list shows it in the profile panel beside it.
  const rows = document.querySelectorAll('.bxlist [data-buyer]');
  let data = [];
  try { data = JSON.parse(document.getElementById('buyers').dataset.buyers || '[]'); } catch (e) {}
  const panel = document.querySelector('.bxside');
  function show(i) {
    const b = data[i];
    if (!b || !panel) return;
    for (const r of rows) {
      const on = r.dataset.buyer === String(i);
      r.classList.toggle('on', on);
      r.querySelector('.bxpick').setAttribute('aria-pressed', String(on));
    }
    const logo = rows[i].querySelector('.logo');
    const old = panel.querySelector('.bxwho .logo');
    if (logo && old) old.replaceWith(logo.cloneNode(true));
    document.getElementById('bxname').textContent = b.name;
    document.getElementById('bxopen').textContent = b.open;
    document.getElementById('bxlast').textContent = b.ago;
    const top = Math.max(1, ...b.weeks);
    const bars = document.getElementById('bxbars').children;
    b.weeks.forEach((n, k) => {
      if (!bars[k]) return;
      bars[k].className = 'h' + Math.round(10 * n / top);
      bars[k].title = n + ' tenders';
      bars[k].firstChild.textContent = n;
    });
    document.getElementById('bxgo').href = '/buyer?name=' + encodeURIComponent(b.name);
  }
  rows.forEach((r, i) => {
    r.querySelector('.bxpick').addEventListener('click', () => show(i));
  });
})();
