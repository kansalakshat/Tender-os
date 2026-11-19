// The profile page's steps (templates/profile.html): one step's questions at a
// time, Back and Next, a progress bar for how much of the profile is answered,
// and a review of every answer before saving. Without this script every
// question shows on one page and the save button sits at the end.
(function () {
  const form = document.getElementById('f');
  const nav = document.querySelector('[data-steps]');
  if (!form || !nav) return;
  const ORDER = ['basic', 'business', 'experience', 'documents', 'review'];
  const sets = form.querySelectorAll('fieldset[data-step]');
  const links = nav.querySelectorAll('[data-step-link]');
  const back = document.getElementById('pfback'), next = document.getElementById('pfnext');
  const save = document.getElementById('pfsave');
  const done = new Set();
  let current = 'basic';
  document.documentElement.classList.add('pfjs');

  // ---- how much of the profile is answered ---------------------------------
  // The same eleven answers, each counting the same, as profile_complete() on
  // the server, so this bar and the ring on the matches page agree.
  const val = n => (form[n] && form[n].value || '').trim();
  const ticked = n => form.querySelectorAll('input[name="' + n + '"]:checked').length > 0;
  const PARTS = [
    () => ticked('sectors'), () => val('keywords'), () => ticked('states') || ticked('districts'),
    () => ticked('buyers'), () => val('max_project_value'), () => val('years_in_business'),
    () => val('annual_turnover'), () => val('largest_similar_work'), () => val('bid_capacity'),
    () => val('emd_budget'), () => ticked('registrations'),
  ];
  function progress() {
    const pct = Math.round(100 * PARTS.filter(p => p()).length / PARTS.length);
    document.getElementById('pfpct').textContent = pct + '%';
    document.getElementById('pfill').className = 'p' + Math.round(pct / 5) * 5;
    return pct;
  }

  // ---- the review --------------------------------------------------------------
  const checkedLabels = n => Array.from(form.querySelectorAll('input[name="' + n + '"]:checked'))
    .map(b => b.closest('label').querySelector('span').textContent.trim());
  const inr = v => v ? 'Rs ' + Number(v).toLocaleString('en-IN') : '';
  function review() {
    const rows = [
      ['Company name', val('name'), 'basic'],
      ['States and districts', checkedLabels('states').concat(checkedLabels('districts')).join(', '), 'basic'],
      ['Contact email', val('contact_email'), 'basic'],
      ['Sectors', checkedLabels('sectors').join(', '), 'business'],
      ['Keywords', val('keywords'), 'business'],
      ['Buyers you want', checkedLabels('buyers').join(', '), 'business'],
      ['Buyers to never show', checkedLabels('exclude_buyers').join(', '), 'business'],
      ['Never show titles with', val('exclude_keywords'), 'business'],
      ['Days to prepare a bid', val('min_lead_days'), 'business'],
      ['Years in business', val('years_in_business'), 'experience'],
      ['Annual turnover', inr(val('annual_turnover')), 'experience'],
      ['Largest similar work', inr(val('largest_similar_work')), 'experience'],
      ['Work you can run at once', inr(val('bid_capacity')), 'experience'],
      ['EMD you can tie up', inr(val('emd_budget')), 'experience'],
      ['Registrations', checkedLabels('registrations').join(', '), 'documents'],
    ];
    document.getElementById('pfreview').innerHTML = rows.map(([k, v, step]) =>
      '<div><dt>' + esc(k) + '</dt><dd>' + (v ? esc(v) : '<span class=muted>Not answered</span>')
      + ' <button type=button class=pfedit data-go="' + step + '">Edit</button></dd></div>').join('');
  }

  // ---- moving between steps ------------------------------------------------------
  function show(step, focus) {
    current = step;
    const i = ORDER.indexOf(step);
    for (const s of sets) s.hidden = s.dataset.step !== step;
    for (const a of links) {
      const k = a.dataset.stepLink, here = k === step;
      a.toggleAttribute('aria-current', here);
      if (here) a.setAttribute('aria-current', 'step');
      a.classList.toggle('done', done.has(k) && !here);
    }
    back.hidden = i === 0;
    next.hidden = step === 'review';
    save.hidden = step !== 'review';
    if (step === 'review') review();
    document.getElementById('err').textContent = '';
    if (focus) {
      const first = form.querySelector('fieldset[data-step="' + step + '"]:not([hidden]) legend');
      if (first) first.scrollIntoView({block: 'nearest'});
      nav.scrollIntoView({block: 'nearest'});
    }
  }
  // A step can be left only with its required answers given: the name on the
  // first step, a sector on the second.
  function problem(step) {
    if (step === 'basic' && !val('name')) return 'Enter your company name.';
    if (step === 'business' && !ticked('sectors')) return 'Pick at least one sector you work in.';
    return '';
  }
  function go(step) {
    const from = ORDER.indexOf(current), to = ORDER.indexOf(step);
    // Going forward checks every step on the way; going back never blocks.
    for (let k = from; k < to; k++) {
      const p = problem(ORDER[k]);
      if (p) { show(ORDER[k], true); showErr(p); return; }
      done.add(ORDER[k]);
    }
    show(step, true);
  }
  next.addEventListener('click', () => go(ORDER[ORDER.indexOf(current) + 1]));
  back.addEventListener('click', () => show(ORDER[ORDER.indexOf(current) - 1], true));
  nav.addEventListener('click', e => {
    const a = e.target.closest('[data-step-link]');
    if (!a) return;
    e.preventDefault();
    go(a.dataset.stepLink);
  });
  form.addEventListener('click', e => {
    const b = e.target.closest('[data-go]');
    if (b) show(b.dataset.go, true);
  });
  // Enter in a text box moves on rather than saving half a profile.
  form.addEventListener('keydown', e => {
    if (e.key === 'Enter' && current !== 'review' && e.target.tagName === 'INPUT'
        && e.target.type !== 'checkbox') {
      e.preventDefault();
      next.click();
    }
  });
  for (const ev of ['input', 'change', 'prefilled']) form.addEventListener(ev, progress);
  // A Clear button empties a question without an input event: count after it.
  form.addEventListener('click', e => {
    if (e.target.closest('[data-action=clear]')) setTimeout(progress, 0);
  });
  progress();
  show('basic');
})();
