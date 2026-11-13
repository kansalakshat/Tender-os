// The home page's India map and the list of states beside it. Both are plain
// links to /browse?state=...; this only makes pointing at one light up the
// other, so the reader can find a small state (Delhi, Goa) on the map by name.
(function () {
  const links = document.querySelectorAll('#states [data-state]');
  function mark(name, on) {
    for (const a of links) if (a.dataset.state === name) a.classList.toggle('lit', on);
  }
  for (const a of links) {
    const name = a.dataset.state;
    for (const ev of ['mouseenter', 'focus']) a.addEventListener(ev, () => mark(name, true));
    for (const ev of ['mouseleave', 'blur']) a.addEventListener(ev, () => mark(name, false));
  }
})();
