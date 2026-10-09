// Loaded blocking in <head>, before the stylesheet paints, so a light-theme
// visitor never sees a flash of dark. A file, not an inline block: the CSP
// allows script-src 'self' and nothing inline.
(function(){
  // Light unless the visitor chose dark: most readers are older, and a light
  // page is the easier one to read.
  var t = 'light';
  try { t = localStorage.getItem('tos-theme') || 'light'; } catch (e) {}
  document.documentElement.dataset.theme = t;
})();
