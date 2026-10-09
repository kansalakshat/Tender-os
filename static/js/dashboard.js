// Deleting a saved search on the dashboard (templates/dashboard.html).
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-delete-search]');
  if(!b) return;
  b.disabled = true;
  const r = await fetch('/searches/' + b.dataset.deleteSearch, {method: 'DELETE'}).catch(() => null);
  if(r && r.ok) b.closest('li').remove();
  else { b.disabled = false; flash('Could not delete that just now. Try again.'); }
});
