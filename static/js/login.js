function go(e){
  e.preventDefault();
  const f=e.target, btn=f.querySelector('button');
  btn.disabled = true;
  fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:f.email.value,password:f.password.value})})
   .then(r=>r.json().then(d=>{
     if(r.ok) return location.assign('/');
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
