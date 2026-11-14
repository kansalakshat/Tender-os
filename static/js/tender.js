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

// The match score (up to ~3s when its cache is cold) and the related lists come
// after the notice itself, so the details never wait on them.
fetch(document.getElementById('moreSlot').dataset.src, {credentials: 'same-origin'})
  .then(function (r) { if (!r.ok) throw r; return r.json(); })
  .then(function (d) {
    document.getElementById('fitSlot').innerHTML = d.fit;
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
