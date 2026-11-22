// The home page's India map and the "Top States" list beside it. Every state is
// a plain link to /browse?state=...; this only adds the extras: each state is
// shaded by its count, and pointing at one lights up its row (and the row its
// shape), puts a bubble with its count over it and points "View State Details"
// at it. The bubble starts on the busiest state.
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
  function mark(name, on) {
    for (const a of links) if (a.dataset.state === name) a.classList.toggle('lit', on);
    // Pointing away puts the bubble back where it rests, so a state's name
    // does not stay on the map after the pointer has left it.
    show(on ? name : home);
  }
  for (const a of links) {
    const name = a.dataset.state;
    for (const ev of ['mouseenter', 'focus']) a.addEventListener(ev, () => mark(name, true));
    for (const ev of ['mouseleave', 'blur']) a.addEventListener(ev, () => mark(name, false));
  }
  if (tip) {
    if (tip.dataset.state) place(tip.dataset.state);
    window.addEventListener('resize', () => { if (tip.dataset.state && !tip.hidden) place(tip.dataset.state); });
  }
})();
