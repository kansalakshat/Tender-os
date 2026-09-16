if(window.gsap){
  gsap.registerPlugin(ScrollTrigger);
  const mm = gsap.matchMedia();

  mm.add('(prefers-reduced-motion: no-preference)', () => {

    // Hero: one timeline, staggered, no scroll involvement. This is the first
    // thing anyone sees, so it runs immediately rather than on a trigger. It
    // only ever touches things inside .hero -- it used to reach out to .panel
    // and .panel li too, which live a screenful below the fold, so those
    // elements had this timeline AND a scroll reveal both driving their
    // opacity and the last one to render won.
    gsap.timeline({defaults:{ease:'power2.out', duration:.7}})
        .from('.hero .eyebrow',{y:14, opacity:0, duration:.5})
        .from('.hero h1',{y:26, opacity:0},'-=.3')
        .from('.hero .lede',{y:20, opacity:0},'-=.5')
        .from('.hero .search',{y:20, opacity:0},'-=.5')
        .from('.hero .hint',{y:14, opacity:0},'-=.55');

    // Everything below the hero reveals on arrival, and it is an
    // IntersectionObserver rather than a ScrollTrigger for one reason: these
    // animations hide their target until they run, so a reveal that never
    // fires is not "no animation", it is a section of the page that stays
    // blank. ScrollTrigger reveals off scroll events against offsets measured
    // once, and any of a font reflow, a fast jump to the bottom, a restored
    // scroll position or a refresh that ran before layout settled can leave a
    // trigger holding an offset that no longer matches its element. The
    // observer is state-based instead: it reports every target once as soon as
    // it is observed and again whenever layout moves under it, so there is no
    // offset to go stale and nothing to miss.
    //
    // No IntersectionObserver at all (nothing current, but be sure) means
    // plan() hides nothing and the page simply arrives un-animated.
    const plans = new Map();
    const io = window.IntersectionObserver && new IntersectionObserver(seen => {
      const groups = new Map();
      seen.forEach(e => {
        const p = e.isIntersecting && plans.get(e.target);
        if(!p) return;
        io.unobserve(e.target);
        plans.delete(e.target);
        groups.has(p) ? groups.get(p).push(e.target) : groups.set(p, [e.target]);
      });
      // Grouped, so a list still staggers as one run instead of each row
      // animating on its own.
      groups.forEach((nodes, p) => gsap.to(nodes, p.to));
    });
    // No rootMargin on purpose. Holding the reveal until an element is a
    // little way up the page looks better in the abstract, but it means
    // anything parked in that band is on screen and blank -- which is the
    // whole complaint. Revealing the moment the first pixel crosses the
    // viewport edge costs nothing: the fade still plays as the element rises.

    const plan = (sel, from, to) => {
      const nodes = io ? [...document.querySelectorAll(sel)] : [];
      if(!nodes.length) return;
      gsap.set(nodes, from);
      const p = {to: Object.assign(
        {opacity:1, x:0, y:0, scale:1, overwrite:'auto'}, to)};
      nodes.forEach(el => { plans.set(el, p); io.observe(el); });
    };

    // Section headings arrive from the left, which reads as the page turning
    // rather than everything fading in the same way.
    plan('.secHead', {opacity:0, x:-34}, {duration:.7, ease:'power3.out'});
    // The cells the figures sit in step in under them.
    plan('.stats > *', {opacity:0, y:24},
         {duration:.55, stagger:.08, ease:'power2.out'});
    // Capability rows come in one after another with their numbers.
    plan('.capList li', {opacity:0, x:-22},
         {duration:.6, stagger:.09, ease:'power3.out'});
    // Rows inside the live panel deal themselves out.
    plan('.panel .rows > *', {opacity:0, y:14},
         {duration:.45, stagger:.05, ease:'power2.out'});
    // Anything else marked up for it.
    plan('.reveal', {opacity:0, y:26, scale:.99},
         {duration:.65, stagger:.07, ease:'power3.out'});

    // The wash drifts a little slower than the page. Transform only, and
    // nothing here can hide content, so ScrollTrigger is the right tool.
    gsap.to('.hero',{backgroundPositionY:'22%', ease:'none',
      scrollTrigger:{trigger:'.hero', start:'top top', end:'bottom top', scrub:.6}});

    // Counters. The figures are the product's whole claim, so they count up
    // once, on arrival, from the value already rendered in the HTML -- which
    // is also why this one may stay on a scroll trigger: if it never fires the
    // number is still sitting there, already correct.
    document.querySelectorAll('.stats dd').forEach(el => {
      const target = parseFloat(el.textContent.replace(/,/g,''));
      if(!isFinite(target)) return;
      const box = {v:0};
      gsap.to(box,{v:target, duration:1.4, ease:'power2.out',
        scrollTrigger:{trigger:el, start:'top 92%', once:true},
        onUpdate(){ el.textContent = Math.round(box.v).toLocaleString(); }});
    });

    return () => {
      io && io.disconnect();
      plans.clear();
      gsap.set('.secHead, .stats > *, .capList li, .panel .rows > *, .reveal',
               {clearProps:'opacity,transform'});
    };
  });

  // Outside matchMedia on purpose: condensing the bar is a state change, and
  // reduced motion only needs to skip the transition, not the behaviour.
  ScrollTrigger.create({
    start: 'top -64', end: 99999,
    toggleClass: { targets: '.top', className: 'scrolled' }
  });

  // Fonts land after first paint and change every element's height. Only the
  // two scroll triggers left above care, and neither of them hides anything.
  document.fonts && document.fonts.ready.then(() => ScrollTrigger.refresh());
}
