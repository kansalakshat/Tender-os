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
