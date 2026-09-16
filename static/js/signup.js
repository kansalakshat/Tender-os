// If the account is created but the profile is rejected, a retry must not try to
// register the same email again -- it would fail with "already registered" and
// strand the user on a form they cannot submit.
let accountCreated = false;
const LABEL = 'Create account and show my matches';

function go(e){
  e.preventDefault();
  const f = e.target;
  const btn = f.querySelector('button');
  const body = collectProfile(f);
  body.contact_email = f.email.value.trim() || null;
  const missing = missingRequired(body);
  if(missing) return showErr(missing);
  document.getElementById('err').textContent = '';

  btn.disabled = true;
  btn.textContent = 'Creating your account…';

  const account = accountCreated ? Promise.resolve() :
    fetch('/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:f.email.value, password:f.password.value})})
     .then(r=>r.json().then(d=>{if(!r.ok) throw d.detail; accountCreated=true;}));

  account.then(()=>saveProfile(body))
         .then(d=>location.assign('/c/'+d.id))
         .catch(err=>{ showErr(err); btn.disabled=false; btn.textContent=LABEL; });
}
document.getElementById('f').addEventListener('submit', go);
