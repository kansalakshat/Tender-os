// Loaded blocking in <head>, before the stylesheet paints, so a light-theme
// visitor never sees a flash of dark. A file, not an inline block: the CSP
// allows script-src 'self' and nothing inline.
(function(){
  var t = 'dark';
  try { t = localStorage.getItem('tos-theme') || 'dark'; } catch (e) {}
  document.documentElement.dataset.theme = t;
})();
