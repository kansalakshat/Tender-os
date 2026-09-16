// The tender page (templates/tender.html). Server values arrive as data-*
// attributes, so this file stays static and cacheable.

// The match score (up to ~3s when its cache is cold) and the related lists come
// after the notice itself, so the details never wait on them.
fetch(document.getElementById('moreSlot').dataset.src, {credentials: 'same-origin'})
  .then(function (r) { if (!r.ok) throw r; return r.json(); })
  .then(function (d) {
    document.getElementById('fitSlot').innerHTML = d.fit;
    document.getElementById('moreSlot').innerHTML = d.more;
  })
  .catch(function () {
    var note = '<p class=hint role=status>Could not load this part. Reload the page to try again.</p>';
    if (document.querySelector('#fitSlot .sk')) document.getElementById('fitSlot').innerHTML = note;
    document.getElementById('moreSlot').innerHTML = note;
  });

// The CPPP CAPTCHA box, present only on CPPP tenders with a stored link.
(function () {
  var form = document.getElementById('cpppForm');
  if (!form) return;
  var img = document.getElementById('cpppImg');
  var msg = document.getElementById('cpppMsg');
  function load() {
    var sk = document.getElementById('cpppSk');
    img.hidden = true; sk.hidden = false; msg.hidden = false;
    msg.textContent = 'Loading the CAPTCHA from CPPP, this takes a few seconds...';
    fetch(form.dataset.captcha, {credentials: 'same-origin'})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { sk.hidden = true; msg.textContent = d.error; return; }
        document.getElementById('cpppState').value = d.state;
        img.src = d.image; img.hidden = false; sk.hidden = true; msg.hidden = true;
      })
      .catch(function () {
        sk.hidden = true; msg.textContent = 'CPPP is not answering. Try New image.';
      });
  }
  document.getElementById('cpppNew').addEventListener('click', load);
  // A CAPTCHA works once: after this one goes to CPPP, fetch the next.
  form.addEventListener('submit', function () {
    setTimeout(function () { form.captcha.value = ''; load(); }, 300);
  });
  load();
})();
