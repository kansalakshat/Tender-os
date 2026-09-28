// The buyer strip on the home pages. The server renders the busiest few so the
// strip is never empty; this fetches every buyer with an open notice and runs
// them past, ~50px a second. Only the chips on screen exist: as the first one
// leaves on the left it is dropped and the next buyer joins on the right. The
// whole list as one sliding row -- 2,000-odd buyers, doubled for the loop, a
// million pixels wide -- kept the main thread repainting ~500 ms in every
// second on the live site.
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

  const SPEED = 50;   // px a second

  function run(list){
    // Reduced motion: the strip stays still and scrolls by hand, every buyer.
    if(matchMedia('(prefers-reduced-motion: reduce)').matches){
      if(list.length) track.innerHTML = list.map(chip).join('');
      return;
    }
    if(!list.length) return;   // nothing fetched: keep the server's few, still
    let next = 0, x = 0, last = 0, paused = false, visible = true;
    const add = () => {
      track.insertAdjacentHTML('beforeend', chip(list[next]));
      next = (next + 1) % list.length;
    };
    track.innerHTML = '';
    // Enough chips to cover the strip plus one to slide in; a short list just
    // repeats, the modulo above wraps it.
    for(let i = 0; i < 400 && track.scrollWidth < box.clientWidth + 400; i++) add();

    const step = t => {
      if(!visible) return;
      // capped, so a frame after a background tab does not leap ahead
      if(last && !paused) x -= Math.min(t - last, 100) / 1000 * SPEED;
      last = t;
      const first = track.firstElementChild;
      // left edge of the second chip: the first chip's width plus its margin
      const w = first.nextElementSibling.offsetLeft - first.offsetLeft;
      if(-x >= w){ first.remove(); x += w; add(); }
      track.style.transform = 'translateX(' + x + 'px)';
      requestAnimationFrame(step);
    };
    // Hover or keyboard focus holds it still, so a chip can be read and
    // clicked; a focused chip is never the one dropped.
    box.addEventListener('mouseenter', () => paused = true);
    box.addEventListener('mouseleave', () => paused = false);
    box.addEventListener('focusin', () => paused = true);
    box.addEventListener('focusout', () => paused = false);
    // Off screen it does not run at all.
    new IntersectionObserver(([e]) => {
      const was = visible;
      visible = e.isIntersecting;
      if(visible && !was){ last = 0; requestAnimationFrame(step); }
    }).observe(box);
    requestAnimationFrame(step);
  }

  fetch(box.dataset.src).then(r => r.json()).catch(() => []).then(run);
})();
