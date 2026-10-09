// The save button on a tender page.
//
// Optimistic: the heart fills the moment it is pressed and rolls back if the
// request fails. A save is one row in one table -- waiting on a round trip to
// acknowledge a heart makes the page feel broken on a slow connection.

const btn = document.getElementById('savebtn');

function paint(on){
  btn.classList.toggle('on', on);
  btn.dataset.saved = on ? 'true' : 'false';
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  btn.querySelector('.hearttext').textContent = on ? 'Saved' : 'Save this tender';
}

if(btn){
  btn.addEventListener('click', async () => {
    if(btn.dataset.signedIn !== 'true'){
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
      return;
    }
    const on = btn.dataset.saved === 'true';
    paint(!on);                       // optimistic
    btn.disabled = true;
    try{
      const r = await fetch('/wishlist/' + btn.dataset.tender,
                            {method: on ? 'DELETE' : 'POST'});
      if(!r.ok){
        paint(on);                    // put it back; the save did not happen
        flash(r.status === 401
          ? 'Sign in to save tenders.'
          : 'Could not save that just now. Try again.', 'warn');
      }
    }catch(e){
      paint(on);
      flash('Could not reach the server.', 'warn');
    }finally{
      btn.disabled = false;
    }
  });
}

// "Ready to fill this tender": marks it as a bid in progress on the dashboard
// (and saves it), then takes the bidder to the documents they need.
const ready = document.getElementById('readybtn');

function paintReady(on){
  ready.classList.toggle('on', on);
  ready.dataset.on = on ? 'true' : 'false';
  ready.setAttribute('aria-pressed', on ? 'true' : 'false');
  ready.querySelector('span').textContent = on ? "You're bidding on this" : 'Ready to fill this tender';
  if(on && btn) paint(true);            // participating implies saved
}

if(ready){
  ready.addEventListener('click', async () => {
    if(ready.dataset.signedIn !== 'true'){
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
      return;
    }
    const on = ready.dataset.on === 'true';
    ready.disabled = true;
    try{
      const r = await fetch('/participate/' + ready.dataset.tender, {method: on ? 'DELETE' : 'POST'});
      if(!r.ok) throw r;
      paintReady(!on);
      document.getElementById('readymsg').innerHTML = on
        ? 'Still saved, no longer listed as a bid.'
        : 'Added to your <a href="/dashboard#bidding">dashboard</a>. Here is what to prepare.';
      if(!on) document.getElementById('documents').scrollIntoView();
    }catch(e){
      flash('Could not update that just now. Try again.', 'warn');
    }finally{
      ready.disabled = false;
    }
  });
}
// The button at the foot of the documents does the same thing.
document.querySelectorAll('[data-ready-jump]').forEach(a => a.addEventListener('click', e => {
  if(!ready) return;
  e.preventDefault();
  if(ready.dataset.on !== 'true') ready.click();
  else location.href = '/dashboard#bidding';
}));
