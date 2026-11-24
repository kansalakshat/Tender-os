// The home page's India map and the "Top States" list beside it. Every state is
// a plain link to /browse?state=...; this only adds the extras: each state is
// shaded by its count, and pointing at one lights up its row (and the row its
// shape), puts a bubble with its count over it and points "View State Details"
// at it. The bubble starts on the busiest state. With no counts on the page a
// state picker takes the list's place: a click on a state fills its card.
(function () {
  const sec = document.getElementById('states');
  if (!sec) return;
  const links = sec.querySelectorAll('[data-state]:not(#sxtip)');
  const tip = document.getElementById('sxtip');
  const go = document.getElementById('sxgo');
  const box = sec.querySelector('.sxmap');
  let counts = {};
  try { counts = JSON.parse(sec.dataset.counts || '{}'); } catch (e) {}
  const most = Math.max(1, ...Object.values(counts));
  for (const a of sec.querySelectorAll('.indiamap [data-state]')) {
    const n = counts[a.dataset.state] || 0;
    if (n) a.classList.add('c' + Math.min(4, Math.ceil(4 * n / most)));
  }
  // Over the middle of the state's shape, measured from the drawn map.
  function place(name) {
    const shape = sec.querySelector('.indiamap [data-state="' + CSS.escape(name) + '"]');
    if (!shape || !tip || !box) return;
    const r = shape.getBoundingClientRect(), b = box.getBoundingClientRect();
    // Kept wholly inside the map box, so a state at the edge (Arunachal,
    // Gujarat) does not push the bubble out and the page sideways.
    const w = tip.offsetWidth, h = tip.offsetHeight;
    const x = Math.min(Math.max(r.left + r.width / 2 - b.left - w * 0.3, 0), Math.max(b.width - w, 0));
    const y = Math.min(Math.max(r.top + r.height / 2 - b.top - h / 2, 0), Math.max(b.height - h, 0));
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  }
  // Where the bubble rests when nothing is pointed at: the busiest state, or
  // nowhere when the page has no counts (then it starts hidden).
  const home = tip ? tip.dataset.state || '' : '';
  function show(name) {
    if (!tip) return;
    if (!name) {
      tip.hidden = true;
      tip.dataset.state = '';
      if (go) go.href = '/browse';
      return;
    }
    const n = counts[name] || 0;
    tip.querySelector('b').textContent = name;
    // No counts on the page (web.py _render_welcome): name the state, no number.
    tip.querySelector('i').textContent = !Object.keys(counts).length ? ''
      : n.toLocaleString('en-IN') + (n === 1 ? ' Tender' : ' Tenders');
    tip.hidden = false;
    tip.dataset.state = name;
    place(name);
    if (go) go.href = '/browse?state=' + encodeURIComponent(name);
  }
  // The pointer and keyboard focus are tracked apart: moving the mouse off a
  // state must not drop a state the keyboard is still on. The bubble shows the
  // pointed-at state, else the focused one, else goes back where it rests --
  // so a state's name does not stay on the map once nothing is on it.
  let hovered = '', focused = '', picked = '';
  function refresh() {
    for (const a of links) a.classList.toggle('lit', a.dataset.state === hovered || a.dataset.state === focused);
    show(hovered || focused || picked || home);
  }
  // The state picker beside the map (no counts on the page): a click on a
  // state fills the card with its links instead of leaving the page.
  const side = document.getElementById('sxside');
  function pick(name) {
    picked = name;
    for (const a of sec.querySelectorAll('.indiamap [data-state]')) a.classList.toggle('sel', a.dataset.state === name);
    const q = 'state=' + encodeURIComponent(name);
    document.getElementById('sxname').textContent = name;
    document.getElementById('sxhint').textContent = 'Open tenders from ' + name + ':';
    document.getElementById('sxall').href = '/browse?' + q;
    document.getElementById('sxweek').href = '/browse?' + q + '&closing=7';
    document.getElementById('sxlinks').hidden = false;
    refresh();
    // On a phone the card sits under the map: bring it into view.
    const card = side.querySelector('.sxcard'), r = card.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) card.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }
  if (side) for (const a of sec.querySelectorAll('.indiamap [data-state]'))
    a.addEventListener('click', (e) => { e.preventDefault(); pick(a.dataset.state); });
  for (const a of links) {
    const name = a.dataset.state;
    a.addEventListener('mouseenter', () => { hovered = name; refresh(); });
    a.addEventListener('mouseleave', () => { if (hovered === name) hovered = ''; refresh(); });
    a.addEventListener('focus', () => { focused = name; refresh(); });
    a.addEventListener('blur', () => { if (focused === name) focused = ''; refresh(); });
  }
  if (tip) {
    if (tip.dataset.state) place(tip.dataset.state);
    window.addEventListener('resize', () => { if (tip.dataset.state && !tip.hidden) place(tip.dataset.state); });
  }
})();
