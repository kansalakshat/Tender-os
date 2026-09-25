function go(e){
  e.preventDefault();
  const f=e.target, btn=f.querySelector('button');
  btn.disabled = true;
  fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:f.email.value,password:f.password.value})})
   .then(r=>r.json().then(d=>{
     // Only ever a path on this site: "/x" yes, "//evil.com" or "/\evil" no,
     // or ?next= would be an open redirect off a trusted sign-in page.
     const want = new URLSearchParams(location.search).get('next') || '';
     const safe = /^\/(?![\/\\])/.test(want) ? want : '';
     if(r.ok) return location.assign(safe || (d.next === '/admin' ? '/admin' : '/'));
     document.getElementById('err').textContent = d.detail;
     btn.disabled = false;
   }))
   .catch(()=>{
     document.getElementById('err').textContent =
       'Could not reach the server. Please try again.';
     btn.disabled = false;
   });
}
document.getElementById('f').addEventListener('submit', go);
