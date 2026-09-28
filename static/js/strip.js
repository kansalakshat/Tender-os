// The buyer strip on the home pages. The server renders the busiest few so the
// strip is never empty; this swaps in every buyer with an open notice and sets
// it running. The list is doubled so the loop has no seam: the track slides
// exactly half its width, where the second copy lines up with the first.
(function(){
  const box = document.querySelector('.bstrip');
  if(!box) return;
  const track = box.querySelector('.btrack');

  // Mirror initials() and tile_tone() in app/web.py.
  const SKIP = new Set(['of','the','and','for','in','&','-','ltd','limited','pvt','private']);
  const initials = n => (n||'').split(/[^A-Za-z0-9]+/)
    .filter(w => w && !SKIP.has(w.toLowerCase())).slice(0,2)
    .map(w => w[0]).join('').toUpperCase() || '?';
  const tone = n => [...(n||'')].reduce((a,c) => a + c.charCodeAt(0), 0) % 6;
  const chip = ([name, n]) => '<li><a class=bchip href="/buyer?name='
    + encodeURIComponent(name) + '"><span class="logo t' + tone(name)
    + '" aria-hidden=true>' + esc(initials(name)) + '</span><span class=bname>'
    + esc(name) + '</span><b>' + Number(n).toLocaleString() + '</b></a></li>';

  function run(){
    // Reduced motion: the strip stays still and scrolls by hand, so one copy.
    if(matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    // The copy is decoration: hidden from screen readers and out of the tab
    // order, so the keyboard walks each buyer once.
    const copy = track.cloneNode(true);
    copy.querySelectorAll('a').forEach(a => a.tabIndex = -1);
    track.insertAdjacentHTML('beforeend', copy.innerHTML.replace(/<li>/g, '<li aria-hidden=true>'));
    // A steady ~50px a second whatever the list length.
    track.style.setProperty('--dur', Math.max(30, track.scrollWidth / 2 / 50) + 's');
    box.classList.add('run');
  }

  fetch(box.dataset.src).then(r => r.json()).then(list => {
    if(list.length) track.innerHTML = list.map(chip).join('');
  }).catch(() => {}).finally(run);
})();
