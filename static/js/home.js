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
    for (const r of rows) r.classList.toggle('on', r.dataset.buyer === String(i));
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
    r.tabIndex = 0;
    r.addEventListener('click', (e) => { if (!e.target.closest('a')) show(i); });
    r.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target === r) { e.preventDefault(); show(i); }
    });
  });
})();
